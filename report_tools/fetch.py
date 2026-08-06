#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全省月报取数 tool（在【本地】跑）。

职责：把引擎 + runner scp 到生产 → ssh 用生产 venv 跑 runner → 取回 JSON 到 stdout。
本地 DB 是空的，数据只在生产，所以取数必须走生产。

用法（被 SKILL 指引的 Claude Code 调用）：
    python3 fetch.py trend --end 2026-05                  # 模块① 趋势（含🔴判定）
    python3 fetch.py drill --end 2026-05 --city 金华市     # 模块② 单地市下钻
    python3 fetch.py focus --end 2026-05                  # 模块③ 三专项

输出：纯 JSON 到 stdout（LLM 直接读，或 > 文件）。
首次调用会 scp 同步引擎；同一轮后续调用可加 --no-sync 略过 scp 提速。
"""
import argparse
import base64
import subprocess
import sys
from pathlib import Path

SSH = "root@121.196.152.24"
PROD_DB = "/opt/so-data-analytics/db/product_flow.db"
PROD_SRC = "/opt/so-data-analytics/src"   # 引擎依赖 _metrics_rfm 等,在生产 src
PROD_TMP = "/tmp"
VENV_PY = "/opt/so-data-analytics/venv/bin/python3"

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "src" / "_monthly_province_report.py"
CITY_ENGINE = HERE.parent / "src" / "_city_report.py"   # 地市月报引擎
RUNNER = HERE / "_report_runner.py"


def sync():
    for f in (ENGINE, CITY_ENGINE, RUNNER):
        subprocess.run(["scp", "-q", str(f), f"{SSH}:{PROD_TMP}/"], check=True)


def run(module, end, n, city, min_wan):
    cmd = (
        f"PYTHONPATH={PROD_TMP}:{PROD_SRC} {VENV_PY} {PROD_TMP}/_report_runner.py "
        f"--module {module} --end {end} --n {n} "
        f"--db {PROD_DB} --min-wan {min_wan}"
    )
    if city:
        b64 = base64.b64encode(city.encode("utf-8")).decode("ascii")
        cmd += f" --city-b64 {b64}"
    r = subprocess.run(["ssh", SSH, cmd], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "ssh/runner 失败\n")
        sys.exit(r.returncode)
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module', choices=['trend', 'drill', 'focus', 'city-report'])
    ap.add_argument('--end', default='2026-05')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--city', default=None, help='drill / city-report 必填，中文城市名')
    ap.add_argument('--min-wan', type=float, default=5.0)
    ap.add_argument('--no-sync', action='store_true', help='略过 scp（引擎已同步时提速）')
    args = ap.parse_args()

    if args.module in ('drill', 'city-report') and not args.city:
        sys.stderr.write(f"{args.module} 模块需要 --city\n")
        sys.exit(2)
    if not args.no_sync:
        sync()
    sys.stdout.write(run(args.module, args.end, args.n, args.city, args.min_wan))


if __name__ == '__main__':
    main()
