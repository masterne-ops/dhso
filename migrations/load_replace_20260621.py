#!/usr/bin/env python3
"""6/21 REPLACE 类表入库 — 红包配额/发放 + 2 个新表(签约客户月度进度 / 会议分析)。

都是月度快照/状态会变 → 按主键 INSERT OR REPLACE 刷新。
自动探测表头 + 动态建表(新表首次自动建)+ Unnamed 合并列重命名为 _colN。
默认 dry-run,--apply 才写库。
"""
import sqlite3, sys, math
import pandas as pd
from datetime import datetime

DB = '/opt/so-data-analytics/db/product_flow.db'
DIR = '/tmp/621数据'
APPLY = '--apply' in sys.argv
NUM_HINT = ['金额', '总额', '客户数', '使用率', '解锁率', '红包值', '上线金额', '签约金额',
            '达成', '完成率', '率', '费用', '台数', '公司数', '账号数', '评价数量']


def cell(v):
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def find_header(rows, must_have, scan=8):
    for i, row in enumerate(rows[:scan]):
        cells = set(str(c).strip() for c in row
                    if c is not None and not (isinstance(c, float) and math.isnan(c)))
        if all(k in cells for k in must_have):
            return i
    raise ValueError(f"找不到表头(需含 {must_have})")


def load(conn, xlsx, must_have, pk_cols, table):
    raw = pd.read_excel(f'{DIR}/{xlsx}', header=None).values.tolist()
    h = find_header(raw, must_have)
    df = pd.read_excel(f'{DIR}/{xlsx}', header=h).dropna(how='all')
    cols = []
    for i, c in enumerate(df.columns):
        cs = str(c).strip()
        cols.append(f'_col{i}' if (cs.startswith('Unnamed') or cs in ('', 'nan')) else cs)
    df.columns = cols
    for pk in pk_cols:
        df = df[df[pk].notna()]
    n0 = len(df)
    df = df.drop_duplicates(subset=pk_cols, keep='last')
    dup = n0 - len(df)

    coldefs = [f'"{c}" {"REAL" if any(hh in c for hh in NUM_HINT) else "TEXT"}' for c in cols]
    pk = ', '.join(f'"{c}"' for c in pk_cols)
    ddl = f'CREATE TABLE IF NOT EXISTS {table} (\n  ' + ',\n  '.join(coldefs) + f',\n  PRIMARY KEY ({pk})\n)'
    print(f"\n【{table}】表头第 {h+1} 行 | {len(df)} 行 × {len(cols)} 列 | 主键({', '.join(pk_cols)})"
          + (f" | 同主键去重 {dup}" if dup else ""))
    if not APPLY:
        print("  列:", cols)
        return len(df)
    conn.execute(ddl)
    colsql = ', '.join(f'"{c}"' for c in cols)
    ph = ', '.join(['?'] * len(cols))
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
        load(conn, '分销经理发红包配额情况表_20260621_1528.xlsx', ['分销经理', '工号'], ['时间', '工号'], 'dahua_redpack_quota')
        load(conn, '分销经理发红包发放明细表_20260621_1528.xlsx', ['发放人姓名', '卡券编码'], ['卡券编码'], 'dahua_redpack_grant')
        load(conn, '11_签约客户月度进度表_20260621_1524.xlsx', ['客户编码', '签约金额'], ['客户编码'], 'signed_customer_monthly')
        load(conn, '会议分析_20260621_1527.xlsx', ['ADSPID', '活动名称'], ['ADSPID'], 'meeting_analysis')
        print('\n✅ 完成' if APPLY else '\n[DRY-RUN] 核对无误后加 --apply')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
