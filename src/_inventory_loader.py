"""库存进销存 数据访问层 — 基于 inventory_snapshot

口径:严口径(是否在库=1)。季度时序动态读取 inventory_snapshot。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


def _conn():
    return sqlite3.connect(str(DB_PATH))


def list_quarters() -> list:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT 盘库季度 FROM inventory_snapshot ORDER BY 盘库季度 DESC"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ═══════════════════════ Tab 1: 库存总览 ═══════════════════════

def get_overview(quarter: str) -> dict:
    conn = _conn()
    try:
        kpi = conn.execute("""
            SELECT COUNT(*) AS 在库台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 在库货值_万,
                   COUNT(DISTINCT 盘库客户名称) AS 代理商数,
                   COUNT(DISTINCT 产品料号) AS SKU数
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1
        """, (quarter,)).fetchone()

        # 分专项(JOIN product_focus)
        focus = pd.read_sql("""
            SELECT fc.专项,
                   COUNT(*) AS 在库台数,
                   ROUND(SUM(inv.产品现有分销价)/10000, 1) AS 货值_万
              FROM inventory_snapshot inv
              JOIN product_focus fc ON fc.物料号 = inv.产品料号
             WHERE inv.盘库季度 = ? AND inv.是否在库 = 1
             GROUP BY fc.专项 ORDER BY 货值_万 DESC
        """, conn, params=(quarter,))

        # 分品类(产品二级)
        cat = pd.read_sql("""
            SELECT COALESCE(产品二级, '未分类') AS 品类,
                   COUNT(*) AS 在库台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 货值_万
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1
             GROUP BY 1 ORDER BY 货值_万 DESC LIMIT 15
        """, conn, params=(quarter,))

        # 分代理商
        dealer = pd.read_sql("""
            SELECT 盘库客户名称 AS 代理商,
                   COUNT(*) AS 在库台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 货值_万,
                   SUM(CASE WHEN 库龄天数 > 365 THEN 1 ELSE 0 END) AS 呆滞台数
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1
             GROUP BY 1 ORDER BY 货值_万 DESC
        """, conn, params=(quarter,))

        # 库龄分布
        age = pd.read_sql("""
            SELECT CASE
                     WHEN 库龄天数 IS NULL THEN '无出库时间'
                     WHEN 库龄天数 < 30 THEN '①<30天'
                     WHEN 库龄天数 < 90 THEN '②30-90天'
                     WHEN 库龄天数 < 180 THEN '③90-180天'
                     WHEN 库龄天数 < 365 THEN '④180-365天'
                     ELSE '⑤>365天(呆滞)'
                   END AS 库龄段,
                   COUNT(*) AS 台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 货值_万
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1
             GROUP BY 1 ORDER BY 1
        """, conn, params=(quarter,))

        return {
            'kpi': {'在库台数': kpi[0], '在库货值_万': kpi[1] or 0,
                    '代理商数': kpi[2], 'SKU数': kpi[3]},
            'focus': focus.to_dict('records'),
            'category': cat.to_dict('records'),
            'dealer': dealer.to_dict('records'),
            'age': age.to_dict('records'),
        }
    finally:
        conn.close()


# ═══════════════════════ Tab 2: 呆滞品 ═══════════════════════

def get_dazhi(quarter: str, threshold: int = 365) -> dict:
    conn = _conn()
    try:
        kpi = conn.execute(f"""
            SELECT COUNT(*) AS 呆滞台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 呆滞货值_万,
                   COUNT(DISTINCT 盘库客户名称) AS 涉及代理商,
                   COUNT(DISTINCT 产品料号) AS 涉及SKU
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1 AND 库龄天数 > ?
        """, (quarter, threshold)).fetchone()
        # 全部在库(算占比)
        total = conn.execute("""
            SELECT COUNT(*), ROUND(SUM(产品现有分销价)/10000, 1)
              FROM inventory_snapshot WHERE 盘库季度 = ? AND 是否在库 = 1
        """, (quarter,)).fetchone()

        dealer = pd.read_sql(f"""
            SELECT 盘库客户名称 AS 代理商,
                   COUNT(*) AS 呆滞台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 呆滞货值_万,
                   ROUND(AVG(库龄天数), 0) AS 平均库龄天
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1 AND 库龄天数 > ?
             GROUP BY 1 ORDER BY 呆滞货值_万 DESC
        """, conn, params=(quarter, threshold))

        sku = pd.read_sql(f"""
            SELECT 内部型号, COALESCE(产品系列,'') AS 产品系列,
                   COUNT(*) AS 呆滞台数,
                   ROUND(SUM(产品现有分销价)/10000, 1) AS 呆滞货值_万,
                   ROUND(AVG(库龄天数), 0) AS 平均库龄天
              FROM inventory_snapshot
             WHERE 盘库季度 = ? AND 是否在库 = 1 AND 库龄天数 > ?
             GROUP BY 内部型号 ORDER BY 呆滞货值_万 DESC LIMIT 50
        """, conn, params=(quarter, threshold))

        return {
            'kpi': {'呆滞台数': kpi[0], '呆滞货值_万': kpi[1] or 0,
                    '涉及代理商': kpi[2], '涉及SKU': kpi[3],
                    '总在库台数': total[0], '总在库货值_万': total[1] or 0,
                    '呆滞台数占比': kpi[0] / total[0] if total[0] else 0,
                    '呆滞货值占比': (kpi[1] or 0) / (total[1] or 1)},
            'dealer': dealer.to_dict('records'),
            'sku': sku.to_dict('records'),
        }
    finally:
        conn.close()


def list_dazhi_detail(quarter: str, threshold: int = 365,
                       dealer: str = None, keyword: str = None) -> pd.DataFrame:
    conn = _conn()
    try:
        where = ['盘库季度 = ?', '是否在库 = 1', '库龄天数 > ?']
        params = [quarter, threshold]
        if dealer and dealer != '全部':
            where.append('盘库客户名称 = ?'); params.append(dealer)
        if keyword:
            where.append('(内部型号 LIKE ? OR 产品名称 LIKE ?)')
            params += [f'%{keyword}%', f'%{keyword}%']
        return pd.read_sql(f"""
            SELECT 产品序列号, 盘库客户名称 AS 代理商, 内部型号, 产品系列,
                   库龄天数, 出库时间, ROUND(产品现有分销价, 0) AS 分销价
              FROM inventory_snapshot
             WHERE {' AND '.join(where)}
             ORDER BY 库龄天数 DESC LIMIT 500
        """, conn, params=params)
    finally:
        conn.close()


# ═══════════════════════ Tab 3: 库存周转 ═══════════════════════

def get_turnover(quarter: str) -> dict:
    conn = _conn()
    try:
        # 进销存:盘到总数 / 在库 / 已动销(有上线时间)
        flow = conn.execute("""
            SELECT COUNT(*) AS 盘到总数,
                   SUM(是否在库) AS 在库,
                   SUM(CASE WHEN 是否在库 = 0 THEN 1 ELSE 0 END) AS 已动销
              FROM inventory_snapshot WHERE 盘库季度 = ?
        """, (quarter,)).fetchone()
        动销率 = flow[2] / flow[0] if flow[0] else 0

        # 分代理商动销率
        dealer = pd.read_sql("""
            SELECT 盘库客户名称 AS 代理商,
                   COUNT(*) AS 盘到,
                   SUM(是否在库) AS 在库,
                   SUM(CASE WHEN 是否在库=0 THEN 1 ELSE 0 END) AS 已动销,
                   ROUND(SUM(CASE WHEN 是否在库=0 THEN 1 ELSE 0 END)*1.0/COUNT(*), 3) AS 动销率,
                   ROUND(AVG(CASE WHEN 是否在库=1 THEN 库龄天数 END), 0) AS 在库平均库龄
              FROM inventory_snapshot WHERE 盘库季度 = ?
             GROUP BY 1 HAVING 盘到 >= 100 ORDER BY 动销率 DESC
        """, conn, params=(quarter,))

        return {
            'flow': {'盘到总数': flow[0], '在库': flow[1], '已动销': flow[2],
                     '动销率': 动销率},
            'dealer': dealer.to_dict('records'),
        }
    finally:
        conn.close()


# ═══════════════════════ Tab 4: 库存趋势 ═══════════════════════

def get_trend() -> dict:
    conn = _conn()
    try:
        # 季度趋势
        q = pd.read_sql("""
            SELECT 盘库季度,
                   SUM(是否在库) AS 在库台数,
                   ROUND(SUM(CASE WHEN 是否在库=1 THEN 产品现有分销价 ELSE 0 END)/10000, 1) AS 在库货值_万,
                   SUM(CASE WHEN 是否在库=1 AND 库龄天数>365 THEN 1 ELSE 0 END) AS 呆滞台数
              FROM inventory_snapshot
             WHERE 盘库季度 != '其他'
             GROUP BY 盘库季度
             ORDER BY 盘库季度
        """, conn)

        # 分专项趋势
        focus = pd.read_sql("""
            SELECT inv.盘库季度, fc.专项,
                   SUM(inv.是否在库) AS 在库台数
              FROM inventory_snapshot inv
              JOIN product_focus fc ON fc.物料号 = inv.产品料号
             WHERE inv.盘库季度 != '其他'
             GROUP BY 1, 2
        """, conn)

        return {
            'quarter': q.to_dict('records'),
            'focus': focus.to_dict('records'),
        }
    finally:
        conn.close()
