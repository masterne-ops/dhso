#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理商作战方案 CLI —— 在服务器上运行（DB 所在处）。随 deploy.sh 同步到 src/。
用法：
  python3 /opt/so-data-analytics/src/gen_battle_plan.py \
      --dealer 杭州万仞科技有限公司 --month 6 --so-year 700 --si-year 1000 --chal-pool 1.0 --out /tmp/plan.html
"""
import argparse, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_DB = '/opt/so-data-analytics/db/product_flow.db'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dealer', required=True)
    ap.add_argument('--month', type=int, required=True)
    ap.add_argument('--so-year', type=float, default=700)
    ap.add_argument('--si-year', type=float, default=1000)
    ap.add_argument('--price', type=float, default=180)
    ap.add_argument('--chal-pool', type=float, default=1.0)
    ap.add_argument('--chal-mult', type=float, default=1.5)
    ap.add_argument('--chal-factor', type=float, default=1.083)
    ap.add_argument('--v2', type=int, default=150)
    ap.add_argument('--v3', type=int, default=50)
    ap.add_argument('--v4', type=int, default=20)
    ap.add_argument('--loyal', type=int, default=15)
    ap.add_argument('--baseload', type=float, default=50)
    ap.add_argument('--wireless', type=float, default=0.10)
    ap.add_argument('--nightking', type=float, default=0.06)
    ap.add_argument('--scenario', type=float, default=0.08)
    ap.add_argument('--db', default=DEF_DB)
    ap.add_argument('--out', default='/tmp/作战方案.html')
    a = ap.parse_args()

    sys.path.insert(0, HERE)
    import _battle_plan as bp
    conn = sqlite3.connect(a.db); conn.row_factory = sqlite3.Row
    plan = bp.build_plan(
        conn, a.dealer, a.month, year=2026,
        so_year_wan=a.so_year, si_year_wan=a.si_year, price=a.price,
        so_challenge_factor=a.chal_factor, challenge_mult=a.chal_mult, challenge_pool_wan=a.chal_pool,
        tier_year={'V2': a.v2, 'V3': a.v3, 'V4': a.v4}, loyal_year=a.loyal, baseload_year_wan=a.baseload,
        cat_year={'无线': a.wireless, '夜视王': a.nightking, '场景化': a.scenario})
    conn.close()
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(bp.render_html(plan))
    d = plan['diag']
    print(f"OK · {a.dealer} {a.month}月 → {a.out}")
    print(f"  现状 V2/V3/V4={d['tiers']['V2']}/{d['tiers']['V3']}/{d['tiers']['V4']} 月月动销={d['loyal_recent']} 激活近月={d['act_recent']}")
    print(f"  {a.month}月 SO基准 {plan['incentive']['so_base_wan']}万 / 挑战 {plan['incentive']['so_chal_wan']}万")


if __name__ == '__main__':
    main()
