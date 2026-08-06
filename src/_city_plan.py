# -*- coding: utf-8 -*-
"""城市 ↔ 代理商 月度规划 · 数据层（共享账本表 dealer_month_plan）

工作流闭环：
  城市页：复盘进展 → 定城市目标 → 把SO任务拆到各代理商（save_so_tasks 下发）→ 汇总各家激励（city_rollup 上收）
  代理商页：接城市下发的SO任务 → 定关键指标(激活/留存/升档/品类)+激励单价（save_plan 写回）
两页同源：城市的每个数 = 各代理商规划当月切片之和。
"""
from __future__ import annotations
import json
import sqlite3
import datetime

DDL = """CREATE TABLE IF NOT EXISTS dealer_month_plan (
  城市 TEXT, 月份 INTEGER, 代理商 TEXT,
  SO任务万 REAL DEFAULT 0,
  SO单价 REAL DEFAULT 1.0,
  指标 TEXT DEFAULT '{}',
  备注 TEXT DEFAULT '',
  更新时间 TEXT,
  PRIMARY KEY (城市, 月份, 代理商)
)"""

IND_KEYS = ['激活', '留存', '升档', '无线', '夜视王', '场景化']   # 关键指标
IND_UNIT = {'激活': '家', '留存': '家', '升档': '户', '无线': '台', '夜视王': '台', '场景化': '台'}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def ensure(conn):
    conn.execute(DDL)
    conn.commit()


def city_list(conn, year=2026):
    ensure(conn)
    rows = conn.execute(
        "SELECT 出货客户城市, COUNT(*) n FROM product_flow_v WHERE 上线年月>=? AND 出货客户城市 IS NOT NULL AND 出货客户城市!='' "
        "GROUP BY 出货客户城市 HAVING n>=200 ORDER BY n DESC", (f'{year}-01',)).fetchall()
    return [r[0] for r in rows]


def city_dealers(conn, city, year=2026):
    """该城市的代理商（出货客户城市=city），按SO台数倒序。"""
    ensure(conn)
    rows = conn.execute(
        "SELECT 出库客户名称, COUNT(*) n FROM product_flow_v WHERE 出货客户城市 LIKE ? AND 上线年月>=? "
        "GROUP BY 出库客户名称 HAVING n>=50 ORDER BY n DESC", (f'%{city}%', f'{year}-01')).fetchall()
    return [r[0] for r in rows]


def get_plan(conn, city, month, dealer):
    ensure(conn)
    r = conn.execute("SELECT SO任务万, SO单价, 指标, 备注 FROM dealer_month_plan WHERE 城市=? AND 月份=? AND 代理商=?",
                     (city, int(month), dealer)).fetchone()
    if not r:
        return None
    return {'so_task': r[0], 'so_price': r[1], 'indicators': json.loads(r[2] or '{}'), 'note': r[3] or ''}


def save_plan(conn, city, month, dealer, so_task, so_price, indicators, note=''):
    ensure(conn)
    conn.execute(
        "INSERT INTO dealer_month_plan (城市,月份,代理商,SO任务万,SO单价,指标,备注,更新时间) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(城市,月份,代理商) DO UPDATE SET "
        "SO任务万=excluded.SO任务万, SO单价=excluded.SO单价, 指标=excluded.指标, 备注=excluded.备注, 更新时间=excluded.更新时间",
        (city, int(month), dealer, float(so_task), float(so_price),
         json.dumps(indicators, ensure_ascii=False), note, _now()))
    conn.commit()


def save_so_tasks(conn, city, month, task_map):
    """城市页下发：批量写各家SO任务（保留已有指标/单价）。task_map={dealer: so万}"""
    ensure(conn)
    for dealer, so in task_map.items():
        if conn.execute("SELECT 1 FROM dealer_month_plan WHERE 城市=? AND 月份=? AND 代理商=?",
                        (city, int(month), dealer)).fetchone():
            conn.execute("UPDATE dealer_month_plan SET SO任务万=?, 更新时间=? WHERE 城市=? AND 月份=? AND 代理商=?",
                         (float(so), _now(), city, int(month), dealer))
        else:
            conn.execute("INSERT INTO dealer_month_plan (城市,月份,代理商,SO任务万,SO单价,指标,更新时间) VALUES (?,?,?,?,1.0,'{}',?)",
                         (city, int(month), dealer, float(so), _now()))
    conn.commit()


def city_rollup(conn, city, month, price=180):
    """上收：把各家规划汇总成城市方案（SO目标/台数/激励）。"""
    ensure(conn)
    rows = conn.execute("SELECT 代理商, SO任务万, SO单价, 指标, 更新时间 FROM dealer_month_plan WHERE 城市=? AND 月份=? ORDER BY SO任务万 DESC",
                        (city, int(month))).fetchall()
    out = []
    for dealer, so, sop, ind_s, upd in rows:
        so = so or 0; sop = sop or 0
        ind = json.loads(ind_s or '{}')
        units = int(round(so * 10000 / price))
        so_budget = round(units * sop)
        ind_budget = sum(round((v.get('tgt', 0) or 0) * (v.get('price', 0) or 0))
                         for v in ind.values() if v.get('on'))
        act = (ind.get('激活', {}) or {})
        out.append({'dealer': dealer, 'so': so, 'units': units, 'so_price': sop, 'so_budget': so_budget,
                    'act_tgt': act.get('tgt', 0) if act.get('on') else 0,
                    'ind_budget': ind_budget, 'total': so_budget + ind_budget, 'indicators': ind, 'updated': upd})
    return out


def city_progress(conn, city, month, year=2026):
    """复盘：城市当月+YTD 实际SO（上线城市口径）+ 当月激活（严口径）。"""
    ensure(conn)
    ym = f"{year}-{int(month):02d}"
    like = f'%{city}%'
    som = conn.execute("SELECT COALESCE(SUM(最新分销价),0)/10000.0, COUNT(*) FROM product_flow_v WHERE 上线城市 LIKE ? AND 上线年月=?",
                       (like, ym)).fetchone()
    ytd = conn.execute("SELECT COALESCE(SUM(最新分销价),0)/10000.0 FROM product_flow_v WHERE 上线城市 LIKE ? AND 上线年月>=? AND 上线年月<=?",
                       (like, f'{year}-01', ym)).fetchone()
    # 当月激活（严口径：上线月==扫码月）—— 城市级，按出货客户城市
    act = conn.execute(
        "SELECT COUNT(DISTINCT 上线客户编码) FROM install_redpack_v "
        "WHERE 出货客户城市 LIKE ? AND substr(上线时间,1,7)=? AND substr(上线时间,1,7)=substr(抽奖机会发放时间,1,7)",
        (like, ym)).fetchone()
    return {'so_month': round(som[0], 1), 'so_month_units': som[1] or 0,
            'so_ytd': round(ytd[0], 1), 'act_month': act[0] or 0}
