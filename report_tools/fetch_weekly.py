#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报取数 tool（本地跑）。scp 周报引擎到生产 → ssh 跑 → 取回 JSON。

用法：
    python3 fetch_weekly.py prov --week-end 2026-06-06            # 全省周报
    python3 fetch_weekly.py city --week-end 2026-06-06 --city 杭州市  # 单地市周报

本周 = week-end 当天往前 7 天（含当天）。本地 DB 空，数据走生产。
首次调用 scp 同步引擎；同轮后续可加 --no-sync。
"""
import argparse
import base64
import subprocess
import sys
from pathlib import Path

SSH = "root@121.196.152.24"
PROD_DB = "/opt/so-data-analytics/db/product_flow.db"
PROD_SRC = "/opt/so-data-analytics/src"
PROD_TMP = "/tmp"
VENV_PY = "/opt/so-data-analytics/venv/bin/python3"

HERE = Path(__file__).resolve().parent
SRCDIR = HERE.parent / "src"
# 周报引擎 + 其依赖（_monthly_province_report 提供 ZHEJIANG_CITIES/_pct）
FILES = [SRCDIR / "_weekly_prov.py", SRCDIR / "_weekly_city.py",
         SRCDIR / "_monthly_province_report.py"]


def sync():
    for f in FILES:
        subprocess.run(["scp", "-q", str(f), f"{SSH}:{PROD_TMP}/"], check=True)


def run(scope, week_end, n, city):
    if scope == 'prov':
        entry = f"{PROD_TMP}/_weekly_prov.py --week-end {week_end} --n {n}"
    else:
        b64 = base64.b64encode(city.encode("utf-8")).decode("ascii")
        # _weekly_city 用 --city 中文；经 ssh 传中文有风险，改在生产侧用 python -c 解码
        entry = (f"{PROD_TMP}/_weekly_city.py --week-end {week_end} --n {n} "
                 f"--city $(echo {b64} | base64 -d)")
    cmd = f"PYTHONPATH={PROD_TMP}:{PROD_SRC} {VENV_PY} {entry} --db {PROD_DB}"
    r = subprocess.run(["ssh", SSH, cmd], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "ssh/runner 失败\n")
        sys.exit(r.returncode)
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scope', choices=['prov', 'city'])
    ap.add_argument('--week-end', default='2026-06-06')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--city', default=None, help='city 必填，中文城市名')
    ap.add_argument('--no-sync', action='store_true')
    args = ap.parse_args()
    if args.scope == 'city' and not args.city:
        sys.stderr.write("city 需要 --city\n")
        sys.exit(2)
    if not args.no_sync:
        sync()
    sys.stdout.write(run(args.scope, args.week_end, args.n, args.city))


if __name__ == '__main__':
    main()
