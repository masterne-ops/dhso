"""待激活客户跟进看板 — 数据层。

口径（与金先生确认）：
  - 待激活客户 = 被打 '⏳本月待激活客户'(pending_activation) 标签的服务商
  - 归属月 = 该客户首次被打 pending_activation 的月份（provider_tag_event 最早 assign）
  - 达标✅ = 2026 累计 install_redpack.上线时下单价 ≥ 1000 元（截至所选月）
  - 城市/代理商/业务员 = provider_contract.客户城市 / 上级分销商名称 / 客户所有者
    （待激活客户可能还没上线记录，维度取自签约表 provider_contract，9462 户全非空）
  - 跑动分我司/代理商 = visit_record_v._打卡方（空=🏢大华，否则🏪代理商）
  - 跨月遗留 = 归属月 < 所选月 且 截至所选月仍未达标 → 显示并标「原月·未完成」
"""
import sqlite3
from datetime import datetime

import pandas as pd

from _loaders import DB_PATH
from _ai_log import _ensure_table

PENDING_TAG = 'pending_activation'
TARGET = 1000           # 达标线：2026 累计上线时下单价 ≥ 1000 元
YEAR = '2026'


# ──────────────────────────────────────────
# 跟进备注（按 客户×月）
# ──────────────────────────────────────────
def upsert_followup_note(*, 客户编码, 归属年月, 备注, 更新人=''):
    """写/更新某客户某月的跟进备注（客户×月 唯一）。"""
    _ensure_table()
    now = datetime.now().isoformat(timespec='seconds')
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE pending_followup_note SET 备注=?, 更新人=?, 更新时间=? "
            "WHERE 客户编码=? AND 归属年月=?",
            (备注, 更新人, now, str(客户编码), 归属年月))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO pending_followup_note (客户编码, 归属年月, 备注, 更新人, 更新时间) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(客户编码), 归属年月, 备注, 更新人, now))
        conn.commit()
    finally:
        conn.close()


def get_followup_note(客户编码, 归属年月):
    """取某客户某月备注，无则 None。"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT 备注, 更新人, 更新时间 FROM pending_followup_note "
            "WHERE 客户编码=? AND 归属年月=?",
            (str(客户编码), 归属年月)).fetchone()
        return {'备注': row[0], '更新人': row[1], '更新时间': row[2]} if row else None
    finally:
        conn.close()


# ──────────────────────────────────────────
# 待激活清单（含跨月遗留 + 达标）
# ──────────────────────────────────────────
def get_pending_board(year_month, *, city=None, district=None, dealer=None, owner=None):
    """选定月份的待激活客户清单。

    返回 DataFrame[客户编码, 客户名称, 城市, 所属代理商, 客户所有者,
                   归属月, 是否遗留, 累计货值, 台数, 达标, 状态, 备注]
    清单规则：归属月 == year_month 全显示；归属月 < year_month 仅显示『仍未达标』的遗留。
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        # 1) 归属月 = pending_activation 首次 assign 年月
        ev = pd.read_sql(
            "SELECT 客户编码, MIN(事件年月) AS 归属月, MIN(客户名称) AS _evt名 "
            "FROM provider_tag_event WHERE tag_key=? AND 事件类型='assign' "
            "GROUP BY 客户编码", conn, params=(PENDING_TAG,))
        if ev.empty:
            return pd.DataFrame()
        ev['客户编码'] = ev['客户编码'].astype(str)
        ev = ev[ev['归属月'] <= year_month]
        if ev.empty:
            return pd.DataFrame()

        # 2) 2026 累计上线时下单价（截至所选月）+ 台数
        so = pd.read_sql(
            "SELECT 上线客户编码 AS 客户编码, "
            "SUM(COALESCE(CAST(上线时下单价 AS REAL),0)) AS 累计货值, "
            "COUNT(产品序列号) AS 台数 "
            "FROM install_redpack_v WHERE 上线年月 BETWEEN ? AND ? "
            "GROUP BY 上线客户编码", conn, params=(f'{YEAR}-01', year_month))
        so['客户编码'] = so['客户编码'].astype(str)

        # 3) 维度 + 名称（provider_contract，待激活客户必有）
        prov = pd.read_sql(
            'SELECT 客户编码, 客户名称, 客户城市 AS 城市, 客户区县 AS 区县, '
            '上级分销商名称 AS 所属代理商, 客户所有者 '
            'FROM provider_contract WHERE 客户编码 IS NOT NULL',
            conn).drop_duplicates('客户编码')
        prov['客户编码'] = prov['客户编码'].astype(str)

        # 4) 备注（所选月）
        notes = pd.read_sql(
            "SELECT 客户编码, 备注 FROM pending_followup_note WHERE 归属年月=?",
            conn, params=(year_month,))
        notes['客户编码'] = notes['客户编码'].astype(str)

        # 合并
        df = (ev.merge(prov, on='客户编码', how='left')
                .merge(so, on='客户编码', how='left')
                .merge(notes, on='客户编码', how='left'))
        df['客户名称'] = df['客户名称'].fillna(df['_evt名']).fillna(df['客户编码'])
        df['累计货值'] = pd.to_numeric(df['累计货值'], errors='coerce').fillna(0.0)
        df['台数'] = pd.to_numeric(df['台数'], errors='coerce').fillna(0).astype(int)
        df['达标'] = df['累计货值'] >= TARGET
        df['是否遗留'] = df['归属月'] < year_month

        # 月份逻辑：当月新打标全显示；更早月份只显示仍未达标的遗留
        keep = (df['归属月'] == year_month) | (df['是否遗留'] & ~df['达标'])
        df = df[keep].copy()

        # 授牌服务商 = 无价值客户(管理标签口径,见 _provider_flags),不进待激活重点跟进
        from _provider_flags import worthless_codes
        df = df[~df['客户编码'].isin(worthless_codes(conn))]

        # 维度筛选
        if city:
            df = df[df['城市'] == city]
        if district:
            df = df[df['区县'] == district]
        if dealer:
            df = df[df['所属代理商'] == dealer]
        if owner:
            df = df[df['客户所有者'] == owner]
        if df.empty:
            return df

        def _status(r):
            if r['达标']:
                return '✅ 达标'
            if r['是否遗留']:
                return f"🔴 {r['归属月']}·未完成"
            return '⏳ 跟进中'
        df['状态'] = df.apply(_status, axis=1)
        df['备注'] = df['备注'].fillna('')
        cols = ['客户编码', '客户名称', '城市', '区县', '所属代理商', '客户所有者',
                '归属月', '是否遗留', '累计货值', '台数', '达标', '状态', '备注']
        return (df[cols]
                .sort_values(['达标', '归属月', '累计货值'],
                             ascending=[True, True, False])
                .reset_index(drop=True))
    finally:
        conn.close()


# ──────────────────────────────────────────
# 选中客户的详情：2026 月度 SO + 跑动（我司/代理商）
# ──────────────────────────────────────────
def get_client_detail(客户编码, year_month):
    """选中客户的 2026 月度 SO（货值/台数）+ 跑动（按 _打卡方 分我司/代理商），截至所选月。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        so = pd.read_sql(
            "SELECT 上线年月 AS 月, "
            "ROUND(SUM(COALESCE(CAST(上线时下单价 AS REAL),0)),0) AS 货值, "
            "COUNT(产品序列号) AS 台数 "
            "FROM install_redpack_v "
            "WHERE 上线客户编码=? AND 上线年月 BETWEEN ? AND ? "
            "GROUP BY 上线年月 ORDER BY 上线年月",
            conn, params=(str(客户编码), f'{YEAR}-01', year_month))
        try:
            visits = pd.read_sql(
                "SELECT 拜访年月 AS 月, _打卡方, COUNT(*) AS 次数 "
                "FROM visit_record_v "
                "WHERE 客户编码=? AND 拜访年月 BETWEEN ? AND ? "
                "AND COALESCE(_打卡异常无效,0)=0 "
                "GROUP BY 拜访年月, _打卡方 ORDER BY 拜访年月",
                conn, params=(str(客户编码), f'{YEAR}-01', year_month))
        except Exception:
            visits = pd.DataFrame(columns=['月', '_打卡方', '次数'])
        return {'so_monthly': so, 'visits': visits}
    finally:
        conn.close()
