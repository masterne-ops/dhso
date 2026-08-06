"""战役管理 数据访问层"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


STATUSES = ['待执行', '进行中', '已完成', '暂缓', '放弃']
PRIORITIES = ['P0', 'P1', 'P2']


def _conn():
    return sqlite3.connect(str(DB_PATH))


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_campaign(代号: str) -> dict | None:
    conn = _conn()
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(campaign)').fetchall()]
        row = conn.execute('SELECT * FROM campaign WHERE 代号 = ?', (代号,)).fetchone()
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def list_targets(campaign_id: int, 状态: str = None, 城市: str = None,
                  关键字: str = None, only_undone: bool = False) -> pd.DataFrame:
    """列出战役目标 + 实时拉取铺货/上线状态"""
    conn = _conn()
    try:
        where = ['ct.campaign_id = ?']
        params = [campaign_id]
        if 状态 and 状态 != '全部':
            where.append('ct.状态 = ?'); params.append(状态)
        if 城市 and 城市 != '全部':
            where.append('ct.城市 = ?'); params.append(城市)
        if 关键字:
            where.append('(ct.客户名称 LIKE ? OR ct.客户所有者 LIKE ? OR ct.上级代理商 LIKE ?)')
            kw = f'%{关键字}%'
            params.extend([kw, kw, kw])
        if only_undone:
            where.append("ct.状态 IN ('待执行', '进行中')")

        sql = f"""
            SELECT ct.id, ct.客户编码, ct.客户名称, ct.城市, ct.区县,
                   ct.客户所有者, ct.上级代理商, ct.责任人, ct.优先级,
                   ct.计划日期, ct.计划备注, ct.状态, ct.实际完成日期, ct.备注,
                   (SELECT COUNT(DISTINCT 序列号) FROM distribution_info di
                     WHERE di.客户名称_下级 = ct.客户名称) AS 已铺货台数,
                   (SELECT COUNT(*) FROM install_redpack ir
                     WHERE ir.上线客户名称 = ct.客户名称) AS 上线记录数
              FROM campaign_target ct
             WHERE {' AND '.join(where)}
             ORDER BY
               CASE ct.状态 WHEN '待执行' THEN 0 WHEN '进行中' THEN 1
                            WHEN '已完成' THEN 2 ELSE 3 END,
               ct.城市, ct.区县, ct.客户名称
        """
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def update_target(target_id: int, **fields):
    """更新单个目标(状态/责任人/优先级/计划日期/计划备注/备注)"""
    conn = _conn()
    try:
        allowed = {'状态', '责任人', '优先级', '计划日期', '计划备注', '备注', '实际完成日期'}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f'"{k}" = ?')
                params.append(v)
        if not sets:
            return
        if fields.get('状态') == '已完成' and not fields.get('实际完成日期'):
            sets.append('实际完成日期 = ?')
            params.append(_now()[:10])
        sets.extend(['更新人 = ?', '更新时间 = ?'])
        params.extend([fields.get('_operator', 'Admin'), _now()])
        params.append(target_id)
        conn.execute(f'UPDATE campaign_target SET {", ".join(sets)} WHERE id = ?', params)
        conn.commit()
    finally:
        conn.close()


def refresh_status_from_data(campaign_id: int) -> int:
    """根据 distribution_info 自动刷新状态(已铺货 → 已完成)"""
    conn = _conn()
    try:
        # 之前是"待执行"且现在已经在 distribution_info 出现 → 改为"已完成"
        cur = conn.execute("""
            UPDATE campaign_target
               SET 状态 = '已完成',
                   实际完成日期 = COALESCE(实际完成日期, ?),
                   更新人 = 'auto-refresh',
                   更新时间 = ?
             WHERE campaign_id = ?
               AND 状态 IN ('待执行', '进行中')
               AND EXISTS (SELECT 1 FROM distribution_info di
                            WHERE di.客户名称_下级 = campaign_target.客户名称)
        """, (_now()[:10], _now(), campaign_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_kpi(campaign_id: int) -> dict:
    """KPI 看板:总数 / 已完成 / 进度 + 上线情况"""
    conn = _conn()
    try:
        kpi = conn.execute("""
            SELECT
              COUNT(*) AS 总数,
              SUM(CASE WHEN 状态 = '已完成' THEN 1 ELSE 0 END) AS 已完成,
              SUM(CASE WHEN 状态 = '待执行' THEN 1 ELSE 0 END) AS 待执行,
              SUM(CASE WHEN 状态 = '进行中' THEN 1 ELSE 0 END) AS 进行中,
              SUM(CASE WHEN 状态 = '暂缓' THEN 1 ELSE 0 END) AS 暂缓,
              SUM(CASE WHEN 状态 = '放弃' THEN 1 ELSE 0 END) AS 放弃
              FROM campaign_target WHERE campaign_id = ?
        """, (campaign_id,)).fetchone()
        cols = ['总数', '已完成', '待执行', '进行中', '暂缓', '放弃']
        return dict(zip(cols, kpi))
    finally:
        conn.close()


def get_distribution_detail(客户名称: str) -> pd.DataFrame:
    """单客户的铺货明细(供 expander 速览)"""
    conn = _conn()
    try:
        return pd.read_sql("""
            SELECT 提交铺货时间, 铺货单号, 铺货单状态,
                   COUNT(DISTINCT 序列号) AS 台数,
                   GROUP_CONCAT(DISTINCT 产品系列) AS 产品系列,
                   客户名称_上级 AS 上级
              FROM distribution_info
             WHERE 客户名称_下级 = ?
             GROUP BY 铺货单号
             ORDER BY 提交铺货时间 DESC
        """, conn, params=(客户名称,))
    finally:
        conn.close()


def get_install_detail(客户名称: str, limit: int = 50) -> pd.DataFrame:
    """单客户的上线明细"""
    conn = _conn()
    try:
        return pd.read_sql("""
            SELECT 上线时间, 内部型号, 产品现有分销价 AS 金额, 物料号
              FROM install_redpack
             WHERE 上线客户名称 = ?
             ORDER BY 上线时间 DESC LIMIT ?
        """, conn, params=(客户名称, limit))
    finally:
        conn.close()
