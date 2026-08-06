#!/usr/bin/env python3
"""批量导入 4 个 Excel → 4 张 DB 表(merge 模式)

用户上传 2026-05-16 数据:
  - 大华人员拜访明细表        → visit_record       (PK 活动编号)
  - 服务商签约明细表          → provider_contract  (PK 客户编码,双行表头)
  - 安装红包明细清单表        → install_redpack    (PK 产品序列号,87 列含 11 个新字段)
  - FX601 设备激活明细表      → product_flow       (PK ID,62 列完全匹配)

merge 模式:按主键 INSERT OR REPLACE(新增追加,重复覆盖)
新列自动 ALTER ADD COLUMN
"""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
from _loaders import DB_PATH


# ──────────────────────────────────────────
# 工具
# ──────────────────────────────────────────

def sanitize_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """pandas 扩展类型 → SQLite 基础类型;同时去重列名"""
    df = df.copy()
    if len(df.columns) != len(set(df.columns)):
        seen = {}
        new_cols = []
        for c in df.columns:
            if c in seen:
                seen[c] += 1
                new_cols.append(f"{c}.dup{seen[c]}")
            else:
                seen[c] = 0
                new_cols.append(c)
        df.columns = new_cols
    for col in df.columns:
        s = df[col]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        if pd.api.types.is_extension_array_dtype(s.dtype):
            df[col] = s.astype(object).where(s.notna(), None)
    return df


def clean_columns(cols) -> list[str]:
    """列名清洗:去空白 / 换行 / 重复"""
    cleaned = (
        pd.Series(cols).astype(str).str.strip()
        .str.replace(r'[\r\n\t]+', ' ', regex=True)
        .str.replace(r'\s+', ' ', regex=True)
        .tolist()
    )
    # 去重
    seen = {}
    out = []
    for c in cleaned:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}.{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out


def merge_excel_to_db(file_path: Path, table: str, pk: str,
                     header_rows: int = 1) -> dict:
    """
    通用 Excel → SQLite merge

    Args:
        file_path: Excel 路径
        table: 目标 DB 表名
        pk: 主键列名(必须存在于 Excel)
        header_rows: 1=单行表头(默认);2=双行表头(用 header=1 跳第一行分组)
    """
    # 读 Excel
    if header_rows == 2:
        df = pd.read_excel(file_path, header=1)
    else:
        df = pd.read_excel(file_path)

    df.columns = clean_columns(df.columns)
    print(f'  Excel: {len(df):,} 行 × {len(df.columns)} 列')

    if pk not in df.columns:
        raise ValueError(f'Excel 缺主键列 [{pk}]。实际列: {list(df.columns)[:10]}...')

    # 主键清洗 + 文件内去重
    df[pk] = df[pk].astype(str).str.strip()
    before = len(df)
    df = df[df[pk].notna() & (df[pk] != '') & (df[pk] != 'nan')]
    df = df.drop_duplicates(subset=pk, keep='last').reset_index(drop=True)
    dup = before - len(df)
    if dup > 0:
        print(f'  文件内主键去重 / 空值剔除: {dup} 行')

    # 进库
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 检查表存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cur.fetchone() is None:
        raise ValueError(f'目标表 {table} 不存在')

    # ALTER 加新列
    cur.execute(f'PRAGMA table_info("{table}")')
    db_cols = [r[1] for r in cur.fetchall()]
    new_cols = [c for c in df.columns if c not in db_cols]
    for c in new_cols:
        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')
    if new_cols:
        print(f'  ➕ ALTER 加新列 {len(new_cols)} 个: {new_cols[:5]}{"..." if len(new_cols)>5 else ""}')

    # inserted vs updated 统计
    existing_pks = set(
        r[0] for r in cur.execute(f'SELECT "{pk}" FROM "{table}"').fetchall()
    )
    df[pk] = df[pk].astype(str)
    new_rows = df[~df[pk].isin(existing_pks)]
    update_rows = df[df[pk].isin(existing_pks)]
    inserted = len(new_rows)
    updated = len(update_rows)

    # merge:INSERT OR REPLACE
    df_clean = sanitize_for_sqlite(df)
    df_clean.to_sql('_tmp_import', conn, if_exists='replace', index=False)
    cols_sql = ', '.join(f'"{c}"' for c in df_clean.columns)
    cur.execute(
        f'INSERT OR REPLACE INTO "{table}" ({cols_sql}) '
        f'SELECT {cols_sql} FROM _tmp_import'
    )
    cur.execute('DROP TABLE _tmp_import')

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    conn.close()

    return {
        'file': file_path.name,
        'table': table,
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'new_cols': new_cols,
    }


# ──────────────────────────────────────────
# 任务清单
# ──────────────────────────────────────────

TASKS = [
    {
        'file': '/Users/cory/Downloads/大华人员拜访明细表_20260516.xlsx',
        'table': 'visit_record',
        'pk': '活动编号',
        'header_rows': 1,
    },
    {
        'file': '/Users/cory/Downloads/服务商签约明细表_20260516.xlsx',
        'table': 'provider_contract',
        'pk': '客户编码',
        'header_rows': 2,  # 双行表头
    },
    {
        'file': '/Users/cory/Downloads/安装红包明细清单表_20260501-0516.xlsx',
        'table': 'install_redpack',
        'pk': '产品序列号',
        'header_rows': 1,
    },
    {
        'file': '/Users/cory/Downloads/FX601-设备激活明细表_2026051-516.xlsx',
        'table': 'product_flow',
        'pk': 'ID',
        'header_rows': 1,
    },
]


def main():
    print(f'═══ Batch Import @ {pd.Timestamp.now()} ═══')
    print(f'DB: {DB_PATH}')
    print()

    results = []
    for t in TASKS:
        print(f'── [{t["table"]}] {Path(t["file"]).name}')
        r = merge_excel_to_db(
            Path(t['file']),
            table=t['table'],
            pk=t['pk'],
            header_rows=t['header_rows'],
        )
        print(f'  ✅ 新增 {r["inserted"]:,} / 更新 {r["updated"]:,} / 库内总数 {r["total"]:,}')
        print()
        results.append(r)

    # 重建视图(install_redpack_v / product_flow_v 等)
    print('── 重建视图(_views.ensure_views)')
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
        from _views import ensure_views
        conn = sqlite3.connect(DB_PATH)
        v = ensure_views(conn)
        conn.close()
        print(f'  ✅ 视图: {v}')
    except Exception as e:
        print(f'  ⚠️  视图重建失败: {e}')

    print()
    print('═══ 汇总 ═══')
    for r in results:
        print(f'  {r["table"]:20} 新增 {r["inserted"]:>6,} / 更新 {r["updated"]:>6,} / '
              f'总 {r["total"]:>8,} | 新列 {len(r["new_cols"])}')


if __name__ == '__main__':
    main()
