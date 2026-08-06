#!/usr/bin/env python3
"""大华业务员「转化红包」入库 — 配额情况 + 发放明细 2 表。

活动 2025-06 起。卡券状态 / 配额数值会更新 → INSERT OR REPLACE 按主键刷新:
  - dahua_redpack_quota  配额(业务员×月)  主键 (时间, 工号)
  - dahua_redpack_grant  发放明细(每张券)  主键 卡券编码

自动探测表头(跳标题废行);默认 dry-run,--apply 才写库。
用法:
  python load_dahua_redpack.py            # dry-run
  python load_dahua_redpack.py --apply
"""
import sqlite3, sys, math
import pandas as pd
from datetime import datetime

DB = '/opt/so-data-analytics/db/product_flow.db'
DIR = '/tmp/红包数据'
APPLY = '--apply' in sys.argv

QUOTA_XLSX = f'{DIR}/分销经理发红包配额情况表.xlsx'
GRANT_XLSX = f'{DIR}/分销经理发红包发放明细表.xlsx'

# 列名含这些子串 → REAL，其余 TEXT
NUM_HINT = ['金额', '总额', '客户数', '使用率', '解锁率', '红包值', '上线金额']


def _blank(c):
    return c is None or (isinstance(c, float) and math.isnan(c))


def find_header(rows, must_have, scan=6):
    for i, row in enumerate(rows[:scan]):
        cells = set(str(c).strip() for c in row if not _blank(c))
        if all(k in cells for k in must_have):
            return i
    raise ValueError(f"找不到表头(需含 {must_have})")


def cell(v):
    try:
        if pd.isna(v):   # 覆盖 None / NaN / NaT
            return None
    except (ValueError, TypeError):
        pass
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def load_table(conn, xlsx, must_have, pk_cols, table):
    rows = pd.read_excel(xlsx, header=None).values.tolist()
    h = find_header(rows, must_have)
    cols = [str(c).strip() for c in rows[h] if not _blank(c)]
    ncol = len(cols)
    df = pd.read_excel(xlsx, header=h).iloc[:, :ncol]
    df.columns = cols
    df = df.dropna(how='all')
    # 主键非空 + 去重(同主键保留最后一条 = 最新状态)
    for pk in pk_cols:
        df = df[df[pk].notna()]
    n_before = len(df)
    df = df.drop_duplicates(subset=pk_cols, keep='last')
    dup = n_before - len(df)

    coldefs = [f'"{c}" {"REAL" if any(hh in c for hh in NUM_HINT) else "TEXT"}' for c in cols]
    pk = ', '.join(f'"{c}"' for c in pk_cols)
    ddl = f'CREATE TABLE IF NOT EXISTS {table} (\n  ' + ',\n  '.join(coldefs) + f',\n  PRIMARY KEY ({pk})\n)'

    print(f"\n【{table}】表头第 {h+1} 行 | {len(df)} 行 × {ncol} 列 | 主键({', '.join(pk_cols)})"
          + (f" | 同主键去重 {dup} 行" if dup else ""))
    if not APPLY:
        print("  列:", cols)
        return len(df)

    conn.execute(ddl)
    colsql = ', '.join(f'"{c}"' for c in cols)
    ph = ', '.join(['?'] * ncol)
    data = [tuple(cell(v) for v in r) for r in df.itertuples(index=False, name=None)]
    conn.executemany(f'INSERT OR REPLACE INTO {table} ({colsql}) VALUES ({ph})', data)
    conn.commit()
    tot = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f"  ✅ INSERT OR REPLACE {len(data)} 行 | 表总 {tot} 行")
    return len(df)


def main():
    conn = sqlite3.connect(DB)
    print('=' * 18, '正式入库 --apply' if APPLY else 'DRY-RUN(不写库)', '=' * 18)
    try:
        load_table(conn, QUOTA_XLSX, ['分销经理', '工号'], ['时间', '工号'], 'dahua_redpack_quota')
        load_table(conn, GRANT_XLSX, ['发放人姓名', '卡券编码'], ['卡券编码'], 'dahua_redpack_grant')
        print('\n✅ 完成' if APPLY else '\n[DRY-RUN] 核对无误后加 --apply')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
