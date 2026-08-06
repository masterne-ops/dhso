#!/usr/bin/env python3
"""单独补 provider_contract 漏掉的「年安防采购量」列

Excel 列名: '年安防采购量\n（年采购视频类产品金额）' (含换行)
DB   列名: '年安防采购量 （年采购视频类产品金额）' (含空格)

策略: 按 客户编码 反查更新这一列
"""
import sqlite3
import openpyxl

DB = '/opt/so-data-analytics/db/product_flow.db'
FP = '/tmp/服务商签约明细表_0524.xlsx'

DB_COL = '年安防采购量 （年采购视频类产品金额）'   # 中间空格
EXCEL_COL_HINT = '年安防采购量'                  # excel 列名以此开头

wb = openpyxl.load_workbook(FP, read_only=True, data_only=True)
ws = wb['sheet1']
rows = list(ws.iter_rows(values_only=True))
hdr = rows[1]   # 表头在第 2 行

# 找列下标
code_idx = None
amt_idx = None
for i, h in enumerate(hdr):
    if h is None: continue
    s = str(h).strip()
    if s == '客户编码': code_idx = i
    elif s.startswith(EXCEL_COL_HINT): amt_idx = i

if code_idx is None or amt_idx is None:
    raise SystemExit(f'未找到列: code_idx={code_idx} amt_idx={amt_idx}')

print(f'Excel 客户编码列 idx={code_idx}, 年安防采购量列 idx={amt_idx}')
print(f'Excel 该列 header: {repr(hdr[amt_idx])}')

# 读所有 (客户编码, 金额)
pairs = []
for r in rows[2:]:
    if not r: continue
    code = r[code_idx]
    amt = r[amt_idx] if amt_idx < len(r) else None
    if code and amt is not None:
        pairs.append((amt, code))

print(f'要更新 {len(pairs)} 行(有金额的)')

conn = sqlite3.connect(DB)
# 反引号包裹列名(带空格 + 中文括号)
sql = f'UPDATE provider_contract SET "{DB_COL}" = ? WHERE 客户编码 = ?'
cur = conn.executemany(sql, pairs)
conn.commit()
updated = conn.execute(f'SELECT COUNT(*) FROM provider_contract WHERE "{DB_COL}" IS NOT NULL').fetchone()[0]
print(f'✅ 表中该列非空行数: {updated}')
conn.close()
