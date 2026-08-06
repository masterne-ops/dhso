#!/usr/bin/env python3
"""新建浙江省 SMB 销售组织架构表 sales_org_structure，灌 33 人。

已应用 2026-06 组织调整:
  ① 薛玉飞管 杭州+宁波、石伟只管 台州(宁波从石伟转薛玉飞)
  ② 黄在洪 / 张志超 上级改薛玉飞、负责宁波
  ③ 王康 从产品组转分销组，上级薛玉飞、杭州分销经理

主键 = 姓名(SMB 团队 33 人唯一)。默认 dry-run，--apply 才建表写库。
名字已统一为 salesperson_scope 版本:林雪怡 / 章旱雨(与业务员实际数据一致)。
"""
import sqlite3, sys
from datetime import datetime
from collections import defaultdict

DB = '/opt/so-data-analytics/db/product_flow.db'
APPLY = '--apply' in sys.argv
SMB, ZX, FX, CP, DS = ('浙江省SMB', '浙江省SMB专项组', '浙江省SMB分销组',
                       '浙江省SMB产品组', '浙江省SMB电商组')

# (姓名, 部门, 职位, 上级, 负责片区)
DATA = [
    ('罗方东', SMB, 'SMB总监', '', '全省'),
    ('谭教典', ZX, 'SMB专项主管', '罗方东', '全省'),
    ('薛玉飞', FX, 'SMB分销主管', '罗方东', '杭州、宁波'),
    ('徐建来', FX, 'SMB分销主管', '罗方东', '金华、丽水、衢州'),
    ('石伟', FX, 'SMB分销主管', '罗方东', '台州'),
    ('叶增强', FX, 'SMB分销主管', '罗方东', '温州'),
    ('黄明君', FX, 'SMB分销主管', '罗方东', '湖州、嘉兴、绍兴'),
    ('张豪楠', FX, '分销经理', '罗方东', '舟山'),
    ('金磊', CP, 'SMB产品主管', '罗方东', '全省'),
    ('林初鼎', CP, '产品经理', '金磊', '全省'),
    ('金科佚', CP, '产品经理', '金磊', '全省'),
    ('王康', FX, '分销经理', '薛玉飞', '杭州'),
    ('时兵橙', FX, '分销经理', '薛玉飞', '杭州'),
    ('章旱雨', FX, '分销经理', '薛玉飞', '杭州'),
    ('祝嘉蔚', FX, '分销经理', '薛玉飞', '杭州'),
    ('徐潮', FX, '分销经理', '薛玉飞', '杭州'),
    ('孙鲁江', FX, '分销经理', '薛玉飞', '杭州'),
    ('黄伟', FX, '分销经理', '薛玉飞', '杭州'),
    ('黄在洪', FX, '分销经理', '薛玉飞', '宁波'),
    ('张志超', FX, '分销经理', '薛玉飞', '宁波'),
    ('朱震文', FX, '分销经理', '徐建来', '衢州'),
    ('徐志鹏', FX, '分销经理', '徐建来', '金华'),
    ('戴增辉', FX, '分销经理', '徐建来', '金华'),
    ('汤固', FX, '分销经理', '徐建来', '丽水'),
    ('杨添龙', FX, '分销经理', '石伟', '台州'),
    ('管鹏伟', FX, '分销经理', '叶增强', '温州'),
    ('林雪怡', FX, '分销经理', '叶增强', '温州'),
    ('陈海鑫', FX, '分销经理', '黄明君', '湖州'),
    ('陈琪能', FX, '分销经理', '黄明君', '绍兴'),
    ('杨校', FX, '分销经理', '黄明君', '绍兴'),
    ('董波波', ZX, '专项经理', '谭教典', '全省'),
    ('孙亿达', ZX, '专项经理', '谭教典', '全省'),
    ('姜雨薇', DS, '电商经理', '罗方东', '全省'),
]


def main():
    print(f"{'='*14} {'建表 + 写库 --apply' if APPLY else 'DRY-RUN(仅预览层级)'} {'='*14}")
    print(f"共 {len(DATA)} 人\n")
    sub = defaultdict(list)
    for n, d, pos, sup, area in DATA:
        sub[sup].append((n, pos, area))

    def tree(sup, ind):
        for n, pos, area in sub.get(sup, []):
            print("   " + "  " * ind + f"{n} · {pos} · {area}")
            tree(n, ind + 1)
    tree('', 0)

    if not APPLY:
        print("\n[DRY-RUN] 核对层级无误后加 --apply 建表写库")
        return
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS sales_org_structure (
        姓名 TEXT PRIMARY KEY, 部门 TEXT, 职位 TEXT, 上级 TEXT, 负责片区 TEXT, 更新时间 TEXT)""")
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn.executemany(
        "INSERT OR REPLACE INTO sales_org_structure (姓名,部门,职位,上级,负责片区,更新时间) "
        "VALUES (?,?,?,?,?,?)", [(*r, now) for r in DATA])
    conn.commit()
    tot = conn.execute("SELECT COUNT(*) FROM sales_org_structure").fetchone()[0]
    print(f"\n✅ 已写入 sales_org_structure，共 {tot} 人")
    conn.close()


if __name__ == '__main__':
    main()
