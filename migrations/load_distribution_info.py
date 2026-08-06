#!/usr/bin/env python3
"""铺货明细 distribution_info 表入库

源:Ditribution_Info.xlsx (15436 行 × 45 列)
主键:(序列号, 铺货单号) 复合
入库:INSERT OR IGNORE (允许同一产品多次铺货)
"""
import sqlite3
import openpyxl
from datetime import datetime
from pathlib import Path

DB = '/opt/so-data-analytics/db/product_flow.db'
FP = '/tmp/Ditribution_Info.xlsx'

# Excel 列号 → DB 列名(45 列,重命名重复客户字段)
COLS = [
    (0,  '序列号',                'TEXT'),
    (1,  '物料号',                'TEXT'),
    (2,  '产品名称',              'TEXT'),
    (3,  '外部型号',              'TEXT'),
    (4,  '内部型号',              'TEXT'),
    (5,  '规格',                  'TEXT'),
    (6,  '产品线一级',            'TEXT'),
    (7,  '产品线二级',            'TEXT'),
    (8,  '产品系列',              'TEXT'),
    (9,  '产品子系列',            'TEXT'),
    (10, '销售类型一级',          'TEXT'),
    (11, '分销产品标签',          'TEXT'),
    (12, '出库时间',              'TEXT'),
    (13, '订单时间',              'TEXT'),
    (14, '设备上线时间',          'TEXT'),
    (15, '设备上线数据来源',      'TEXT'),
    (16, '合同编号',              'TEXT'),
    (17, '订单编号',              'TEXT'),
    (18, '产品下单价',            'REAL'),
    (19, '产品分销价',            'REAL'),
    (20, '分销合同类型',          'TEXT'),
    (21, '产品标签',              'TEXT'),
    (22, '扫码添加_手动添加',     'TEXT'),
    (23, '扫码时GPS省',           'TEXT'),
    (24, '扫码时GPS市',           'TEXT'),
    (25, '扫码时GPS区',           'TEXT'),
    (26, '出货客户账号_姓名',     'TEXT'),
    (27, '客户编码_上级',         'TEXT'),
    (28, '客户名称_上级',         'TEXT'),
    (29, '所在省份_上级',         'TEXT'),
    (30, '所在城市_上级',         'TEXT'),
    (31, '所在区县_上级',         'TEXT'),
    (32, '客户所有者',            'TEXT'),  # 大华业务员
    (33, '客户编码_下级',         'TEXT'),
    (34, '客户名称_下级',         'TEXT'),
    (35, '客户所在省份_下级',     'TEXT'),
    (36, '客户所在城市_下级',     'TEXT'),
    (37, '客户所在区县_下级',     'TEXT'),
    (38, '铺货单号',              'TEXT'),
    (39, '提交铺货时间',          'TEXT'),
    (40, '铺货单状态',            'TEXT'),
    (41, '下级客户确认收货时间',  'TEXT'),
    (42, '完成时间',              'TEXT'),
    (43, '设备清除状态',          'TEXT'),
    (44, '退回时间',              'TEXT'),
]


def main():
    conn = sqlite3.connect(DB)

    # 建表
    col_ddl = ',\n    '.join(f'"{name}" {ty}' for _, name, ty in COLS)
    ddl = f"""
        CREATE TABLE IF NOT EXISTS distribution_info (
            数据时点 TEXT NOT NULL,
            {col_ddl},
            PRIMARY KEY (序列号, 铺货单号)
        );
        CREATE INDEX IF NOT EXISTS idx_di_upper_dealer ON distribution_info(客户名称_上级);
        CREATE INDEX IF NOT EXISTS idx_di_lower_provider ON distribution_info(客户名称_下级);
        CREATE INDEX IF NOT EXISTS idx_di_owner ON distribution_info(客户所有者);
        CREATE INDEX IF NOT EXISTS idx_di_material ON distribution_info(物料号);
        CREATE INDEX IF NOT EXISTS idx_di_status ON distribution_info(铺货单状态);
        CREATE INDEX IF NOT EXISTS idx_di_submit ON distribution_info(提交铺货时间);
    """
    conn.executescript(ddl)

    # 入库
    print(f'读取 {FP}')
    wb = openpyxl.load_workbook(FP, read_only=True, data_only=True)
    ws = wb['sheet1']
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    print(f'Excel: {len(rows)} 行')

    col_names = ['数据时点'] + [c[1] for c in COLS]
    col_sql = ', '.join(f'"{c}"' for c in col_names)
    ph = ', '.join(['?'] * len(col_names))
    insert_sql = f'INSERT OR IGNORE INTO distribution_info ({col_sql}) VALUES ({ph})'

    period = '2026-05-22'  # 数据时点(出库时间最大值)
    batch = []
    skipped_no_pk = 0
    for r in rows:
        if not r: continue
        sn = r[0] if 0 < len(r) else None
        order = r[38] if 38 < len(r) else None
        if not sn or not order:
            skipped_no_pk += 1
            continue
        vals = [period]
        for i, _, _ in COLS:
            v = r[i] if i < len(r) else None
            if isinstance(v, datetime):
                v = v.strftime('%Y-%m-%d %H:%M:%S')
            vals.append(v)
        batch.append(tuple(vals))

    print(f'有效行(去掉 PK 缺失): {len(batch)},跳过 {skipped_no_pk}')
    cur = conn.executemany(insert_sql, batch)
    conn.commit()
    inserted = cur.rowcount
    skipped_dup = len(batch) - inserted
    total = conn.execute('SELECT COUNT(*) FROM distribution_info').fetchone()[0]
    print(f'✅ 入库 {inserted} 行 / 跳过(主键重复) {skipped_dup} / 表中总数 {total}')

    # ─── 校验 ──
    print('\n═══ 校验 ═══')
    # 状态分布
    print('\n铺货单状态分布:')
    for s, n in conn.execute('SELECT 铺货单状态, COUNT(*) FROM distribution_info GROUP BY 1 ORDER BY 2 DESC').fetchall():
        print(f'  {s}: {n:,}')

    # 月度提交分布
    print('\n月度提交铺货量:')
    for m, n in conn.execute("""
        SELECT substr(提交铺货时间, 1, 7) AS 月, COUNT(*)
          FROM distribution_info WHERE 提交铺货时间 IS NOT NULL
         GROUP BY 1 ORDER BY 1
    """).fetchall():
        print(f'  {m}: {n:,}')

    # 三大专项铺货数量
    print('\n三大专项铺货统计(铺货单序列号去重):')
    rows = conn.execute("""
        SELECT fc.专项, COUNT(DISTINCT di.序列号) AS 铺货序列号, COUNT(DISTINCT di.客户名称_下级) AS 下级服务商数
          FROM distribution_info di
          JOIN product_focus fc ON fc.物料号 = di.物料号
         GROUP BY fc.专项 ORDER BY 铺货序列号 DESC
    """).fetchall()
    for r in rows:
        print(f'  {r[0]}: {r[1]:,} 台 / {r[2]} 家下级服务商')

    # 上线率(铺货后是否在 install_redpack 出现)
    print('\n铺货 → 上线率:')
    rows = conn.execute("""
        SELECT
          (SELECT COUNT(DISTINCT 序列号) FROM distribution_info) AS 铺货总数,
          (SELECT COUNT(DISTINCT di.序列号) FROM distribution_info di
            JOIN install_redpack ir ON ir.产品序列号 = di.序列号) AS 已上线数
    """).fetchone()
    print(f'  铺货总序列号: {rows[0]:,}')
    print(f'  已在 install_redpack 上线: {rows[1]:,}')
    if rows[0]:
        print(f'  上线率: {rows[1]/rows[0]*100:.2f}%')

    # 11 地市铺货分布(按下级服务商地市)
    print('\n11 地市铺货分布(按下级服务商):')
    for r in conn.execute("""
        SELECT 客户所在城市_下级, COUNT(DISTINCT 序列号) AS 台数, COUNT(DISTINCT 客户名称_下级) AS 服务商数
          FROM distribution_info
         WHERE 客户所在城市_下级 IS NOT NULL
         GROUP BY 1 ORDER BY 2 DESC LIMIT 12
    """).fetchall():
        print(f'  {r[0]}: {r[1]:,} 台 / {r[2]} 家')

    conn.close()


if __name__ == '__main__':
    main()
