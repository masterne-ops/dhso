#!/usr/bin/env python3
"""代理商 SI 月度快照 dealer_si_snapshot 入库

源: 签约客户明细表_20260525.xlsx (51 行 × 67 列)
主键: (数据时点, 客户编码)
策略: 按数据时点快照,多月共存; 全量替换同时点数据
"""
import sqlite3
import openpyxl
import re
from datetime import datetime
from pathlib import Path

DB = '/opt/so-data-analytics/db/product_flow.db'
FP = '/tmp/签约客户明细表_20260525.xlsx'
PERIOD = '2026-05-25'


def to_cell(v):
    if v is None: return None
    if isinstance(v, datetime): return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, str): return v.strip()
    return v


def main():
    wb = openpyxl.load_workbook(FP, read_only=True, data_only=True)
    ws = wb['sheet1']
    rows = list(ws.iter_rows(values_only=True))
    r1, r2 = rows[0], rows[1]

    # 字段名规范化(R2 子字段, 去换行)
    cols = []
    for j, v2 in enumerate(r2):
        if v2:
            name = str(v2).replace('\n', '').strip()
            cols.append((j, name))

    print(f'Excel: {len(rows)-2} 行数据 / {len(cols)} 列')

    # 数值列(用于 REAL 类型推断)
    real_cols = {
        '签约金额', '累计任务', '累计业绩达成（返利前）', '累计业绩达成（计任务）',
        '签约任务完成率', '同期业绩达成', '业绩同比', '累计业绩达成（视频类）',
        '累计订单金额(返利前)', '累计订单金额(计销售)', '累计订单金额(计任务)',
        '本月任务', '本月业绩达成（返利前）', '本月业绩达成（计任务）', '本月任务完成率',
        '同期达成(月)', '业绩同比(月)', '本月业绩达成（视频类）',
        '本月订单金额(返利前)', '本月订单金额(计销售)', '本月订单金额(计任务)',
        '同期订单金额(返利前)', '本月订单预测', '本月订单预测完成率',
        '本月业绩预测金额', '本月业绩预测完成率', '返利订单',
        '遗留合计', '商务遗留-订单', '生产遗留-订单', '发运未签收金额',
        '应收权利余额', '到期应收权利余额', '本月收款金额', '铺底资金', '已使用铺底资金',
        '被扫码台数', '被扫码金额', '本年上线台数', '本年上线金额',
        '本年本省上线台数', '本年本省上线销售金额', '本月本省上线台数', '本月本省上线销售金额',
        '上年12月业绩达成（计任务）', '下游服务商客户数', '下游全量服务商客户数',
    }

    # 建表
    conn = sqlite3.connect(DB)
    col_ddl = ['数据时点 TEXT NOT NULL']
    for _, name in cols:
        ty = 'REAL' if name in real_cols else 'TEXT'
        col_ddl.append(f'"{name}" {ty}')
    pk_extra = ['客户编码'] if '客户编码' in [n for _, n in cols] else []
    pk = '数据时点, "客户编码"' if pk_extra else '数据时点'
    ddl = f"""
        CREATE TABLE IF NOT EXISTS dealer_si_snapshot (
            {','.join(col_ddl)},
            PRIMARY KEY ({pk})
        );
        CREATE INDEX IF NOT EXISTS idx_dss_city ON dealer_si_snapshot("客户所在城市");
        CREATE INDEX IF NOT EXISTS idx_dss_owner ON dealer_si_snapshot("客户所有者");
        CREATE INDEX IF NOT EXISTS idx_dss_signed ON dealer_si_snapshot("是否新签");
    """
    conn.executescript(ddl)

    # 清旧时点 + 入新
    conn.execute("DELETE FROM dealer_si_snapshot WHERE 数据时点 = ?", (PERIOD,))
    col_names = ['数据时点'] + [n for _, n in cols]
    col_sql = ', '.join(f'"{c}"' for c in col_names)
    ph = ', '.join(['?'] * len(col_names))
    insert_sql = f'INSERT OR IGNORE INTO dealer_si_snapshot ({col_sql}) VALUES ({ph})'

    batch = []
    for r in rows[2:]:
        if not r or not any(r): continue
        code = r[2] if 2 < len(r) else None
        if not code: continue
        vals = [PERIOD] + [to_cell(r[i]) for i, _ in cols]
        batch.append(tuple(vals))

    cur = conn.executemany(insert_sql, batch)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM dealer_si_snapshot WHERE 数据时点 = ?", (PERIOD,)).fetchone()[0]
    print(f'✅ 入库 {cur.rowcount} 行 / 表中该时点 {n} 行')

    # ─── 校验 ──
    print('\n═══ 校验 ═══')

    # 全省累计任务/业绩
    total = conn.execute(f"""
        SELECT
          SUM(\"累计任务\") AS 任务总,
          SUM(\"累计业绩达成（计任务）\") AS 达成总,
          COUNT(*) AS 总数,
          SUM(CASE WHEN \"累计业绩达成（计任务）\" >= \"累计任务\" THEN 1 ELSE 0 END) AS 达标数
          FROM dealer_si_snapshot WHERE 数据时点 = ?
    """, (PERIOD,)).fetchone()
    task_tot, done_tot, total_n, done_n = total
    print(f'  全省累计任务: {task_tot:.1f} 万')
    print(f'  全省累计达成: {done_tot:.1f} 万')
    print(f'  完成率: {done_tot/task_tot*100:.2f}%' if task_tot else '')
    print(f'  达标家数: {done_n}/{total_n}')

    # 跟 city_snapshot_p3 对比
    p3_target = conn.execute("""
        SELECT SUM(CAST(\"城市签约目标\" AS REAL)) FROM city_snapshot_p3 WHERE 数据时点 = '2026-04'
    """).fetchone()[0]
    print(f'\n  city_snapshot_p3 全省签约目标加和(4 月): {p3_target:.1f} 万')
    print(f'  dealer_si_snapshot 全省累计任务(5/25): {task_tot:.1f} 万')
    print(f'  差异: {task_tot - p3_target:+.1f} 万({(task_tot-p3_target)/p3_target*100:+.2f}%)')

    # 是否新签分布
    print('\n  是否新签分布:')
    for s, c in conn.execute(f"""
        SELECT \"是否新签\", COUNT(*) FROM dealer_si_snapshot WHERE 数据时点 = ? GROUP BY 1
    """, (PERIOD,)).fetchall():
        print(f'    {s}: {c}')

    # 城市分布
    print('\n  城市分布(代理商家数 + 累计任务):')
    for r in conn.execute(f"""
        SELECT \"客户所在城市\" AS 城市, COUNT(*) AS 家数,
               ROUND(SUM(\"累计任务\"), 1) AS 任务_万,
               ROUND(SUM(\"累计业绩达成（计任务）\"), 1) AS 达成_万
          FROM dealer_si_snapshot WHERE 数据时点 = ?
         GROUP BY 1 ORDER BY 任务_万 DESC
    """, (PERIOD,)).fetchall():
        print(f'    {r[0]}: {r[1]} 家 / 任务 {r[2]} / 达成 {r[3]}')

    # 大华业务员归属
    print('\n  业务员归属:')
    for r in conn.execute(f"""
        SELECT \"客户所有者\" AS 业务员, COUNT(*) AS 代理商数,
               ROUND(SUM(\"累计任务\"), 1) AS 任务_万
          FROM dealer_si_snapshot WHERE 数据时点 = ?
         GROUP BY 1 ORDER BY 任务_万 DESC LIMIT 25
    """, (PERIOD,)).fetchall():
        print(f'    {r[0]}: {r[1]} 家 / 任务 {r[2]} 万')

    conn.close()


if __name__ == '__main__':
    main()
