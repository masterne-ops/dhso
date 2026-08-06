"""业务员跑动任务分配 + 核验（page 07）

分配算法（按 RFM + scope）：
  🚨 高优：scope 内 V4/V3 严重漏跑（期初 R≤60，期末 R>60，累计货值 ≥1万）
  ⚠️ 中优：scope 内 B2 救援未果（拜访时 R 30-60，30 天内无激活）
  ⚠️ 中优：scope 内 E3 维护不到位（拜访时 R 健康，当期末 R 滑到 30-60）
  🟢 低优：scope 内 V3+ 但 F<3（频次不达标），且本期未访

核验逻辑（按月初执行）：
  完成状态：
    已激活 — 实际拜访 + 30 天内有新激活（拉动 SO）
    已拜访 — 去过但 30 天内无激活
    未完成 — 完全没去
    逾期   — 月末了还没去
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'

R_HEALTHY = 30
R_WARN = 60
F_BAD = 3


def _next_month(ym: str) -> str:
    """'2026-04' -> '2026-05'"""
    return str(pd.Period(ym, freq='M') + 1)


def _prev_month(ym: str) -> str:
    return str(pd.Period(ym, freq='M') - 1)


from _metrics_rfm import calc_rfm, tier_of as _value_tier  # 货值档=全历史累计货值分层(非服务商等级)


def _calc_rfm_at(acts_idx: dict, code: str, asof: pd.Timestamp) -> Optional[dict]:
    """兼容旧签名,转调 _metrics_rfm.calc_rfm"""
    g = acts_idx.get(str(code))
    if g is None or g.empty:
        return None
    r = calc_rfm(g, asof, with_growth=False)
    if r is None:
        return None
    return {'R': r['R'], 'F': r['F'], 'M': r['M_cur']}


def generate_tasks(
    conn: sqlite3.Connection,
    city: str,
    target_month: str,                   # 下月 '2026-06'
    eval_period_start: str,              # 评估窗口起 '2026-01'
    eval_period_end: str,                # 评估窗口止 '2026-05'
    per_sp_limit: int = 50,
) -> pd.DataFrame:
    """生成下月任务清单（不写库）。返回 DataFrame，调用方负责审核后批量入库。

    逻辑：
      1. 拿评估期内 page 06 跑动评估的结果（每个大华业务员的漏跑 + 救援未果）
      2. 加上 scope 内 F偏弱（V3+ 但 F<3）的客户
      3. ⚔️ 加上 competitor_top_provider 里的客户（按表里的责任人派，跨 scope）
      4. 排除 closed_provider 客户（明确无采购意向）
      5. 每个业务员排序后取 top per_sp_limit
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from _visit_rfm_eval import evaluate_salesperson, list_all_salespeople

    period_end_dt = pd.Timestamp(eval_period_end + '-01') + pd.offsets.MonthEnd(0)

    # 取所有有 scope 的大华业务员（仅这些人有 scope，代理商业务员暂无）
    sp_rows = pd.read_sql("""
        SELECT DISTINCT 业务员 FROM salesperson_scope WHERE 市 = ?
    """, conn, params=(city,))
    dahua_sps = sp_rows['业务员'].tolist()

    # ⚔️ 竞品 Top 客户 — 即使业务员不在 salesperson_scope 也派
    competitor_top_df = pd.read_sql("""
        SELECT 客户编码, 客户名称, 城市, 区县,
               客户经营品牌, 在售大华, 竞品体量_万,
               责任人姓名, 责任人角色, 转化策略
          FROM competitor_top_provider WHERE 城市 = ?
    """, conn, params=(city,))

    if not dahua_sps and competitor_top_df.empty:
        return pd.DataFrame()

    # 取该城市所有客户的累计上线（用于算货值档 + F）
    all_codes = pd.read_sql("""
        SELECT 上线客户编码 AS 客户编码, 上线时间, 产品现有分销价
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(city,))
    all_codes['上线时间'] = pd.to_datetime(all_codes['上线时间'])
    acts_idx = {c: g for c, g in all_codes.groupby('客户编码')}

    # 当期已被拜访的客户（这些下月任务里建议过滤掉，避免重复派）
    visited = set(pd.read_sql("""
        SELECT DISTINCT 客户编码 FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
    """, conn, params=(city, eval_period_start, eval_period_end))['客户编码'].astype(str))

    # 「明确无采购意向」客户 — 业务方人工标记，从任务分配排除（最高优先级过滤）
    from _ai_log import closed_provider_codes
    closed_codes = closed_provider_codes(city=city)
    # 授牌服务商 = 无价值客户(管理标签口径),同样不进任何跑动任务(见 _provider_flags)
    from _provider_flags import worthless_codes
    closed_codes = set(closed_codes) | worthless_codes(conn)

    records = []
    for sp in dahua_sps:
        try:
            r = evaluate_salesperson(conn, city, sp, eval_period_start, eval_period_end)
        except Exception:
            continue

        scope_codes = r['scope_codes']
        visits_df = r['visits_df']
        missed_df = r['missed_df']

        sp_records = []

        # 🚨 高优：scope 内严重漏跑（V3/V4）
        if not missed_df.empty:
            severe = missed_df[missed_df['严重度'] == '🚨 严重']
            for _, m in severe.iterrows():
                sp_records.append({
                    '客户编码': m['客户编码'],
                    '客户名称': m['客户名称'],
                    '区县': m['区县'],
                    '任务类型': '严重漏跑救援',
                    '优先级': '🚨 高',
                    '任务说明': (
                        f"{m['等级']} | 累计 {m['累计货值_万']}万 | "
                        f"期初 R={m['期初_R']}天 → 期末 R={m['期末_R']}天 滑落"
                    ),
                    '_sort_key': (0, -float(m['累计货值_万'])),  # 高优 + 货值大
                })

        # ⚠️ 中优:本期救援失败(原 B 类合并)
        from _metrics_rfm import LABEL_RESCUE_FAIL, LABEL_LOST
        if not visits_df.empty:
            b_cases = visits_df[visits_df['分类'] == LABEL_RESCUE_FAIL]
            for _, b in b_cases.iterrows():
                # 上月已拜访的客户避免重复派
                if str(b['客户编码']) in visited:
                    优先级 = '⚠️ 中'
                    sort_priority = 1
                else:
                    优先级 = '🚨 高'
                    sort_priority = 0
                rfm_end = _calc_rfm_at(acts_idx, b['客户编码'], period_end_dt)
                if rfm_end is None:
                    continue
                vtier = _value_tier(rfm_end['M'])  # 货值档
                if vtier == 'v0':
                    continue
                sp_records.append({
                    '客户编码': b['客户编码'],
                    '客户名称': b['客户名称'],
                    '区县': b['区县'],
                    '任务类型': 'R预警救援',
                    '优先级': 优先级,
                    '任务说明': (
                        f"货值档{vtier} | {b['分类']} | 上次拜访 {b['首次拜访日'].strftime('%Y-%m-%d')} 后无激活"
                    ),
                    '_sort_key': (sort_priority, -float(rfm_end['M'])),
                })

            # ⚠️ 中优:本期流失(原 E2+E3 合并)
            e3 = visits_df[visits_df['分类'] == LABEL_LOST]
            for _, e in e3.iterrows():
                rfm_end = _calc_rfm_at(acts_idx, e['客户编码'], period_end_dt)
                if rfm_end is None:
                    continue
                sp_records.append({
                    '客户编码': e['客户编码'],
                    '客户名称': e['客户名称'],
                    '区县': e['区县'],
                    '任务类型': '维护到期复访',
                    '优先级': '⚠️ 中',
                    '任务说明': f"货值档{_value_tier(rfm_end['M'])} | 期末 R={rfm_end['R']} - 已流失",
                    '_sort_key': (1, -float(rfm_end['M'])),
                })

        # 🟢 低优：scope 内 V3+ 但 F<3 且本期未拜访
        for code in scope_codes:
            if str(code) in visited:
                continue  # 拜访过的不再补任务（下月新批次）
            rfm = _calc_rfm_at(acts_idx, code, period_end_dt)
            if rfm is None:
                continue
            vtier = _value_tier(rfm['M'])  # 货值档
            if vtier in ('v0', '已激活', 'V2'):
                continue  # 仅货值档 V3+
            if rfm['F'] >= F_BAD:
                continue  # F 不弱
            if rfm['R'] > R_WARN:
                continue  # 已经严重漏跑了，上面 missed_df 已经覆盖

            # 拿客户名 + 区县
            g = acts_idx.get(str(code))
            if g is None or g.empty:
                continue
            name = pd.read_sql(
                "SELECT MAX(上线客户名称) AS n, MAX(上线客户区县) AS d "
                "FROM install_redpack_v WHERE 上线客户编码 = ?",
                conn, params=(str(code),),
            ).iloc[0]
            sp_records.append({
                '客户编码': str(code),
                '客户名称': name['n'],
                '区县': name['d'],
                '任务类型': 'F偏弱补访',
                '优先级': '🟢 低',
                '任务说明': f"货值档{vtier} | 12mo 内仅 {rfm['F']} 次激活 | R={rfm['R']}天",
                '_sort_key': (2, -float(rfm['M'])),
            })

        # 去重 + 排序 + 截断（先剔除「明确无采购意向」客户）
        seen_codes = set()
        unique = []
        for r in sorted(sp_records, key=lambda x: x['_sort_key']):
            if r['客户编码'] in seen_codes:
                continue
            if str(r['客户编码']) in closed_codes:
                continue        # 明确无采购意向 → 不派任务
            seen_codes.add(r['客户编码'])
            r.pop('_sort_key', None)
            r['任务年月'] = target_month
            r['地市'] = city
            r['业务员'] = sp
            unique.append(r)
        records.extend(unique[:per_sp_limit])

    # ⚔️ 追加竞品开拓任务（直接按 competitor_top.责任人姓名 派，跨 scope）
    if not competitor_top_df.empty:
        for _, c in competitor_top_df.iterrows():
            code = str(c['客户编码'])
            if code in closed_codes:
                continue
            责任人 = c.get('责任人姓名')
            if not 责任人 or pd.isna(责任人):
                continue
            label = '已混卖' if c.get('在售大华') == 1 else '纯竞品'
            体量 = float(c['竞品体量_万']) if pd.notna(c['竞品体量_万']) else 0
            records.append({
                '客户编码': code,
                '客户名称': c['客户名称'],
                '区县': c['区县'],
                '任务类型': '⚔️ 竞品开拓',
                '优先级': '🚨 高',
                '任务说明': (
                    f"{label} | 经营 {c['客户经营品牌'] or '?'} | "
                    f"竞品体量 {体量:.0f} 万 | 角色: {c.get('责任人角色') or '?'}"
                )[:200],
                '任务年月': target_month,
                '地市': city,
                '业务员': str(责任人).strip(),
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    # 关联 所属一级客户
    if '所属一级客户' not in df.columns or df['所属一级客户'].isna().all():
        codes_in = "','".join(df['客户编码'].astype(str).unique())
        dealers = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码, MAX(所属一级客户) AS 所属一级客户
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{codes_in}')
             GROUP BY 上线客户编码
        """, conn)
        df = df.merge(dealers, on='客户编码', how='left')
    return df


def verify_tasks(
    conn: sqlite3.Connection,
    target_month: str,
    city: str = None,
) -> int:
    """核验 target_month 月的任务完成情况。返回更新行数。

    完成状态判定（按月末数据）：
      - 已激活：实际拜访 + 拜访后到月末 30 天内有新激活
      - 已拜访：去过但 30 天内无激活
      - 未完成：完全没去
    """
    from _ai_log import list_tasks, update_task_status

    tasks = list_tasks(任务年月=target_month, 地市=city, limit=100000)
    if tasks.empty:
        return 0

    month_start = pd.Timestamp(target_month + '-01')
    month_end = month_start + pd.offsets.MonthEnd(0)

    n = 0
    for _, t in tasks.iterrows():
        # 业务员对该客户在当月内的首次拜访
        v = pd.read_sql("""
            SELECT MIN(date(拜访时间)) AS 首次拜访日
              FROM visit_record_v
             WHERE 拜访客户城市 = ?
               AND 打卡人姓名 = ?
               AND 客户编码 = ?
               AND date(拜访时间) BETWEEN ? AND ?
               AND _打卡异常无效 = 0 AND _真异常打卡 = 0
        """, conn, params=(
            t['地市'], t['业务员'], t['客户编码'],
            month_start.strftime('%Y-%m-%d'),
            month_end.strftime('%Y-%m-%d'),
        ))
        first_visit = v.iloc[0]['首次拜访日']

        if not first_visit:
            # 没拜访 — 已经到月末 → 逾期；否则未完成
            today = pd.Timestamp.today()
            new_status = '逾期' if today > month_end else '未完成'
            update_task_status(int(t['id']), 完成状态=new_status)
            n += 1
            continue

        # 拜访后 30 天内是否有新激活
        first_visit_dt = pd.Timestamp(first_visit)
        win_end = min(first_visit_dt + timedelta(30), month_end)
        a = pd.read_sql("""
            SELECT MIN(date(上线时间)) AS 首次激活日
              FROM install_redpack_v
             WHERE 上线客户编码 = ?
               AND date(上线时间) > ?
               AND date(上线时间) <= ?
        """, conn, params=(
            t['客户编码'],
            first_visit_dt.strftime('%Y-%m-%d'),
            win_end.strftime('%Y-%m-%d'),
        ))
        first_activate = a.iloc[0]['首次激活日']

        if first_activate:
            update_task_status(int(t['id']),
                               完成状态='已激活',
                               实际拜访日=first_visit,
                               实际激活日=first_activate)
        else:
            update_task_status(int(t['id']),
                               完成状态='已拜访',
                               实际拜访日=first_visit)
        n += 1
    return n


def search_clients_for_replace(conn, city: str, sp: str, keyword: str, limit: int = 20) -> pd.DataFrame:
    """业务员手动新增任务：在 scope 内 模糊搜索客户名"""
    df = pd.read_sql("""
        WITH sp_dealers AS (
            SELECT 代理商 FROM salesperson_scope WHERE 业务员 = ? AND 市 = ?
        ),
        cands AS (
            SELECT 上线客户编码 AS 客户编码, MAX(上线客户名称) AS 客户名称,
                   MAX(上线客户区县) AS 区县, MAX(所属一级客户) AS 所属一级客户,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 累计货值_万,
                   MAX(上线年月) AS 最近上线
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 所属一级客户 IN (SELECT 代理商 FROM sp_dealers)
             GROUP BY 上线客户编码
        )
        SELECT * FROM cands
         WHERE 客户名称 LIKE ?
         ORDER BY 累计货值_万 DESC LIMIT ?
    """, conn, params=(sp, city, city, f'%{keyword}%', limit))
    return df
