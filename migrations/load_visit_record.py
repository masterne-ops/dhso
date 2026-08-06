#!/usr/bin/env python3
"""拜访活动明细表 → visit_record（只代理商,过滤大华;整月全量替换）。

跑动换源后(见 load_smb_visit.py):大华分销经理跑动走 SMB 表,拜访活动明细表里的
大华行(打卡人所属公司为空)**不再入库**,只入代理商行(打卡公司非空)。
更新方式=整月全量替换:--months 指定月份,DELETE 该月代理商行 + INSERT 该月全量,
单事务。这是周更 visit_record 代理商侧的标准入口。

用法:
  python load_visit_record.py --file /tmp/拜访.xlsx --months 2026-06:2026-06           # dry-run
  python load_visit_record.py --file /tmp/拜访.xlsx --months 2026-06:2026-06 --apply    # 正式(整月全量替换)
"""
import sqlite3, sys, math, argparse
from datetime import datetime
import pandas as pd

DB = '/opt/so-data-analytics/db/product_flow.db'


def to_cell(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def is_blank(c):
    return c is None or (isinstance(c, float) and math.isnan(c))


def find_header(rows, must_have, scan=8):
    for i, row in enumerate(rows[:scan]):
        cells = set(str(c).strip() for c in row if not is_blank(c))
        if all(k in cells for k in must_have):
            return i
    raise ValueError(f"找不到表头(需含 {must_have})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--months', required=True, help='更新的拜访月份范围 LO:HI,如 2026-06:2026-06(整月全量替换该月代理商)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    LO, HI = a.months.split(':')
    conn = sqlite3.connect(DB)

    rows = pd.read_excel(a.file, header=None).values.tolist()
    h = find_header(rows, ['活动编号'])
    header = [str(c).strip() if not is_blank(c) else None for c in rows[h]]
    data = rows[h + 1:]
    dbc = [r[1] for r in conn.execute('PRAGMA table_info(visit_record)')]
    cm = [(i, c) for i, c in enumerate(header) if c and c in dbc]   # name 对齐
    comp_idx = header.index('打卡人所属公司') if '打卡人所属公司' in header else None
    ti_idx = header.index('拜访时间') if '拜访时间' in header else None
    print(f"表头第{h+1}行 | 数据 {len(data)} 行 | 列对齐 {len(cm)} 列")

    batch, n_dahua, n_out = [], 0, 0
    for r in data:
        if all(is_blank(c) for c in r):
            continue
        # 过滤大华(打卡人所属公司为空)——大华走 SMB,不从这里入
        if comp_idx is not None:
            comp = to_cell(r[comp_idx]) if comp_idx < len(r) else None
            if comp is None or str(comp).strip() == '':
                n_dahua += 1
                continue
        # 只取拜访月份落在 [LO,HI] 的行(整月全量替换该月代理商)
        if ti_idx is not None:
            tv = to_cell(r[ti_idx]) if ti_idx < len(r) else None
            ym = str(tv)[:7] if tv is not None else ''
            if not (LO <= ym <= HI):
                n_out += 1
                continue
        batch.append(tuple(to_cell(r[i]) if i < len(r) else None for i, _ in cm))
    print(f"代理商行命中[{LO}~{HI}]: {len(batch)} | 过滤大华 {n_dahua} | 范围外 {n_out}")

    allc = [c for _, c in cm]
    csql = ', '.join(f'"{c}"' for c in allc)
    ph = ', '.join(['?'] * len(allc))
    before = conn.execute("SELECT COUNT(*) FROM visit_record WHERE COALESCE(打卡人所属公司,'')!='' "
                          "AND substr(拜访时间,1,7) BETWEEN ? AND ?", (LO, HI)).fetchone()[0]
    print(f"现有代理商[{LO}~{HI}] {before} 行 → {'删除' if a.apply else '将删'} + 插 {len(batch)}")
    if not a.apply:
        print("[DRY-RUN] 加 --apply(单事务,整月全量替换)。")
        conn.close()
        return
    conn.execute('BEGIN')
    try:
        conn.execute("DELETE FROM visit_record WHERE COALESCE(打卡人所属公司,'')!='' "
                     "AND substr(拜访时间,1,7) BETWEEN ? AND ?", (LO, HI))
        conn.executemany(f'INSERT INTO visit_record ({csql}) VALUES ({ph})', batch)
        conn.commit()
        print(f"✅ [{LO}~{HI}] 删旧代理商 {before} + 插 {len(batch)}")
    except Exception as e:
        conn.rollback()
        print(f"❌ 出错,已整体回滚: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
