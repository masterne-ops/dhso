#!/usr/bin/env python3
"""导出规范化后的客户分类表 — 全省 1 份 + 11 地市各 1 份

字段:
  客户编码, 客户名称, 城市, 区县, 原分类, 规范分类, 客户所有者(大华业务员),
  上级一级代理商, 年安防采购量_万, 服务商等级, 是否激活, 累计上线金额, 渠道客户类型
"""
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from pathlib import Path

DB = '/opt/so-data-analytics/db/product_flow.db'
OUT_DIR = Path('/tmp/客户分类待确认_20260525')
OUT_DIR.mkdir(exist_ok=True)

SQL = """
SELECT
  pc.客户编码,
  pc.客户名称,
  pc.客户城市 AS 城市,
  pc.客户区县 AS 区县,
  pc.客户分类 AS 原分类,
  pc.客户分类_规范 AS 规范分类,
  pc.客户所有者 AS 大华业务员,
  pc.上级客户名称 AS 上级一级代理商,
  ROUND(pc.\"年安防采购量 （年采购视频类产品金额）\", 1) AS 年安防采购量_万,
  pc.服务商等级,
  pc.是否激活,
  ROUND(pc.累计上线金额 / 10000, 2) AS 累计上线金额_万,
  pc.渠道客户类型,
  pc.签约日期
  FROM provider_contract_v pc
 WHERE pc.客户省区 = '浙江'
 {where}
 ORDER BY 城市, 区县, 规范分类, 客户名称
"""

# 列宽
COL_WIDTHS = {
    '客户编码': 18, '客户名称': 35, '城市': 10, '区县': 10,
    '原分类': 12, '规范分类': 12, '大华业务员': 12, '上级一级代理商': 30,
    '年安防采购量_万': 14, '服务商等级': 10, '是否激活': 8,
    '累计上线金额_万': 14, '渠道客户类型': 14, '签约日期': 18,
}


def export_sheet(ws, rows, header):
    """填一个 worksheet,带样式"""
    # 标题样式
    hdr_font = Font(bold=True, color='FFFFFF', size=11)
    hdr_fill = PatternFill('solid', fgColor='4472C4')
    align_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    border = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC'),
    )

    # 表头
    for c, h in enumerate(header, 1):
        cell = ws.cell(1, c, h)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = align_center
        cell.border = border
        col_letter = openpyxl.utils.get_column_letter(c)
        ws.column_dimensions[col_letter].width = COL_WIDTHS.get(h, 15)
    ws.row_dimensions[1].height = 32

    # 数据 + 标红"规范分类"列(便于地市负责人核对)
    norm_col_idx = header.index('规范分类') + 1
    for r, row in enumerate(rows, 2):
        for c, v in enumerate(row, 1):
            cell = ws.cell(r, c, v)
            cell.border = border
            if c == norm_col_idx:
                cell.font = Font(bold=True, color='C00000')
                cell.alignment = Alignment(horizontal='center')

    ws.freeze_panes = 'A2'   # 冻结表头


def main():
    conn = sqlite3.connect(DB)
    cursor = conn.cursor()

    # 11 地市清单
    cities = [r[0] for r in cursor.execute("""
        SELECT DISTINCT 客户城市 FROM provider_contract
         WHERE 客户省区 = '浙江' AND 客户城市 IS NOT NULL AND 客户城市 != ''
         ORDER BY 客户城市
    """).fetchall()]

    print(f'城市列表:{cities}')

    # ─── 1. 全省总表 ──
    print(f'\n══ 导出全省总表 ══')
    cursor.execute(SQL.format(where=''))
    rows = cursor.fetchall()
    header = [d[0] for d in cursor.description]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '全省服务商分类'
    export_sheet(ws, rows, header)

    # 加汇总 sheet
    summary = wb.create_sheet('🗺️ 地市×分类 网格')
    cursor.execute("""
        SELECT 客户城市,
          SUM(CASE WHEN 客户分类_规范 = '安装商' THEN 1 ELSE 0 END) AS 安装商,
          SUM(CASE WHEN 客户分类_规范 = '中小工程商' THEN 1 ELSE 0 END) AS 中小工程商,
          SUM(CASE WHEN 客户分类_规范 = '夫妻门店' THEN 1 ELSE 0 END) AS 夫妻门店,
          SUM(CASE WHEN 客户分类_规范 = '批发门店' THEN 1 ELSE 0 END) AS 批发门店,
          SUM(CASE WHEN 客户分类_规范 = '未分类' THEN 1 ELSE 0 END) AS 未分类,
          COUNT(*) AS 合计
          FROM provider_contract_v WHERE 客户省区 = '浙江' AND 客户城市 IS NOT NULL AND 客户城市 != ''
         GROUP BY 1 ORDER BY 合计 DESC
    """)
    s_rows = cursor.fetchall()
    s_hdr = [d[0] for d in cursor.description]
    for c, h in enumerate(s_hdr, 1):
        cell = summary.cell(1, c, h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='4472C4')
        cell.alignment = Alignment(horizontal='center')
        summary.column_dimensions[openpyxl.utils.get_column_letter(c)].width = 14
    for r, row in enumerate(s_rows, 2):
        for c, v in enumerate(row, 1):
            summary.cell(r, c, v)
    summary.freeze_panes = 'A2'

    fp = OUT_DIR / '浙江省服务商分类_全省_待确认.xlsx'
    wb.save(fp)
    print(f'  ✓ {fp}  ({len(rows)} 行)')

    # ─── 2. 11 地市各一份 ──
    print(f'\n══ 导出 11 地市分表 ══')
    for city in cities:
        cursor.execute(SQL.format(where="AND pc.客户城市 = ?"), (city,))
        rows = cursor.fetchall()
        if not rows:
            continue

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f'{city}服务商分类'
        export_sheet(ws, rows, header)

        fp = OUT_DIR / f'浙江省服务商分类_{city}_待确认.xlsx'
        wb.save(fp)
        print(f'  ✓ {city}: {len(rows)} 行 → {fp.name}')

    conn.close()
    print(f'\n📦 导出完成,共 1 + {len(cities)} 个文件,在 {OUT_DIR}')


if __name__ == '__main__':
    main()
