#!/usr/bin/env python3
"""建表 product_line_balance — 代理商产品均衡管理(产品经理工作板块)。

源: RP10-SMB产品线分析-客户 Excel(双表头:行2=产品线分组,行3=字段;数据=本年累计到月口径)。
列名 = 字段行经 _smart_import._norm 规范化(换行→空格),与智能导入 name 对齐完全一致。
更新方式: snapshot 按数据时点(=导出日期)全量替换——同时点重导替换、不同时点累积,
留住季度末快照以支撑「产品均衡返点」的季度结算 + 年度补齐核算。

政策口径(2026): CCTV销售额占比≥70% 且 (数通占比>3% 或 配套占比>10%) → 产品均衡 1% 返点;
季度结算、年度补齐(年末整体达标可补前期未达成季度)。

用法: python create_product_balance.py            # 建表(IF NOT EXISTS,幂等)
"""
import sqlite3

DB = '/opt/so-data-analytics/db/product_flow.db'

TEXT_COLS = ['客户编码', '客户名称', '军团', '客户省区', '客户城市', '客户区县', '是否签约']

ALL_COLS = [
    '客户编码', '客户名称', '军团', '客户省区', '客户城市', '客户区县', '是否签约',
    '签约金额', '实销金额', '实发数量', '同期实销 （到月）', '实销同比 （到月）',
    '订单金额 （返利前）', '订单数量',
    'CCTV实销', 'CCTV占比', '同期CCTV实销', 'CCTV实销同比 （到月）',
    '网线实销', '网线占比', '同期网线实销', '网线实销同比 （到月）', '网线遗留订单',
    '硬盘（含SSD）实销', '硬盘（含SSD）占比', '同期硬盘（含SSD）实销',
    '硬盘（含SSD）实销同比 （到月）', '硬盘（含SSD)遗留订单', '网线及硬盘（SSD） 占比',
    '配套实销', '配套占比', '同期配套实销', '配套实销同比 （到月）',
    '数通实销', '数通实销占比',
    '夜视王订单台数', '无线订单台数', '场景化订单台数', '丰视安防订单台数',
    '自研产品实销', 'LED项目经销实销', '综合专项（充电桩+广播）实销',
    '出入口实销', '专项数通实销', '电商专款实销',
]


def main():
    conn = sqlite3.connect(DB)
    defs = ['数据时点 TEXT NOT NULL']
    for c in ALL_COLS:
        ty = 'TEXT' if c in TEXT_COLS else 'REAL'
        defs.append(f'"{c}" {ty}')
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS product_line_balance (
            {','.join(defs)},
            PRIMARY KEY (数据时点, 客户编码)
        );
        CREATE INDEX IF NOT EXISTS idx_plb_city ON product_line_balance(客户城市);
        CREATE INDEX IF NOT EXISTS idx_plb_name ON product_line_balance(客户名称);
    """)
    conn.commit()
    n = len([r for r in conn.execute('PRAGMA table_info(product_line_balance)')])
    print(f'✅ product_line_balance 建表完成({n} 列)')
    conn.close()


if __name__ == '__main__':
    main()
