#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全省月报取数 runner —— 在【生产服务器】侧运行（import 引擎），按 module 分模块输出 JSON。

本文件不直接由人调用：本地的 fetch.py 会把它 + 引擎 _monthly_province_report.py
scp 到生产 /tmp，再 ssh 用生产 venv python 调用本文件。

module:
  trend  → 模块① 11 地市 SO 趋势（不下钻，快）
  drill  → 模块② 单个地市下钻（需 --city-b64）
  focus  → 模块③ 三大专项趋势 + 下降专项下钻

城市名经 base64 传入（--city-b64），避免中文过 ssh 命令行的转义/locale 问题。
"""
import argparse
import base64
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _monthly_province_report import (  # noqa: E402
    query_city_so_trend, drilldown_city, query_focus_trends, _last_n_months,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module', required=True,
                    choices=['trend', 'drill', 'focus', 'city-report'])
    ap.add_argument('--end', default='2026-05')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--city-b64', default=None, help='base64(utf-8 城市名)')
    ap.add_argument('--min-wan', type=float, default=5.0)
    ap.add_argument('--db', required=True)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        if args.module == 'trend':
            out = query_city_so_trend(conn, args.end, args.n)
        elif args.module == 'drill':
            if not args.city_b64:
                print(json.dumps({'error': 'drill 需要 --city-b64'}), file=sys.stderr)
                sys.exit(2)
            city = base64.b64decode(args.city_b64).decode('utf-8')
            months = _last_n_months(args.end, args.n)
            out = drilldown_city(conn, city, months, min_dealer_wan=args.min_wan)
        elif args.module == 'city-report':
            # 单地市完整报告（一次返回 ①趋势②区县③下钻④专项）
            if not args.city_b64:
                print(json.dumps({'error': 'city-report 需要 --city-b64'}), file=sys.stderr)
                sys.exit(2)
            from _city_report import gather_city_report
            city = base64.b64decode(args.city_b64).decode('utf-8')
            out = gather_city_report(city, args.end, args.n,
                                     db_path=args.db, min_dealer_wan=args.min_wan)
        else:  # focus
            out = query_focus_trends(conn, args.end, args.n)
        sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
