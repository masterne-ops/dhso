"""KPI 目标 + 月度节奏导入器（V3 周报校验依赖）

把 2 份 Excel 导入到 SQLite，给 V3 沙箱 AI 校验周报时查目标用：

  - `data/SO全年任务.xlsx`              → kpi_targets   (97 行：城市/区县全年 SO 目标)
  - `data/2026年SO和服务商进度条.xlsx`  → kpi_rhythm   (6 指标 × 12 月 = 72 行：每月占全年比例)

业务用法（V3 沙箱里 AI 就能这么算）：

  某区县某月应达成 = SELECT SO目标_万 FROM kpi_targets WHERE 城市='金华市' AND 区县='义乌市' AND 年度=2026
                   × SELECT 占比 FROM kpi_rhythm
                          WHERE 指标='省区SO进度条（返利前）' AND 月份=5 AND 年度=2026

CLI 用法：
  python src/_kpi_loader.py                      # 默认导入 data/ 下的两个 Excel 到本地 db
  python src/_kpi_loader.py --year 2027 \
         --targets data/2027目标.xlsx \
         --rhythm  data/2027进度条.xlsx
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd


DEFAULT_DB = Path(__file__).parent.parent / 'db' / 'product_flow.db'
DEFAULT_DATA = Path(__file__).parent.parent / 'data'


CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS kpi_targets (
    城市       TEXT NOT NULL,
    区县       TEXT NOT NULL,
    年度       INTEGER NOT NULL,
    SO目标_万  REAL,
    PRIMARY KEY (城市, 区县, 年度)
);

CREATE INDEX IF NOT EXISTS idx_kpi_targets_city ON kpi_targets(城市);
CREATE INDEX IF NOT EXISTS idx_kpi_targets_year ON kpi_targets(年度);

CREATE TABLE IF NOT EXISTS kpi_rhythm (
    指标      TEXT NOT NULL,
    适用范围  TEXT,
    类型      TEXT,
    年度      INTEGER NOT NULL,
    月份      INTEGER NOT NULL,
    占比      REAL,
    PRIMARY KEY (指标, 适用范围, 年度, 月份)
);

CREATE INDEX IF NOT EXISTS idx_kpi_rhythm_year_month ON kpi_rhythm(年度, 月份);


-- 业务员责任范围（来自「责任区县管理.xlsx」）
-- 一行 = 一个业务员负责的「市/区县/代理商」三元组
-- 同业务员可负责多区县和多代理商；同区县/代理商可有多个业务员（穿插覆盖）
CREATE TABLE IF NOT EXISTS salesperson_scope (
    省        TEXT,
    市        TEXT,
    区县      TEXT,
    代理商    TEXT,
    业务员    TEXT,
    PRIMARY KEY (省, 市, 区县, 代理商, 业务员)
);

CREATE INDEX IF NOT EXISTS idx_sp_scope_city     ON salesperson_scope(市);
CREATE INDEX IF NOT EXISTS idx_sp_scope_district ON salesperson_scope(区县);
CREATE INDEX IF NOT EXISTS idx_sp_scope_dealer   ON salesperson_scope(代理商);
CREATE INDEX IF NOT EXISTS idx_sp_scope_person   ON salesperson_scope(业务员);
"""


def ensure_tables(conn: sqlite3.Connection):
    conn.executescript(CREATE_TABLES)
    conn.commit()


def import_kpi_targets(xlsx_path: Path, conn: sqlite3.Connection, year: int) -> int:
    """把 SO 全年任务表导入 kpi_targets。年度从命令行传"""
    df = pd.read_excel(xlsx_path)
    # 列顺序应该是：城市 / 区县 / XX年SO目标（万）
    if len(df.columns) < 3:
        raise ValueError(f"Excel 列数 {len(df.columns)} < 3，期望 [城市, 区县, 目标]")

    df = df.rename(columns={
        df.columns[0]: '城市',
        df.columns[1]: '区县',
        df.columns[2]: 'SO目标_万',
    })[['城市', '区县', 'SO目标_万']]
    df['年度'] = year
    df = df[['城市', '区县', '年度', 'SO目标_万']]
    df['SO目标_万'] = pd.to_numeric(df['SO目标_万'], errors='coerce').fillna(0)

    cur = conn.cursor()
    cur.execute("DELETE FROM kpi_targets WHERE 年度 = ?", (year,))
    df.to_sql('_tmp_targets', conn, if_exists='replace', index=False)
    cur.execute("""
        INSERT INTO kpi_targets (城市, 区县, 年度, SO目标_万)
        SELECT 城市, 区县, 年度, SO目标_万 FROM _tmp_targets
    """)
    cur.execute("DROP TABLE _tmp_targets")
    conn.commit()
    return len(df)


def import_kpi_rhythm(xlsx_path: Path, conn: sqlite3.Connection, year: int) -> int:
    """把月度进度条表展开成 (指标, 月份, 占比) 长格式导入"""
    df = pd.read_excel(xlsx_path)
    # 「指标」列可能有 merge cell（空），ffill 向下填充
    if '指标' in df.columns:
        df['指标'] = df['指标'].ffill()
    else:
        raise ValueError("找不到「指标」列")

    rows = []
    for _, row in df.iterrows():
        指标 = str(row.get('指标', '')).strip()
        适用范围 = str(row.get('适用范围', '')).strip() if pd.notna(row.get('适用范围')) else ''
        类型 = str(row.get('类型', '')).strip() if pd.notna(row.get('类型')) else ''

        for month in range(1, 13):
            col = f'{month}月'
            if col not in row.index:
                continue
            ratio = row[col]
            if pd.isna(ratio):
                continue
            rows.append({
                '指标': 指标,
                '适用范围': 适用范围,
                '类型': 类型,
                '年度': year,
                '月份': month,
                '占比': float(ratio),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return 0

    cur = conn.cursor()
    cur.execute("DELETE FROM kpi_rhythm WHERE 年度 = ?", (year,))
    out.to_sql('_tmp_rhythm', conn, if_exists='replace', index=False)
    cur.execute("""
        INSERT INTO kpi_rhythm (指标, 适用范围, 类型, 年度, 月份, 占比)
        SELECT 指标, 适用范围, 类型, 年度, 月份, 占比 FROM _tmp_rhythm
    """)
    cur.execute("DROP TABLE _tmp_rhythm")
    conn.commit()
    return len(out)


def import_salesperson_scope(xlsx_path: Path, conn: sqlite3.Connection) -> int:
    """导入业务员责任范围（全量替换 — 一变就重发全表）"""
    df = pd.read_excel(xlsx_path)
    rename_map = {}
    for col in df.columns:
        c = str(col).strip()
        if c in ('省', '省份'):
            rename_map[col] = '省'
        elif c in ('市', '地市', '城市'):
            rename_map[col] = '市'
        elif c in ('区/县', '区县', '县区'):
            rename_map[col] = '区县'
        elif c in ('客户名称', '代理商', '代理商名称', '一级客户'):
            rename_map[col] = '代理商'
        elif c in ('业务员', '业务员姓名', '责任人'):
            rename_map[col] = '业务员'
    df = df.rename(columns=rename_map)

    needed = ['省', '市', '区县', '代理商', '业务员']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"缺列：{missing}（找到的列：{list(df.columns)}）")

    df = df[needed].copy()
    for c in needed:
        df[c] = df[c].fillna('').astype(str).str.strip()
    df = df[df['业务员'] != ''].reset_index(drop=True)

    cur = conn.cursor()
    cur.execute("DELETE FROM salesperson_scope")
    df.to_sql('_tmp_sp', conn, if_exists='replace', index=False)
    cur.execute("""
        INSERT OR IGNORE INTO salesperson_scope (省, 市, 区县, 代理商, 业务员)
        SELECT 省, 市, 区县, 代理商, 业务员 FROM _tmp_sp
    """)
    cur.execute("DROP TABLE _tmp_sp")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM salesperson_scope")
    return cur.fetchone()[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', default=str(DEFAULT_DB),
                   help=f'SQLite DB 路径，默认 {DEFAULT_DB}')
    p.add_argument('--year', type=int, default=2026,
                   help='这批数据是哪年的，默认 2026')
    p.add_argument('--targets', default=str(DEFAULT_DATA / 'SO全年任务.xlsx'),
                   help='全年任务 Excel 路径')
    p.add_argument('--rhythm', default=str(DEFAULT_DATA / '2026年SO和服务商进度条.xlsx'),
                   help='月度节奏 Excel 路径')
    p.add_argument('--scope', default=str(DEFAULT_DATA / '责任区县管理.xlsx'),
                   help='业务员责任范围 Excel 路径')
    args = p.parse_args()

    print(f'DB:        {args.db}')
    print(f'年度:      {args.year}')
    print(f'全年任务:   {args.targets}')
    print(f'月度节奏:   {args.rhythm}')
    print(f'业务员归属: {args.scope}')
    print()

    conn = sqlite3.connect(args.db)
    try:
        ensure_tables(conn)

        n_t = import_kpi_targets(Path(args.targets), conn, year=args.year)
        print(f'✅ kpi_targets: {n_t} 行（{args.year} 年）')

        n_r = import_kpi_rhythm(Path(args.rhythm), conn, year=args.year)
        print(f'✅ kpi_rhythm:  {n_r} 行（{args.year} 年）')

        if Path(args.scope).exists():
            n_s = import_salesperson_scope(Path(args.scope), conn)
            print(f'✅ salesperson_scope: {n_s} 行')

        # 抽样验证
        cur = conn.cursor()
        print('\n验证：金华市义乌市全年目标 + 5 月节奏 = 5 月应达成')
        cur.execute("""
            SELECT t.SO目标_万 AS 全年, r.占比 AS 五月节奏,
                   ROUND(t.SO目标_万 * r.占比, 1) AS 五月应达成
              FROM kpi_targets t
              JOIN kpi_rhythm  r ON r.年度 = t.年度
             WHERE t.城市='金华市' AND t.区县='义乌市'
               AND r.指标 LIKE '省区SO进度条%' AND r.月份=5
               AND t.年度=?
        """, (args.year,))
        row = cur.fetchone()
        if row:
            print(f'  全年 {row[0]:.0f} 万 × 五月节奏 {row[1]} = 应达成 {row[2]} 万')
        print('\n验证：金华全市 5 月应达成（合所有区县）')
        cur.execute("""
            SELECT ROUND(SUM(t.SO目标_万 * r.占比), 1) AS 五月应达成
              FROM kpi_targets t
              JOIN kpi_rhythm  r ON r.年度 = t.年度
             WHERE t.城市='金华市'
               AND r.指标 LIKE '省区SO进度条%' AND r.月份=5
               AND t.年度=?
        """, (args.year,))
        print(f'  → {cur.fetchone()[0]} 万（周报里写的 195.5 万）')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
