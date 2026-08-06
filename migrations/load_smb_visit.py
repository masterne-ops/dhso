#!/usr/bin/env python3
"""SMB客户拜访明细 → visit_record 大华部分(方案X:数据层修正,不改统计代码;整月全量替换)。

大华分销经理跑动改用 SMB 表:visit_record 大华行(打卡人所属公司为空,系统判🏢大华)
的数据源 = SMB 表。更新方式=整月全量替换:--months 指定月份,DELETE 该月大华行
+ INSERT 该月 SMB 映射(打卡公司留空),单事务、失败回滚。代理商部分(打卡公司非空)
完全不动。这样 visit_record 大华部分=SMB准确数据,18 处按 _打卡方/_是否大华 区分
大华的统计代码全部自动正确、零改动。
(注:25 年大华跑动已一次性清零,以后只按月维护 26 年起的大华数据。)

SMB(sheet「客户拜访活动明细」) → visit_record 列映射见 COLMAP;活动编码=活动编号天然唯一。

用法:
  python load_smb_visit.py --file /tmp/smb.xlsx --months 2026-06:2026-06           # dry-run
  python load_smb_visit.py --file /tmp/smb.xlsx --months 2026-06:2026-06 --apply   # 正式(整月全量替换)
"""
import sqlite3, sys, argparse
from datetime import datetime
import pandas as pd

DB = '/opt/so-data-analytics/db/product_flow.db'
SHEET = '客户拜访活动明细'

# SMB 列名 → visit_record 列名
COLMAP = {
    '活动编码': '活动编号', '拜访类型': '拜访类型', '活动创建时间': '活动创建时间',
    '拜访时间': '拜访时间', '客户编码': '客户编码', '客户名称': '拜访客户',
    '省份': '拜访客户省份', '城市': '拜访客户城市', '区县': '拜访客户区县',
    '行动业务员': '打卡人姓名', '行动业务员工号': '打卡人工号/账号',
    '行动业务员省份': '打卡人所属省份', '行动业务员部门': '打卡人所属部门',
    '渠道客户类型': '渠道客户类型', '分销商认证': '分销商认证',
    '客户所有者': '客户所有者', '客户所有者工号': '所有者工号',
    '拜访对象': '拜访对象', '拜访对象职位': '拜访对象职位', '拜访目的': '拜访目的',
    '达成结果': '达成结果', '后期计划': '后期计划',
    '打卡异常类型': '打卡异常类型', '打卡异常描述': '打卡异常描述',
    '打卡地点': '打卡地点', '距离偏差_米': '距离偏离_米',
    '场景类型': '场景类型', '附件类型': '附件类型',
}
# 注意:不映射「打卡人所属公司」→ 入库后为 NULL → visit_record_v 派生 _是否大华=1(大华)


def cell(v):
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--months', required=True, help='更新的拜访月份范围 LO:HI,如 2026-06:2026-06(整月全量替换该月大华)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    LO, HI = a.months.split(':')
    conn = sqlite3.connect(DB)

    df = pd.read_excel(a.file, sheet_name=SHEET, header=0)
    dbc = [r[1] for r in conn.execute('PRAGMA table_info(visit_record)')]
    use = [(smb, vr) for smb, vr in COLMAP.items() if smb in df.columns and vr in dbc]
    print(f"SMB「{SHEET}」{len(df)} 行 | 映射 {len(use)}/{len(COLMAP)} 列 → visit_record")
    miss = [smb for smb in COLMAP if smb not in df.columns]
    if miss:
        print("  ⚠️ SMB 缺列(跳过):", miss)
    # 只取拜访月份落在 [LO,HI] 的行(整月全量替换该月大华)
    df['_ym'] = pd.to_datetime(df['拜访时间'], errors='coerce').dt.strftime('%Y-%m')
    df_m = df[(df['_ym'] >= LO) & (df['_ym'] <= HI)]
    print(f"  命中月份[{LO}~{HI}]: {len(df_m)} 行 (范围外跳过 {len(df) - len(df_m)})")

    vr_cols = [vr for _, vr in use]
    batch = [tuple(cell(row[smb]) for smb, _ in use) for _, row in df_m.iterrows()]

    before_dh = conn.execute(
        "SELECT COUNT(*) FROM visit_record WHERE COALESCE(打卡人所属公司,'')='' "
        "AND substr(拜访时间,1,7) BETWEEN ? AND ?", (LO, HI)).fetchone()[0]
    print(f"\n现有大华[{LO}~{HI}] {before_dh} 行 → {'删除' if a.apply else '将删'} + 插 SMB {len(batch)} 行")

    if not a.apply:
        print("\n[DRY-RUN] 未写库。核对后加 --apply(单事务)。")
        conn.close()
        return

    csql = ', '.join(f'"{c}"' for c in vr_cols)
    ph = ', '.join(['?'] * len(vr_cols))
    conn.execute('BEGIN')
    try:
        conn.execute("DELETE FROM visit_record WHERE COALESCE(打卡人所属公司,'')='' "
                     "AND substr(拜访时间,1,7) BETWEEN ? AND ?", (LO, HI))
        conn.executemany(f'INSERT INTO visit_record ({csql}) VALUES ({ph})', batch)
        conn.commit()
        print(f"✅ [{LO}~{HI}] 删旧大华 {before_dh} + 插 SMB {len(batch)}")
    except Exception as e:
        conn.rollback()
        print(f"❌ 出错,已整体回滚: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
