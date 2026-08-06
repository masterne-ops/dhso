"""月度地市经营报告 — 数据汇总模块

职责：把所有数字算准，喂给 V3 沙箱 AI 拼装 docx。

数据汇总分 5 块：
  1.1 SO 总览（双口径：全量感知 product_flow_v / 安装红包 install_redpack_v）
  1.2 目标完成（kpi_targets × kpi_rhythm 算应达成 vs 实际）
  1.3 区县分布（top + bottom）
  1.4 服务商画像（等级分布、新激活、流失、集中度 top10）
  1.5 业务员跑动（有效打卡、大华 vs 代理商、异常分类、top 业务员）

每块都跑 3 次：当期、同期（去年同期）、环期（紧邻当期前 N 个月，N=当期月数）

不让 V3 AI 写 SQL —— 数字准确性优先。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from _metrics_rfm import tier_case_sql, tier_of, NINE_CELL_LABEL

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'


# ══════════════════════════════════════════════
# 时段计算
# ══════════════════════════════════════════════

@dataclass
class Period:
    start: str   # 'YYYY-MM'
    end: str     # 'YYYY-MM'
    n_months: int
    label: str   # 'YYYY-MM' or 'YYYY-MM ~ YYYY-MM'

    def months(self) -> list[str]:
        """返回这个时段含的所有月份 'YYYY-MM' 列表"""
        s = pd.Period(self.start, freq='M')
        e = pd.Period(self.end, freq='M')
        return [str(s + i) for i in range((e - s).n + 1)]


def make_period(start: str, end: str) -> Period:
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    n = (e - s).n + 1
    label = start if start == end else f'{start} ~ {end}'
    return Period(start=str(s), end=str(e), n_months=n, label=label)


def calculate_periods(start: str, end: str) -> dict[str, Period]:
    """
    user 给 start='2026-01', end='2026-04'：
      current: 2026-01 ~ 2026-04 (4 个月)
      yoy:     2025-01 ~ 2025-04 (去年同期 4 个月)
      mom:     2025-09 ~ 2025-12 (紧邻当期前 4 个月)
    """
    current = make_period(start, end)
    n = current.n_months

    s = pd.Period(current.start, freq='M')
    e = pd.Period(current.end, freq='M')

    yoy = make_period(str(s - 12), str(e - 12))
    mom_end = s - 1
    mom_start = mom_end - (n - 1)
    mom = make_period(str(mom_start), str(mom_end))

    return {'current': current, 'yoy': yoy, 'mom': mom}


# ══════════════════════════════════════════════
# 各维度查询
# ══════════════════════════════════════════════

def _pct_change(curr: float, base: float) -> Optional[float]:
    """返回小数百分比，base=0 时返回 None（避免无穷大）"""
    if base is None or base == 0:
        return None
    return (curr - base) / base


def query_so_overview(conn, city: str, period: Period) -> dict:
    """1.1 SO 总览：全量感知 + 安装红包 + 绑定率

    重要：如果某表在该时段行数 = 0 (数据缺失)，**所有相关字段返回 None**
    而不是 0。这样下游对比表会显示 N/A，不会误导。
    """
    # 全量感知（product_flow_v 按上线城市）
    main_df = pd.read_sql("""
        SELECT COUNT(*) AS 台数,
               COALESCE(SUM(最新分销价), 0) AS 全量金额
          FROM product_flow_v
         WHERE 上线城市 = ?
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(city, period.start, period.end))
    main_n_raw = int(main_df.iloc[0]['台数'])
    main_amt_raw = float(main_df.iloc[0]['全量金额'])

    # 安装红包（install_redpack_v 按上线客户地市 = 服务商所属地市）
    rp_df = pd.read_sql("""
        SELECT COUNT(*) AS 台数,
               COALESCE(SUM(产品现有分销价), 0) AS 红包金额
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(city, period.start, period.end))
    rp_n_raw = int(rp_df.iloc[0]['台数'])
    rp_amt_raw = float(rp_df.iloc[0]['红包金额'])

    # 数据缺失判定
    main_missing = (main_n_raw == 0)
    rp_missing = (rp_n_raw == 0)

    # 绑定率仅当两表都有数据时才算
    if main_missing or rp_missing or main_amt_raw == 0:
        binding = None
    else:
        binding = round(rp_amt_raw / main_amt_raw, 4)

    return {
        '全量感知_台数': None if main_missing else main_n_raw,
        '全量感知_金额_万': None if main_missing else round(main_amt_raw / 10000, 2),
        '安装红包_台数': None if rp_missing else rp_n_raw,
        '安装红包_金额_万': None if rp_missing else round(rp_amt_raw / 10000, 2),
        '绑定率': binding,
        '_全量感知数据缺失': main_missing,
        '_安装红包数据缺失': rp_missing,
    }


def query_target_completion(conn, city: str, period: Period, year: int = 2026) -> dict:
    """1.2 目标完成情况"""
    # 解析当期月份
    months = period.months()
    month_nums = [int(m.split('-')[1]) for m in months]
    placeholders = ','.join('?' * len(month_nums))

    # 应达成（仅 year 年的）
    if str(year) not in period.start:
        # 跨年了或不是当年的，应达成不算（避免跨年节奏混淆）
        target_amt = None
    else:
        tgt_df = pd.read_sql(f"""
            SELECT COALESCE(SUM(t.SO目标_万 * r.占比), 0) AS 应达成_万
              FROM kpi_targets t
              JOIN kpi_rhythm  r ON r.年度 = t.年度
             WHERE t.城市 = ?
               AND t.年度 = ?
               AND r.指标 LIKE '省区SO进度条%'
               AND r.月份 IN ({placeholders})
        """, conn, params=(city, year, *month_nums))
        target_amt = float(tgt_df.iloc[0]['应达成_万']) if not tgt_df.empty else 0

    # 实际达成（**全量感知口径** = product_flow_v 最新分销价之和）
    # SO 目标本来就是公司销售订单（出货）口径，所以实际也用全量感知
    actual_df = pd.read_sql("""
        SELECT COALESCE(SUM(最新分销价), 0) / 10000.0 AS 实际_万
          FROM product_flow_v
         WHERE 上线城市 = ?
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(city, period.start, period.end))
    actual_amt = float(actual_df.iloc[0]['实际_万'])

    # 辅助参考：安装红包口径金额（不算完成率，只展示）
    rp_df = pd.read_sql("""
        SELECT COALESCE(SUM(产品现有分销价), 0) / 10000.0 AS 红包_万
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
    """, conn, params=(city, period.start, period.end))
    rp_amt = float(rp_df.iloc[0]['红包_万'])

    completion = None
    if target_amt and target_amt > 0:
        completion = actual_amt / target_amt

    return {
        '应达成_万': round(target_amt, 1) if target_amt is not None else None,
        '实际达成_万': round(actual_amt, 1),             # 全量感知口径
        '安装红包_万': round(rp_amt, 1),                  # 辅助参考（无对应目标）
        '完成率': round(completion, 4) if completion is not None else None,
        '_口径': '全量感知（product_flow_v 最新分销价）',
    }


def query_district_breakdown(conn, city: str, period: Period, top_k: int = 5) -> dict:
    """1.3 区县分布"""
    df = pd.read_sql("""
        SELECT pf.上线区县 AS 区县,
               COUNT(*) AS 台数,
               COALESCE(SUM(pf.最新分销价), 0) / 10000.0 AS 全量金额_万
          FROM product_flow_v pf
         WHERE pf.上线城市 = ?
           AND pf.上线年月 BETWEEN ? AND ?
           AND pf.上线区县 IS NOT NULL
           AND pf.上线区县 != ''
         GROUP BY pf.上线区县
         ORDER BY 全量金额_万 DESC
    """, conn, params=(city, period.start, period.end))

    if df.empty:
        return {'top': [], 'bottom': [], '区县数': 0}

    # 补红包金额（按上线客户区县）
    rp = pd.read_sql("""
        SELECT 上线客户区县 AS 区县,
               COALESCE(SUM(产品现有分销价), 0) / 10000.0 AS 红包金额_万
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户区县 IS NOT NULL
         GROUP BY 上线客户区县
    """, conn, params=(city, period.start, period.end))

    df = df.merge(rp, on='区县', how='left').fillna({'红包金额_万': 0})

    # 加目标完成（如果有）— 按区县
    # 注意：跨年时段的应达成不计算（节奏只在本年内有效）
    months_in_period = period.months()
    if all(m.startswith(period.start.split('-')[0]) for m in months_in_period):
        year_int = int(period.start.split('-')[0])
        month_nums = [int(m.split('-')[1]) for m in months_in_period]
        placeholders = ','.join('?' * len(month_nums))
        tgt = pd.read_sql(f"""
            SELECT t.区县 AS 区县,
                   SUM(t.SO目标_万 * r.占比) AS 应达成_万
              FROM kpi_targets t
              JOIN kpi_rhythm  r ON r.年度 = t.年度
             WHERE t.城市 = ?
               AND t.年度 = ?
               AND r.指标 LIKE '省区SO进度条%'
               AND r.月份 IN ({placeholders})
             GROUP BY t.区县
        """, conn, params=(city, year_int, *month_nums))
        df = df.merge(tgt, on='区县', how='left')
    else:
        df['应达成_万'] = None

    # ⭐ 完成率口径：**全量金额 / 应达成**（跟 1.2 全市完成率一致）
    # SO 目标本来就是全量感知（出货）口径，所以分子也用 product_flow_v 的全量金额
    df['完成率'] = df.apply(
        lambda r: (r['全量金额_万'] / r['应达成_万'])
                  if pd.notna(r.get('应达成_万')) and r['应达成_万'] > 0 else None,
        axis=1,
    )
    # 辅助参考：区县「红包/全量」比 — 不是"绑定率"（绑定率是省级口径），但能反映服务商网络覆盖
    df['红包占全量比'] = df.apply(
        lambda r: (r['红包金额_万'] / r['全量金额_万']) if r['全量金额_万'] > 0 else None,
        axis=1,
    )

    df = df.round({
        '全量金额_万': 1, '红包金额_万': 1, '应达成_万': 1,
        '完成率': 4, '红包占全量比': 4,
    })

    # top：按全量金额排序前 K（业务上「重头区县」更重要）
    df_sorted = df.sort_values('全量金额_万', ascending=False)
    top = df_sorted.head(top_k).to_dict('records')

    # bottom：按完成率升序前 K（**最该警报的区县** — 完成率为 N/A 的排最后）
    df_bot = df.copy()
    # pd.fillna 现在对 object dtype 有 FutureWarning；先 to_numeric 再 fillna 干净
    df_bot['_完成率_排序'] = pd.to_numeric(df_bot['完成率'], errors='coerce').fillna(999)
    bottom = df_bot.sort_values('_完成率_排序').head(top_k).drop(columns=['_完成率_排序'], errors='ignore').to_dict('records')

    return {'top': top, 'bottom': bottom, '区县数': len(df)}


def query_provider_profile(conn, city: str, period: Period) -> dict:
    """1.4 服务商画像（等级=provider_contract 官方原始等级 V0-V5；累计货值=provider_tier_v）"""
    # 当期活跃服务商（这段时间有上线的）。等级取 provider_contract 官方原始等级（26年官方评定）；
    # 累计货值仍取 provider_tier_v.上线累计（全历史货值，仅供 top10 集中度展示，非定级用）。
    df = pd.read_sql("""
        WITH active AS (
            SELECT 上线客户编码,
                   MAX(上线客户名称) AS 上线客户名称,
                   SUM(产品现有分销价) AS 当期货值
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 上线年月 BETWEEN ? AND ?
               AND 上线客户编码 IS NOT NULL
               AND 上线客户编码 != ''
             GROUP BY 上线客户编码
        )
        SELECT a.上线客户编码,
               a.上线客户名称,
               a.当期货值,
               COALESCE(pc.服务商等级, '未签约') AS 服务商等级,
               COALESCE(pt.上线累计, 0) AS 累计货值
          FROM active a
          LEFT JOIN provider_contract pc ON pc.客户编码 = a.上线客户编码
          LEFT JOIN provider_tier_v pt ON pt.客户编码 = a.上线客户编码
    """, conn, params=(city, period.start, period.end))

    if df.empty:
        return {
            '当期活跃服务商数': 0,
            '等级分布': {},
            '新激活': 0,
            '流失': 0,
            'top10_集中度': 0,
            'top10_列表': [],
        }

    # 等级分布（按 provider_contract 官方原始等级 V0-V5，未签约归「未签约」）
    _GRADE_LABEL = {f'v{i}服务商': f'V{i}' for i in range(6)}
    level_dist = (df['服务商等级'].map(lambda x: _GRADE_LABEL.get(x, x))
                  .value_counts().to_dict())

    # 新激活：当期有上线 且 DB 内 此前 ≥6 个月 都没上线（含全部历史）
    # 历史窗口为啥要 ≥6 个月：避免被 DB 数据起点误导
    # 例如 DB 从 2025-01 开始，查询 2025-02 的新激活时，
    # 「2025-01 之前从未上线」≈ DB 没有更早数据，会把所有 2025-02 上线的都当成新激活
    # 加 6 个月历史窗口意味着：仅当 DB 起点在当期起点之前至少 6 个月时，新激活数才有意义
    db_min_df = pd.read_sql(
        "SELECT MIN(上线年月) AS min FROM install_redpack_v WHERE 上线年月 IS NOT NULL",
        conn,
    )
    db_min = db_min_df.iloc[0]['min']
    period_start_p = pd.Period(period.start, freq='M')
    db_min_p = pd.Period(db_min, freq='M') if db_min else None
    enough_history = db_min_p is not None and (period_start_p - db_min_p).n >= 6

    if enough_history:
        new_df = pd.read_sql("""
            SELECT COUNT(DISTINCT a.上线客户编码) AS n
              FROM install_redpack_v a
             WHERE a.上线客户地市 = ?
               AND a.上线年月 BETWEEN ? AND ?
               AND a.上线客户编码 NOT IN (
                   SELECT DISTINCT 上线客户编码
                     FROM install_redpack_v
                    WHERE 上线客户地市 = ?
                      AND 上线年月 < ?
                      AND 上线客户编码 IS NOT NULL
               )
               AND a.上线客户编码 IS NOT NULL
        """, conn, params=(city, period.start, period.end, city, period.start))
        new_count = int(new_df.iloc[0]['n'])
        new_count_note = None
    else:
        # DB 历史不够 → 新激活数会被夸大，标 N/A
        new_count = None
        new_count_note = (
            f'DB 数据起点 {db_min}，距当期起点 {period.start} 不足 6 个月历史窗口，'
            f'"新激活"无法准确判定（早期月份会把已存在服务商误标为新激活），故置为 N/A。'
        )

    # 流失：环期内有上线但当期没上线（要算 mom 时段，由外面传入）
    # 这里只算当期视角，流失靠对比层算

    # top10 服务商集中度
    df_sorted = df.sort_values('当期货值', ascending=False)
    top10 = df_sorted.head(10)
    top10_sum = top10['当期货值'].sum()
    total_sum = df['当期货值'].sum()
    top10_pct = top10_sum / total_sum if total_sum > 0 else 0

    # TOP10 加马甲标识（join vest_account）
    vest_codes = set(pd.read_sql(
        "SELECT 服务商客户编码 FROM vest_account", conn,
    )['服务商客户编码'].astype(str))
    top10 = top10.assign(
        当期货值=lambda x: (x['当期货值'] / 10000).round(2),
        是马甲=lambda x: x['上线客户编码'].astype(str).isin(vest_codes),
    ).rename(columns={'当期货值': '当期货值_万'})
    top10['标识'] = top10['是马甲'].apply(lambda x: '🎭 马甲' if x else '')

    return {
        '当期活跃服务商数': len(df),
        '等级分布': level_dist,
        '新激活': new_count,
        '新激活_备注': new_count_note,
        'top10_集中度': round(top10_pct, 4),
        'top10_列表': top10[['上线客户名称', '当期货值_万', '服务商等级', '标识']].to_dict('records'),
        'top10_马甲数': int(top10['是马甲'].sum()),
    }


def query_provider_lifecycle(conn, city: str, periods: dict) -> dict:
    """服务商生命周期四分类（基于安装红包扫码上线记录）

    分类定义：
      - 持续活跃：当期+环期 都有上线
      - 新激活：当期上线 且 当期起点前 ≥6 个月历史窗口内**无该客户编码**上线
      - 沉睡：环期有上线但当期未上线（短期未活动）
      - 流失：当期+环期 都没上线 且 最近一次上线距当期起点 ≥3 个月

    名单返回前 20 条样本（用于报告引用具体名字）
    """
    cur = periods['current']
    mom = periods['mom']
    cur_start_p = pd.Period(cur.start, freq='M')

    def fetch_codes(start, end):
        df = pd.read_sql("""
            SELECT 上线客户编码, MAX(上线客户名称) AS 上线客户名称,
                   SUM(产品现有分销价) AS 货值
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 上线年月 BETWEEN ? AND ?
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
             GROUP BY 上线客户编码
        """, conn, params=(city, start, end))
        return df

    cur_df = fetch_codes(cur.start, cur.end)
    mom_df = fetch_codes(mom.start, mom.end)

    cur_codes = set(cur_df['上线客户编码'].astype(str))
    mom_codes = set(mom_df['上线客户编码'].astype(str))

    # 6 月历史窗口
    history_start = str(cur_start_p - 6)
    history_end = str(cur_start_p - 1)
    history_codes = set(pd.read_sql("""
        SELECT DISTINCT 上线客户编码 FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(city, history_start, history_end))['上线客户编码'].astype(str))

    # 「全部历史」客户 = 当期之前任何时刻有过上线
    full_history_codes = set(pd.read_sql("""
        SELECT DISTINCT 上线客户编码 FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 < ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(city, cur.start))['上线客户编码'].astype(str))

    # 当期起点前 3 个月内有上线 = 不算流失
    recent_3m_start = str(cur_start_p - 3)
    recent_3m_end = str(cur_start_p - 1)
    recent_3m_codes = set(pd.read_sql("""
        SELECT DISTINCT 上线客户编码 FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
    """, conn, params=(city, recent_3m_start, recent_3m_end))['上线客户编码'].astype(str))

    # 分类
    持续活跃 = cur_codes & mom_codes
    新激活 = cur_codes - history_codes - mom_codes  # 当期有 + 历史窗口无 + 环期无
    沉睡 = mom_codes - cur_codes  # 环期有 + 当期无
    # 流失 = 全部历史里有 + 当期/环期没 + 近 3 月也没
    流失 = (full_history_codes - cur_codes - mom_codes) - recent_3m_codes

    # 名单（用客户名称）
    name_map = dict(zip(cur_df['上线客户编码'].astype(str), cur_df['上线客户名称'].astype(str)))
    name_map.update(dict(zip(mom_df['上线客户编码'].astype(str), mom_df['上线客户名称'].astype(str))))

    # 沉睡列表带「环期货值」让用户能看到价值
    mom_value_map = dict(zip(mom_df['上线客户编码'].astype(str), mom_df['货值']))
    沉睡_list = sorted(沉睡, key=lambda c: -mom_value_map.get(c, 0))[:10]
    沉睡_list_named = [
        {'客户编码': c, '客户名称': name_map.get(c, '?'),
         '环期货值_万': round((mom_value_map.get(c) or 0) / 10000, 2)}
        for c in 沉睡_list
    ]

    新激活_list = sorted(新激活)[:10]
    新激活_list_named = [
        {'客户编码': c, '客户名称': name_map.get(c, '?')}
        for c in 新激活_list
    ]

    历史窗口够长 = (cur_start_p - pd.Period(
        pd.read_sql("SELECT MIN(上线年月) AS m FROM install_redpack_v WHERE 上线年月 IS NOT NULL",
                    conn).iloc[0]['m'], freq='M')).n >= 6

    return {
        '持续活跃数': len(持续活跃),
        '新激活数': len(新激活) if 历史窗口够长 else None,
        '沉睡数': len(沉睡),
        '流失数': len(流失),
        '当期活跃总数': len(cur_codes),
        '名单_新激活_top10': 新激活_list_named if 历史窗口够长 else [],
        '名单_沉睡_top10_按环期货值': 沉睡_list_named,
        '_新激活备注': None if 历史窗口够长 else
                       f'DB 历史窗口不足 6 个月（最早 {pd.read_sql("SELECT MIN(上线年月) AS m FROM install_redpack_v", conn).iloc[0]["m"]}），"新激活"无法准确判定',
    }


def query_keypoint_unvisited(conn, city: str, period: Period, top_k: int = 15) -> dict:
    """重点客户当期未跑动名单

    重点客户定义：按 install_redpack_v 上线累计金额排「货值档」（公司 KPI 标准，非服务商等级）
    - V4: ≥ ¥30,000（货值档）
    - V3: ¥10,000 - 30,000（货值档）
    ⚠️ 这里 V4/V3 是「货值档」（全历史累计货值分层），不是 provider_contract 官方服务商等级。

    当期未跑动：当期内 visit_record_v 中拜访该客户编码次数 = 0

    马甲标识：joined vest_account
    """
    df = pd.read_sql("""
        WITH provider_value AS (
            SELECT 上线客户编码 AS 客户编码,
                   MAX(上线客户名称) AS 上线客户名称,
                   MAX(上线客户区县) AS 上线客户区县,
                   SUM(产品现有分销价) AS 累计货值
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
             GROUP BY 上线客户编码
            HAVING SUM(产品现有分销价) >= 10000
        ),
        current_visits AS (
            SELECT 客户编码, COUNT(*) AS 当期拜访次数
              FROM visit_record_v
             WHERE 拜访客户城市 = ?
               AND 拜访年月 BETWEEN ? AND ?
               AND _打卡异常无效 = 0 AND _真异常打卡 = 0
             GROUP BY 客户编码
        )
        SELECT pv.客户编码,
               pv.上线客户名称,
               pv.上线客户区县,
               ROUND(pv.累计货值/10000, 2) AS 累计货值_万,
               COALESCE(cv.当期拜访次数, 0) AS 当期拜访次数,
               -- 货值档：仅 V4/V3 两档(本节业务只关心顶部货值档位,其他归 NULL 在下游 filter 掉)
               -- 阈值参照 _metrics_rfm.TIER_V4_FLOOR / TIER_V3_FLOOR；非服务商等级
               CASE WHEN pv.累计货值 >= 30000 THEN 'V4'
                    WHEN pv.累计货值 >= 10000 THEN 'V3'
               END AS 货值档,
               EXISTS (SELECT 1 FROM vest_account va WHERE va.服务商客户编码 = pv.客户编码) AS 是马甲
          FROM provider_value pv
          LEFT JOIN current_visits cv ON cv.客户编码 = pv.客户编码
         ORDER BY pv.累计货值 DESC
    """, conn, params=(city, city, period.start, period.end))

    if df.empty:
        return {'未跑动数': 0, '未跑动总货值_万': 0, '名单': []}

    # 未跑动 = 当期拜访次数 = 0
    unvisited = df[df['当期拜访次数'] == 0].copy()

    # 加马甲标记 emoji
    unvisited['标识'] = unvisited['是马甲'].apply(lambda x: '🎭马甲' if x else '')

    # 按货值档分类
    v4_unvisited = unvisited[unvisited['货值档'] == 'V4']
    v3_unvisited = unvisited[unvisited['货值档'] == 'V3']

    return {
        '重点客户总数_V4': int((df['货值档'] == 'V4').sum()),
        '重点客户总数_V3': int((df['货值档'] == 'V3').sum()),
        '未跑动数_V4': len(v4_unvisited),
        '未跑动数_V3': len(v3_unvisited),
        '未跑动总数': len(unvisited),
        '未跑动总货值_万': round(float(unvisited['累计货值_万'].sum()), 1),
        '名单_top15': unvisited.head(top_k)[
            ['客户编码', '上线客户名称', '上线客户区县', '货值档', '累计货值_万', '标识']
        ].to_dict('records'),
    }


def query_redpack_roi(conn, city: str, period: Period, top_k: int = 10) -> dict:
    """红包 ROI 偏离度分析

    全省 ROI = SUM(中奖金额) / SUM(产品现有分销价)
    服务商 ROI = 该服务商红包奖金总和 / 该服务商上线总和
    偏离度 = 服务商 ROI / 全省 ROI

    > 1.5: 红包发了多于行业平均比例，但没换来上线 → 业务员跟进
    < 0.5: 服务商没享受到红包福利 → 忠诚度风险
    """
    # 全省 ROI（用整个 install_redpack_v 表）
    prov_df = pd.read_sql("""
        SELECT COALESCE(SUM(中奖金额), 0) AS 红包总额,
               COALESCE(SUM(产品现有分销价), 0) AS 上线总额
          FROM install_redpack_v
         WHERE 上线年月 BETWEEN ? AND ?
    """, conn, params=(period.start, period.end))
    prov_red = float(prov_df.iloc[0]['红包总额'])
    prov_sale = float(prov_df.iloc[0]['上线总额'])
    prov_roi = prov_red / prov_sale if prov_sale > 0 else None

    # 城市内每个服务商的 ROI
    sp_df = pd.read_sql("""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 上线客户名称,
               MAX(上线客户区县) AS 区县,
               SUM(中奖金额) AS 红包额,
               SUM(产品现有分销价) AS 上线额,
               COUNT(*) AS 上线笔数
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码
        HAVING SUM(产品现有分销价) >= 1000   -- 过滤太小的服务商
    """, conn, params=(city, period.start, period.end))

    if sp_df.empty or prov_roi is None or prov_roi == 0:
        return {
            '全省红包ROI': prov_roi,
            '当期红包总额_万': round(prov_red / 10000, 2),
            '当期上线总额_万': round(prov_sale / 10000, 2),
            '高偏离名单_top10': [],
            '低偏离名单_top10': [],
        }

    sp_df['服务商ROI'] = sp_df['红包额'] / sp_df['上线额']
    sp_df['偏离度'] = sp_df['服务商ROI'] / prov_roi
    sp_df['红包额_元'] = sp_df['红包额'].round(2)
    sp_df['上线额_万'] = (sp_df['上线额'] / 10000).round(2)

    # 高偏离（>1.5）：红包发的多但没换来等比例上线 → 跟进
    high = sp_df[sp_df['偏离度'] > 1.5].copy()
    high = high.sort_values('偏离度', ascending=False).head(top_k)
    high_records = high[[
        '客户编码', '上线客户名称', '区县', '红包额_元', '上线额_万', '服务商ROI', '偏离度'
    ]].round({'服务商ROI': 4, '偏离度': 2}).to_dict('records')

    # 低偏离（<0.5）：没享受到福利 → 忠诚度风险
    low = sp_df[(sp_df['偏离度'] < 0.5) & (sp_df['上线额'] >= 10000)].copy()  # 只看 V2+
    low = low.sort_values('上线额', ascending=False).head(top_k)
    low_records = low[[
        '客户编码', '上线客户名称', '区县', '红包额_元', '上线额_万', '服务商ROI', '偏离度'
    ]].round({'服务商ROI': 4, '偏离度': 2}).to_dict('records')

    return {
        '全省红包ROI': round(prov_roi, 4),
        '当期红包总额_万': round(prov_red / 10000, 2),
        '当期上线总额_万': round(prov_sale / 10000, 2),
        '高偏离服务商数': len(sp_df[sp_df['偏离度'] > 1.5]),
        '低偏离服务商数': len(sp_df[sp_df['偏离度'] < 0.5]),
        '高偏离名单_top10': high_records,
        '低偏离名单_top10': low_records,
    }


def query_visit_efficiency(conn, city: str, period: Period) -> dict:
    """业务员空跑率（拜访后 30 天内无激活）+ 异常客户拜访

    空跑率定义：业务员当期对客户编码 X 拜访 ≥1 次
      → 看后续 30 天内 X 是否有任何 install_redpack_v 上线
      → 没有就算「空跑」

    异常拜访：拜访的客户编码出现在 vest_account（马甲）里
    """
    # 1. 业务员-客户 当期拜访矩阵
    visits_df = pd.read_sql("""
        SELECT 打卡人姓名 AS 业务员,
               _打卡方,
               客户编码,
               MIN(date(拜访时间)) AS 首次拜访日,
               COUNT(*) AS 拜访次数
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 打卡人姓名 IS NOT NULL AND 打卡人姓名 != ''
           AND 客户编码 IS NOT NULL AND 客户编码 != ''
         GROUP BY 打卡人姓名, _打卡方, 客户编码
    """, conn, params=(city, period.start, period.end))

    if visits_df.empty:
        return {
            '业务员_客户_拜访总数': 0,
            '空跑次数_总': 0,
            '空跑率': None,
            '业务员空跑率_top10': [],
            '马甲客户拜访次数': 0,
            '马甲客户拜访_业务员_top10': [],
        }

    # 2. 每个客户编码：拜访后 30 天窗口内是否有上线
    # 30 天内激活查询：每条拜访 → 在 install_redpack_v 里看 客户编码 + 上线时间 在 [拜访日, 拜访日+30] 内
    visits_df['首次拜访日'] = pd.to_datetime(visits_df['首次拜访日'])
    visits_df['窗口截止日'] = visits_df['首次拜访日'] + pd.Timedelta(days=30)

    # 拿所有相关客户编码的上线记录
    codes_str = ','.join(f"'{c}'" for c in visits_df['客户编码'].unique() if c)
    if codes_str:
        all_act = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码, 上线时间
              FROM install_redpack_v
             WHERE 上线客户编码 IN ({codes_str})
        """, conn)
        all_act['上线时间'] = pd.to_datetime(all_act['上线时间'])

        def has_activation(row):
            return any(
                (row['首次拜访日'] <= t <= row['窗口截止日'])
                for t in all_act[all_act['客户编码'] == row['客户编码']]['上线时间']
            )

        visits_df['30天激活'] = visits_df.apply(has_activation, axis=1)
    else:
        visits_df['30天激活'] = False

    # 3. 业务员空跑统计
    visits_df['是空跑'] = ~visits_df['30天激活']

    by_sp = visits_df.groupby(['业务员', '_打卡方']).agg(
        拜访客户数=('客户编码', 'nunique'),
        空跑数=('是空跑', 'sum'),
    ).reset_index()
    by_sp['空跑率'] = (by_sp['空跑数'] / by_sp['拜访客户数']).round(3)
    by_sp_top = by_sp.sort_values('空跑数', ascending=False).head(10)

    # 4. 拜访马甲客户次数
    vest_df = pd.read_sql(
        "SELECT 服务商客户编码 AS 客户编码 FROM vest_account",
        conn,
    )
    vest_codes = set(vest_df['客户编码'].astype(str))
    visits_df['是马甲'] = visits_df['客户编码'].astype(str).isin(vest_codes)
    vest_visits = visits_df[visits_df['是马甲']]
    by_sp_vest = (
        vest_visits.groupby(['业务员', '_打卡方'])
        .agg(马甲拜访次数=('拜访次数', 'sum'),
             马甲客户数=('客户编码', 'nunique'))
        .reset_index()
        .sort_values('马甲拜访次数', ascending=False)
        .head(10)
    )

    total = len(visits_df)
    empty_total = int(visits_df['是空跑'].sum())

    return {
        '业务员_客户_拜访总数': total,
        '空跑次数_总': empty_total,
        '空跑率': round(empty_total / total, 4) if total else None,
        '业务员空跑率_top10': by_sp_top[
            ['业务员', '_打卡方', '拜访客户数', '空跑数', '空跑率']
        ].to_dict('records'),
        '马甲客户拜访次数': int(vest_visits['拜访次数'].sum()) if not vest_visits.empty else 0,
        '马甲客户拜访_业务员_top10': by_sp_vest.to_dict('records'),
    }


def query_salesperson_scope(conn, city: str) -> dict:
    """业务员责任范围（用于工作计划派活时按 scope 分配责任方）

    返回：{
        '业务员_列表': [{业务员, 负责区县_list, 负责代理商_list}],
        '区县_to_业务员': {区县: [业务员名]},
        '代理商_to_业务员': {代理商: [业务员名]},
    }
    """
    df = pd.read_sql("""
        SELECT 区县, 代理商, 业务员
          FROM salesperson_scope
         WHERE 市 = ? AND 业务员 != ''
    """, conn, params=(city,))

    if df.empty:
        return {
            '业务员_列表': [],
            '区县_to_业务员': {},
            '代理商_to_业务员': {},
            '_备注': f'{city} 在 salesperson_scope 表中无数据（导入「责任区县管理.xlsx」）',
        }

    业务员_列表 = []
    for sp, sub in df.groupby('业务员'):
        业务员_列表.append({
            '业务员': sp,
            '负责区县': sorted(set(sub['区县'].dropna().tolist())),
            '负责代理商': sorted(set(sub['代理商'].dropna().tolist())),
        })

    区县_to = {}
    for d, sub in df.groupby('区县'):
        区县_to[d] = sorted(set(sub['业务员'].tolist()))

    代理商_to = {}
    for da, sub in df.groupby('代理商'):
        代理商_to[da] = sorted(set(sub['业务员'].tolist()))

    return {
        '业务员_列表': 业务员_列表,
        '区县_to_业务员': 区县_to,
        '代理商_to_业务员': 代理商_to,
    }


def query_dispatch_alerts(conn, city: str, period: Period) -> dict:
    """智能派单台预警 + 实际跟进率（复算 page 02 派单逻辑）

    简化版派单规则（跟 page 02 一致）：
    1. 🚨 紧急派单：V4/V3 服务商，最后一次上线距当期起点 > 30 天
    2. ⚠️ 漏跑预警：V4/V3 服务商，最后一次拜访距当期起点 > 30 天
    3. 🆕 培育新人：当期前 6 个月内新激活的服务商（≥¥1000 但 < ¥10000）

    实际跟进率 = 当期内被拜访过的派单客户数 / 派单总数
    """
    cur_start = period.start  # 'YYYY-MM'
    cur_start_p = pd.Period(cur_start, freq='M')
    threshold_30d = str(cur_start_p - 1)  # 当期前 1 个月
    new_window_start = str(cur_start_p - 6)

    # 历史服务商画像（当期前的累计）
    pv_df = pd.read_sql("""
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               SUM(产品现有分销价) AS 累计货值,
               MAX(上线年月) AS 最后上线月,
               MIN(上线年月) AS 首次上线月
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 < ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码
    """, conn, params=(city, period.start))

    if pv_df.empty:
        return {
            '紧急派单_数': 0, '漏跑预警_数': 0, '培育新人_数': 0,
            '总派单数': 0, '当期实际跟进数': 0, '跟进率': None,
            '详情_紧急派单_top10': [], '详情_漏跑预警_top10': [], '详情_培育新人_top10': [],
        }

    # 货值档 — 全历史累计货值分层，统一从 _metrics_rfm.tier_of（非 provider_contract 服务商等级）
    pv_df['货值档'] = pv_df['累计货值'].apply(tier_of)

    # 当期前 30 天的拜访（用「上一个月」近似）
    last_visit_df = pd.read_sql("""
        SELECT 客户编码,
               MAX(date(拜访时间)) AS 最后拜访日
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 < ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 客户编码 IS NOT NULL AND 客户编码 != ''
         GROUP BY 客户编码
    """, conn, params=(city, period.start))
    pv_df = pv_df.merge(last_visit_df, on='客户编码', how='left')

    # 派单逻辑（按货值档找重点客户）
    # 紧急派单：货值档 V4/V3 + 最后上线月 <= 当期前 1 个月（即过去 1 月没新上线）
    紧急 = pv_df[
        pv_df['货值档'].isin(['V4', 'V3'])
        & (pv_df['最后上线月'] <= threshold_30d)
    ].copy()
    紧急['派单类型'] = '🚨 紧急派单'

    # 漏跑预警：货值档 V4/V3 + 最后拜访日 < 当期前 30 天 OR 从未拜访
    pv_df['_最后拜访_日期'] = pd.to_datetime(pv_df['最后拜访日'], errors='coerce')
    threshold_date = pd.Timestamp(cur_start_p.start_time) - pd.Timedelta(days=30)
    漏跑 = pv_df[
        pv_df['货值档'].isin(['V4', 'V3'])
        & (pv_df['_最后拜访_日期'].isna() | (pv_df['_最后拜访_日期'] < threshold_date))
    ].copy()
    漏跑['派单类型'] = '⚠️ 漏跑预警'

    # 培育新人：当期前 6 个月内首次上线 + 累计货值 [1000, 10000)
    培育 = pv_df[
        (pv_df['首次上线月'] >= new_window_start)
        & (pv_df['累计货值'] >= 1000)
        & (pv_df['累计货值'] < 10000)
    ].copy()
    培育['派单类型'] = '🆕 培育新人'

    # 总派单集合（同一客户可能在多个类型中，去重）
    all_dispatched = (
        pd.concat([紧急[['客户编码', '客户名称', '累计货值', '货值档', '派单类型']],
                   漏跑[['客户编码', '客户名称', '累计货值', '货值档', '派单类型']],
                   培育[['客户编码', '客户名称', '累计货值', '货值档', '派单类型']]],
                  ignore_index=True)
    )
    unique_dispatched = set(all_dispatched['客户编码'].astype(str))

    # 当期内被实际拜访的派单客户数
    cur_visited = set(pd.read_sql("""
        SELECT DISTINCT 客户编码
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 客户编码 IS NOT NULL AND 客户编码 != ''
    """, conn, params=(city, period.start, period.end))['客户编码'].astype(str))

    实际跟进 = unique_dispatched & cur_visited

    def fmt_list(df, k=10):
        return [
            {'客户名称': r['客户名称'], '货值档': r['货值档'],
             '累计货值_万': round(r['累计货值'] / 10000, 2)}
            for _, r in df.sort_values('累计货值', ascending=False).head(k).iterrows()
        ]

    return {
        '紧急派单_数': len(紧急),
        '漏跑预警_数': len(漏跑),
        '培育新人_数': len(培育),
        '总派单数_去重': len(unique_dispatched),
        '当期实际跟进数': len(实际跟进),
        '跟进率': round(len(实际跟进) / len(unique_dispatched), 4) if unique_dispatched else None,
        '详情_紧急派单_top10': fmt_list(紧急),
        '详情_漏跑预警_top10': fmt_list(漏跑),
        '详情_培育新人_top10': fmt_list(培育),
    }


def query_visit_summary(conn, city: str, period: Period) -> dict:
    """1.5 业务员跑动"""
    # 拜访客户城市 字段
    df = pd.read_sql("""
        SELECT
            SUM(CASE WHEN _打卡异常无效=0 AND _真异常打卡=0 THEN 1 ELSE 0 END) AS 有效打卡,
            SUM(CASE WHEN _是否大华=1 AND _打卡异常无效=0 AND _真异常打卡=0 THEN 1 ELSE 0 END) AS 大华_有效,
            SUM(CASE WHEN _是否大华=0 AND _打卡异常无效=0 AND _真异常打卡=0 THEN 1 ELSE 0 END) AS 代理商_有效,
            SUM(_打卡异常无效) AS 数据问题打卡,
            SUM(_真异常打卡) AS 真异常打卡,
            COUNT(*) AS 总记录
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
    """, conn, params=(city, period.start, period.end))

    summary = df.iloc[0].fillna(0).astype(int).to_dict()

    # top 拜访业务员（前 10）
    top_df = pd.read_sql("""
        SELECT 打卡人姓名,
               _打卡方,
               COUNT(*) AS 有效打卡次数,
               COUNT(DISTINCT 客户编码) AS 拜访客户数
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0
           AND _真异常打卡 = 0
           AND 打卡人姓名 IS NOT NULL
           AND 打卡人姓名 != ''
         GROUP BY 打卡人姓名, _打卡方
         ORDER BY 有效打卡次数 DESC
         LIMIT 10
    """, conn, params=(city, period.start, period.end))

    summary['top10_业务员'] = top_df.to_dict('records')
    summary['活跃业务员数'] = int(pd.read_sql("""
        SELECT COUNT(DISTINCT 打卡人姓名) AS n
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 打卡人姓名 IS NOT NULL AND 打卡人姓名 != ''
    """, conn, params=(city, period.start, period.end)).iloc[0]['n'])

    return summary


# ══════════════════════════════════════════════
# v3 新增：5 个查询（章 1.3 / 2 / 3 / 4.3 / 5.3）
# ══════════════════════════════════════════════

def query_activation_status(conn, city: str, period: Period, top_k: int = 10) -> dict:
    """章 1.3 服务商激活情况

    应激活 = 该城市 V2+ 历史客户（累计货值 ≥ ¥1,000，货值档口径）
    实际激活 = 当期内有上线
    漏激活 = 应激活但当期无上线（按累计货值降序取 top_k）
    ⚠️ 这里的「货值档」(V2+ 等) 是全历史累计货值分层，非 provider_contract 服务商等级。
    """
    df = pd.read_sql("""
        WITH provider_value AS (
            SELECT 上线客户编码 AS 客户编码,
                   MAX(上线客户名称) AS 客户名称,
                   MAX(上线客户区县) AS 区县,
                   MAX(所属一级客户) AS 所属一级客户,
                   SUM(产品现有分销价) AS 累计货值,
                   MAX(上线年月) AS 最近上线年月
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
             GROUP BY 上线客户编码
            HAVING SUM(产品现有分销价) >= 1000
        ),
        current_act AS (
            SELECT 上线客户编码 AS 客户编码,
                   SUM(产品现有分销价) AS 当期货值
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 上线年月 BETWEEN ? AND ?
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
             GROUP BY 上线客户编码
        )
        SELECT pv.客户编码, pv.客户名称, pv.区县, pv.所属一级客户,
               ROUND(pv.累计货值/10000, 2) AS 累计货值_万,
               """ + tier_case_sql('pv.累计货值') + """ AS 货值档,
               pv.最近上线年月,
               COALESCE(ca.当期货值, 0) AS 当期货值,
               EXISTS (SELECT 1 FROM vest_account va WHERE va.服务商客户编码 = pv.客户编码) AS 是马甲
          FROM provider_value pv
          LEFT JOIN current_act ca ON ca.客户编码 = pv.客户编码
    """, conn, params=(city, city, period.start, period.end))

    if df.empty:
        return {
            '应激活_总数': 0, '实际激活_数': 0, '漏激活_数': 0,
            '激活率': None, '漏激活名单_top10': [],
        }

    应激活 = len(df)
    实际激活 = int((df['当期货值'] > 0).sum())
    漏激活 = 应激活 - 实际激活

    miss = df[df['当期货值'] == 0].copy()
    miss['标识'] = miss['是马甲'].apply(lambda x: '🎭' if x else '')
    miss_list = miss.sort_values('累计货值_万', ascending=False).head(top_k)[
        ['客户编码', '客户名称', '区县', '所属一级客户', '货值档', '累计货值_万', '最近上线年月', '标识']
    ].to_dict('records')

    # 按货值档看漏激活
    by_tier = {
        lvl: {
            '应激活': int((df['货值档'] == lvl).sum()),
            '漏激活': int(((df['货值档'] == lvl) & (df['当期货值'] == 0)).sum()),
        }
        for lvl in ['V4', 'V3', 'V2', '已激活']
    }

    return {
        '应激活_总数': 应激活,
        '实际激活_数': 实际激活,
        '漏激活_数': 漏激活,
        '激活率': round(实际激活 / 应激活, 4) if 应激活 > 0 else None,
        '按货值档': by_tier,
        '漏激活名单_top10': miss_list,
    }


# NINE_CELL_LABEL 统一从 _metrics_rfm 导入(top of file)


def _tier_by_m(m_cur: float) -> str:
    """按累计货值分等级 (M 维度) — 转调 _metrics_rfm.tier_of"""
    return tier_of(m_cur)


def query_rfm_distribution(conn, city: str, period: Period) -> dict:
    """章 2 RFM 客户分布

    R/F 阈值同 _visit_rfm_eval：
      R: ≤30🟢 / 30-60🟡 / >60🔴
      F: ≥12🟢 / 3-11🟡 / <3🔴
    M 维度用服务商层级替代：
      V4≥30000 / V3 10000-30000 / V2 1000-10000 / 已激活 0-1000 / v0=0

    输出：
      - dist_整体：R × F 9 宫格（全体客户）
      - dist_按等级：{V4: [9宫格], V3: [9宫格], V2: [9宫格], 已激活: [9宫格]}
      - count_v0：v0 客户数（无 R/F 数据）
      - 9宫格定义：每格的业务标签
      - 增长_top10：本期 vs 上期 M 增长 >+30%
      - 下滑_top10：M 下滑 <-30% 或 R 从 ≤60 滑到 >60
      - 健康度流转：期初 R 状态 → 期末 R 状态
    """
    period_end_dt = pd.Timestamp(period.end + '-01') + pd.offsets.MonthEnd(0)
    period_start_dt = pd.Timestamp(period.start + '-01')

    # 取该城市所有有上线历史的客户编码
    all_codes = pd.read_sql("""
        SELECT DISTINCT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户区县) AS 区县
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
         GROUP BY 上线客户编码
    """, conn, params=(city,))
    if all_codes.empty:
        return {
            'dist_九宫格': {}, '增长_top10': [], '下滑_top10': [],
            '健康度流转': {},
        }

    # 一次性取所有上线
    codes_in = "','".join(all_codes['客户编码'].astype(str))
    acts = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码, 上线时间, 产品现有分销价
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in}')
    """, conn)
    acts['上线时间'] = pd.to_datetime(acts['上线时间'])
    acts_grouped = {c: g for c, g in acts.groupby('客户编码')}

    # 算每客户 期末 RFM + 期初 R
    rows = []
    for _, c in all_codes.iterrows():
        code = str(c['客户编码'])
        g = acts_grouped.get(code)
        if g is None or g.empty:
            continue

        # 期末
        before = g[g['上线时间'] < period_end_dt]
        if before.empty:
            continue
        R_end = (period_end_dt - before['上线时间'].max()).days
        win = period_end_dt - timedelta(365)
        prev_win = period_end_dt - timedelta(730)
        cur_12m = before[before['上线时间'] >= win]
        prev_12m = before[(before['上线时间'] >= prev_win) & (before['上线时间'] < win)]
        F = cur_12m['上线时间'].dt.date.nunique()
        M_cur = float(cur_12m['产品现有分销价'].sum())
        M_prev = float(prev_12m['产品现有分销价'].sum())
        M_growth = (M_cur - M_prev) / M_prev if M_prev > 0 else None

        # 期初 R
        before_start = g[g['上线时间'] < period_start_dt]
        R_start = (period_start_dt - before_start['上线时间'].max()).days if not before_start.empty else None

        rows.append({
            '客户编码': code,
            '客户名称': c['客户名称'],
            '区县': c['区县'],
            'R_end': R_end, 'F_12mo': F,
            'M_cur_元': M_cur, 'M_prev_元': M_prev,
            'M_growth': M_growth,
            'R_start': R_start,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return {
            'dist_整体': [], 'dist_按等级': {}, 'count_v0': 0,
            '增长_top10': [], '下滑_top10': [], '健康度流转': {},
            '9宫格定义': [{'格子': f'{k[0]} × {k[1]}', '业务含义': v}
                          for k, v in NINE_CELL_LABEL.items()],
        }

    # 9 宫格 buckets — 统一从 _metrics_rfm
    from _metrics_rfm import r_bucket, f_bucket
    df['R档'] = df['R_end'].apply(r_bucket)
    df['F档'] = df['F_12mo'].apply(f_bucket)
    df['等级'] = df['M_cur_元'].apply(_tier_by_m)

    def build_9cell(sub_df):
        """给定子集，返回 9 宫格 records（已加业务标签）"""
        if sub_df.empty:
            return []
        agg = (sub_df.groupby(['R档', 'F档'])
                     .agg(客户数=('客户编码', 'count'),
                          累计货值_万=('M_cur_元', lambda x: round(x.sum()/10000, 2)))
                     .reset_index())
        agg['业务标签'] = agg.apply(
            lambda r: NINE_CELL_LABEL.get((r['R档'], r['F档']), '?'), axis=1,
        )
        return agg.to_dict('records')

    # 整体 9 宫格
    dist_全 = build_9cell(df[df['等级'] != 'v0'])

    # 按等级 9 宫格
    dist_by_tier = {}
    for tier in ['V4', 'V3', 'V2', '已激活']:
        sub = df[df['等级'] == tier]
        dist_by_tier[tier] = {
            '客户数': int(len(sub)),
            '累计货值_万': round(float(sub['M_cur_元'].sum()) / 10000, 2),
            '九宫格': build_9cell(sub),
        }
    # v0（无上线历史）：单算
    count_v0 = int((df['等级'] == 'v0').sum())

    # 增长 TOP 20（M_growth > 0.3 且 M_cur >= 1000）
    growers = df[(df['M_growth'].notna()) & (df['M_growth'] > 0.30) & (df['M_cur_元'] >= 1000)].copy()
    growers = growers.sort_values('M_growth', ascending=False).head(10)
    grow_list = [
        {
            '客户名称': r['客户名称'], '区县': r['区县'],
            '上期货值_万': round(r['M_prev_元']/10000, 2),
            '本期货值_万': round(r['M_cur_元']/10000, 2),
            '增长率': f"+{r['M_growth']*100:.0f}%",
            'R_end': int(r['R_end']), 'F_12mo': int(r['F_12mo']),
        }
        for _, r in growers.iterrows()
    ]

    # 下滑 TOP 20：M_growth < -0.30 或 R 从 ≤60 滑到 >60
    cond_m = (df['M_growth'].notna()) & (df['M_growth'] < -0.30)
    cond_r = (df['R_start'].notna()) & (df['R_start'] <= 60) & (df['R_end'] > 60)
    losers = df[cond_m | cond_r].copy()
    # 排序：先按 M 下滑严重度，再按 R 滑落幅度
    losers['_sort'] = losers['M_growth'].fillna(0).where(cond_m, -1.0)
    losers = losers.sort_values('_sort').head(10)
    lose_list = [
        {
            '客户名称': r['客户名称'], '区县': r['区县'],
            '上期货值_万': round(r['M_prev_元']/10000, 2),
            '本期货值_万': round(r['M_cur_元']/10000, 2),
            '增长率': f"{r['M_growth']*100:+.0f}%" if pd.notna(r['M_growth']) else 'N/A',
            'R_期初': int(r['R_start']) if pd.notna(r['R_start']) else None,
            'R_期末': int(r['R_end']),
            '问题': '；'.join(filter(None, [
                'M 下滑' if pd.notna(r['M_growth']) and r['M_growth'] < -0.30 else None,
                'R 滑落' if pd.notna(r['R_start']) and r['R_start'] <= 60 and r['R_end'] > 60 else None,
            ])),
        }
        for _, r in losers.iterrows()
    ]

    # 健康度流转：期初 R 状态 → 期末 R 状态
    def rs(r):
        if r is None or pd.isna(r): return '?'
        if r <= 30: return '🟢健康'
        if r <= 60: return '🟡预警'
        return '🔴恶化'

    df['期初_R状态'] = df['R_start'].apply(rs)
    df['期末_R状态'] = df['R_end'].apply(rs)
    flow = (df.groupby(['期初_R状态', '期末_R状态'])
              .size().reset_index(name='客户数'))
    flow_list = flow.to_dict('records')

    return {
        'dist_整体': dist_全,
        'dist_按等级': dist_by_tier,
        'count_v0': count_v0,
        '增长_top10': grow_list,
        '下滑_top10': lose_list,
        '健康度流转': flow_list,
        '9宫格定义': [{'格子': f'{k[0]} × {k[1]}', '业务含义': v}
                       for k, v in NINE_CELL_LABEL.items()],
        '客户总数': int(len(df)),
    }


def query_abnormal_providers(conn, city: str, period: Period, top_k: int = 10) -> dict:
    """章 2.6 异常服务商：🎭 马甲 / ☂️ 伞形 / ❌ 假签约

    定义（与 page 03 关联挖掘与假商治理 对齐，伞形采用简化版）：
      🎭 马甲：vest_account 表（人工维护）
      ☂️ 伞形：同 老板姓名 + 老板电话 注册 ≥ 2 家不同客户
                 （page 03 用团伙图算法，这里简化用 BOSS-PHONE 关联）
      ❌ 假签约：签约 60+ 天但累计上线 ≤ 2 台
    """
    # 数据基准日：用 install_redpack_v 最大上线日
    db_max_dt = pd.read_sql(
        "SELECT MAX(上线时间) AS d FROM install_redpack_v", conn,
    ).iloc[0]['d']

    # ─── 1. 🎭 马甲 ───
    vest_df = pd.read_sql("""
        SELECT v.服务商客户编码 AS 客户编码,
               v.服务商客户名称 AS 客户名称,
               v.对应一级 AS 所属一级,
               COALESCE(ROUND(rp.金额/10000, 2), 0) AS 累计货值_万,
               COALESCE(rp.台数, 0) AS 累计台数
          FROM vest_account v
          LEFT JOIN (
              SELECT 上线客户编码, SUM(产品现有分销价) AS 金额, COUNT(*) AS 台数
                FROM install_redpack_v
               GROUP BY 上线客户编码
          ) rp ON rp.上线客户编码 = v.服务商客户编码
         WHERE v.城市 = ?
         ORDER BY 累计货值_万 DESC
    """, conn, params=(city,))
    vest_total = int(len(vest_df))
    vest_top = vest_df.head(top_k).to_dict('records')

    # ─── 2. ☂️ 伞形（同 老板姓名 + 电话 注册 ≥2 家）───
    umb_df = pd.read_sql("""
        WITH grp AS (
            SELECT 老板姓名 AS 老板, 老板电话 AS 电话,
                   GROUP_CONCAT(客户编码, '|') AS codes,
                   GROUP_CONCAT(公司名称, '|') AS names,
                   COUNT(*) AS 客户数
              FROM provider_profile
             WHERE 地市 = ?
               AND 老板姓名 IS NOT NULL AND 老板姓名 != ''
               AND 老板电话 IS NOT NULL AND 老板电话 != ''
             GROUP BY 老板姓名, 老板电话
            HAVING COUNT(*) >= 2
        )
        SELECT * FROM grp
         ORDER BY 客户数 DESC
    """, conn, params=(city,))
    umb_total_groups = int(len(umb_df))
    umb_total_codes = int(umb_df['客户数'].sum()) if not umb_df.empty else 0

    # 给每个伞形组的客户算累计货值（用第一个客户编码代表，仅展示）
    umb_top_rows = []
    for _, r in umb_df.head(top_k).iterrows():
        codes = [c.strip() for c in (r['codes'] or '').split('|') if c.strip()]
        names = [n.strip() for n in (r['names'] or '').split('|') if n.strip()]
        # 累计货值（伞形组合计）
        amt_df = pd.read_sql(f"""
            SELECT COALESCE(SUM(产品现有分销价), 0) AS 万
              FROM install_redpack_v
             WHERE 上线客户编码 IN ('{"','".join(codes)}')
        """, conn) if codes else pd.DataFrame([{'万': 0}])
        amt = float(amt_df.iloc[0]['万']) / 10000
        umb_top_rows.append({
            '老板姓名': r['老板'], '电话': r['电话'][:3] + '****' + r['电话'][-3:] if r['电话'] and len(r['电话']) >= 7 else r['电话'],
            '客户数': int(r['客户数']),
            '客户清单': '、'.join(names[:5]) + ('…' if len(names) > 5 else ''),
            '伞形累计货值_万': round(amt, 2),
        })

    # ─── 3. ❌ 假签约（签约 60+ 天 但累计上线 ≤2 台）───
    fake_df = pd.read_sql("""
        WITH cust_amt AS (
            SELECT 上线客户编码 AS 客户编码,
                   COUNT(*) AS 上线台数,
                   ROUND(SUM(产品现有分销价)/10000, 2) AS 累计货值_万,
                   MAX(date(上线时间)) AS 最近上线
              FROM install_redpack_v
             GROUP BY 上线客户编码
        )
        SELECT pc.客户编码, pc.客户名称, pc.客户区县 AS 区县,
               date(pc.签约日期) AS 签约日,
               CAST(julianday(date(?)) - julianday(date(pc.签约日期)) AS INTEGER) AS 签约后天数,
               COALESCE(ca.上线台数, 0) AS 累计上线台数,
               COALESCE(ca.累计货值_万, 0) AS 累计货值_万,
               COALESCE(ca.最近上线, '从未') AS 最近上线
          FROM provider_contract pc
          LEFT JOIN cust_amt ca ON ca.客户编码 = pc.客户编码
         WHERE pc.客户城市 = ?
           AND pc.签约日期 IS NOT NULL
           AND date(pc.签约日期) <= date(?, '-60 day')
           AND COALESCE(ca.上线台数, 0) <= 2
         ORDER BY pc.签约日期 ASC
    """, conn, params=(db_max_dt, city, db_max_dt))
    fake_total = int(len(fake_df))
    fake_top = fake_df.head(top_k)[
        ['客户编码', '客户名称', '区县', '签约日', '签约后天数', '累计上线台数', '累计货值_万', '最近上线']
    ].to_dict('records')

    return {
        '马甲': {
            '总数': vest_total,
            '说明': '来源：vest_account 表（人工维护清单）',
            '名单_top10': vest_top[:10],
        },
        '伞形': {
            '老板组数': umb_total_groups,
            '涉及客户数': umb_total_codes,
            '说明': '判定：同老板姓名 + 同电话 注册 ≥2 家不同客户（page 03 用更严的图算法，这里简化）',
            '名单_top10': umb_top_rows[:10],
        },
        '假签约': {
            '总数': fake_total,
            '说明': '判定：签约日距数据基准日 ≥60 天 且 累计上线 ≤2 台',
            '基准日': db_max_dt,
            '名单_top10': fake_top[:10],
        },
    }


def query_salesperson_evaluation(
    conn, city: str, period: Period,
) -> dict:
    """章 3 + 5 业务员跑动评估（直接调 _visit_rfm_eval）

    顺手在此采集 highlights — 避免重跑 evaluate_salesperson

    输出：
      - 我司工作量：大华业务员 list
      - 代理商工作量：代理商业务员 list
      - 对比表：人均拜访 / 选择合理率 / 救援成功率 / 维护成功率 / 漏跑率
      - 5.1 我司工作量：list[{业务员, 拜访客户数, 覆盖区县, 救援成功, 维护成功, 新客探访, 严重漏跑}]
      - 5.2 代理商工作量：同上
      - _highlights_raw: list[{业务员, 所属, 客户名称, 区县, 首拜访日, 分类, 拉动SO_万}]
        给 query_highlights 用，避免重复跑 evaluate_salesperson
    """
    # 延迟导入避免循环
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    from _visit_rfm_eval import list_all_salespeople, evaluate_salesperson

    sps = list_all_salespeople(conn, city, period.start, period.end)
    if not sps:
        return {
            '对比表': {}, '我司工作量': [], '代理商工作量': [],
            '_highlights_raw': [],
        }

    # 一次性查 SP 所属
    pf_df = pd.read_sql("""
        SELECT 打卡人姓名, MAX(_打卡方) AS _打卡方
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
         GROUP BY 打卡人姓名
    """, conn, params=(city, period.start, period.end))
    pf_map = dict(zip(pf_df['打卡人姓名'], pf_df['_打卡方']))

    # 跑每个 SP，同时采集 highlights_raw（避免重跑 evaluate）
    rank_rows = []
    highlights_raw = []
    for sp in sps:
        try:
            r = evaluate_salesperson(conn, city, sp, period.start, period.end)
        except Exception:
            continue
        s = r['summary']
        owned = pf_map.get(sp, '?')
        belong = '大华' if '大华' in (owned or '') else '代理商'

        rank_rows.append({
            '业务员': sp,
            '所属': belong,
            '负责区县': '、'.join(sorted(r['sp_districts'])) if r['sp_districts'] else '—',
            '拜访客户数': s['visits_total'],
            'sel_代理商办公室': s['sel_代理商办公室'],
            'sel_主动救援': s['sel_主动救援'],
            'sel_健康维护': s['sel_健康维护'],
            'sel_新客探访': s['sel_新客探访'],
            'sel_马甲拜访': s['sel_马甲拜访'],
            '🚨 严重漏跑': s['missed_严重'],
            '⚠️ 一般漏跑': s['missed_一般'],
            '✅ 救援成功': s['res_救援成功'],
            '❌ 救援失败': s['res_救援失败'],
            '🟢 正常':     s['res_正常'],
            '🚨 流失':     s['res_流失'],
            '⏳ 待观察':   s['eval_待观察'],
        })

        # 同时采集 highlights:救援成功 + 正常 客户的拉动 SO
        from _metrics_rfm import LABEL_RESCUE_SUCCESS, LABEL_NORMAL
        v = r['visits_df']
        if v.empty:
            continue
        hits = v[v['分类'].isin([LABEL_RESCUE_SUCCESS, LABEL_NORMAL])]
        for _, h in hits.iterrows():
            code = str(h['客户编码'])
            fv = h['首次拜访日']
            fv_str = fv.strftime('%Y-%m-%d')
            df_act = pd.read_sql("""
                SELECT COALESCE(SUM(产品现有分销价), 0) AS 拉动SO
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                   AND 上线时间 > ?
                   AND 上线时间 <= date(?, '+30 day')
            """, conn, params=(code, fv.strftime('%Y-%m-%d %H:%M:%S'), fv_str))
            so = float(df_act.iloc[0]['拉动SO'] or 0)
            if so <= 0:
                continue
            highlights_raw.append({
                '业务员': sp, '所属': belong,
                '客户名称': h['客户名称'], '区县': h['区县'],
                '首拜访日': fv_str, '分类': h['分类'],
                '拉动SO_万': round(so / 10000, 2),
            })

    rank_df = pd.DataFrame(rank_rows)
    if rank_df.empty:
        return {
            '对比表': {}, '我司工作量': [], '代理商工作量': [],
            '_highlights_raw': [],
        }

    dahua = rank_df[rank_df['所属'].str.contains('大华', na=False)].copy()
    agent = rank_df[~rank_df['所属'].str.contains('大华', na=False)].copy()

    def stats(group_df, has_scope: bool):
        if group_df.empty:
            return {
                '业务员数': 0, '总拜访客户数': 0, '人均拜访': 0,
                '选择合理率': None, '救援成功率': None,
                '维护成功率': None, '漏跑总数': None,
                '严重漏跑': None, '一般漏跑': None,
                '漏跑备注': '无 scope，不统计' if not has_scope else None,
            }
        n = len(group_df)
        v = int(group_df['拜访客户数'].sum())
        sel_ok = int((group_df['sel_主动救援'] + group_df['sel_健康维护'] + group_df['sel_新客探访']).sum())
        sel_bad = int(group_df['sel_代理商办公室'].sum())
        sel_total = sel_ok + sel_bad
        rescue_attempt = int(group_df['sel_主动救援'].sum())
        rescue_success = int(group_df['✅ 救援成功'].sum())
        maint_attempt = int(group_df['sel_健康维护'].sum())
        maint_success = int(group_df['🟢 正常'].sum())
        return {
            '业务员数': n, '总拜访客户数': v,
            '人均拜访': round(v / n, 1) if n else 0,
            '选择合理率': round(sel_ok / sel_total, 4) if sel_total else None,
            '救援成功率': round(rescue_success / rescue_attempt, 4) if rescue_attempt else None,
            '维护正常率': round(maint_success / maint_attempt, 4) if maint_attempt else None,
            '严重漏跑': int(group_df['🚨 严重漏跑'].sum()) if has_scope else None,
            '一般漏跑': int(group_df['⚠️ 一般漏跑'].sum()) if has_scope else None,
            '漏跑总数': int(group_df['🚨 严重漏跑'].sum() + group_df['⚠️ 一般漏跑'].sum()) if has_scope else None,
            '漏跑备注': None if has_scope else '代理商无 scope 定义,漏跑统计不适用',
        }

    def workload(group_df, has_scope: bool):
        rows = []
        for _, r in group_df.iterrows():
            rows.append({
                '业务员': r['业务员'],
                '所属': r['所属'],
                '负责区县': r['负责区县'],
                '拜访客户数': int(r['拜访客户数']),
                '✅ 救援成功': int(r['✅ 救援成功']),
                '❌ 救援失败': int(r['❌ 救援失败']),
                '🟢 正常':     int(r['🟢 正常']),
                '🚨 流失':     int(r['🚨 流失']),
                '🌱 新客探访': int(r['sel_新客探访']),
                '🚨 严重漏跑': int(r['🚨 严重漏跑']) if has_scope else None,
                '⚠️ 一般漏跑': int(r['⚠️ 一般漏跑']) if has_scope else None,
                '⚠️ 代理商办公室': int(r['sel_代理商办公室']),
            })
        return rows

    # 我司明细 == 我司工作量（之前重复存了 2 份）→ 现在只存 1 份
    dahua_workload = workload(dahua, has_scope=True)
    agent_workload = workload(agent, has_scope=False)
    return {
        '对比表': {
            '我司': stats(dahua, has_scope=True),
            '代理商': stats(agent, has_scope=False),
        },
        '我司工作量': dahua_workload,
        '代理商工作量': agent_workload,
        '_highlights_raw': highlights_raw,
    }


def query_product_redpack_roi(conn, city: str, period: Period, top_k: int = 10) -> dict:
    """章 4.3 产品红包 ROI

    按产品维度看：红包额 / 上线 SO / ROI
    """
    df = pd.read_sql("""
        SELECT COALESCE(产品名称, '未知产品') AS 产品,
               COUNT(*) AS 笔数,
               SUM(COALESCE(中奖金额, 0)) AS 红包额,
               SUM(COALESCE(产品现有分销价, 0)) AS 上线额
          FROM install_redpack_v
         WHERE 上线客户地市 = ?
           AND 上线年月 BETWEEN ? AND ?
         GROUP BY 产品名称
        HAVING SUM(产品现有分销价) > 0
         ORDER BY 上线额 DESC
    """, conn, params=(city, period.start, period.end))

    if df.empty:
        return {'产品ROI_top10_按上线额': [], '总产品数': 0}

    df['ROI'] = df['红包额'] / df['上线额']
    df['红包额_元'] = df['红包额'].round(2)
    df['上线额_万'] = (df['上线额'] / 10000).round(2)

    top = df.head(top_k)
    records = top[['产品', '笔数', '红包额_元', '上线额_万', 'ROI']].round({'ROI': 4}).to_dict('records')

    return {
        '总产品数': int(len(df)),
        '产品ROI_top10_按上线额': records,
    }


def query_highlights(conn, city: str, period: Period, top_k: int = 5,
                    _sp_eval_result: dict = None) -> dict:
    """章 5.3 亮点案例 — 救援成功 / 维护成功 中拉动 SO 最多的客户

    定义：在 _visit_rfm_eval 评估中分类为 A1（救援成功）或 E1（维护成功）
          的客户，按拜访后 30 天内的上线货值降序，取 top_k

    优化：如果传入 _sp_eval_result（包含 _highlights_raw），直接用，避免重跑 evaluate
    """
    if _sp_eval_result and _sp_eval_result.get('_highlights_raw'):
        raw = _sp_eval_result['_highlights_raw']
        # 同一客户可能被多业务员拜访 → 按客户去重，保留最早拜访的 SP
        seen = {}
        for rec in sorted(raw, key=lambda x: x['首拜访日']):
            key = rec['客户名称']
            if key not in seen:
                seen[key] = rec
        records = sorted(seen.values(), key=lambda x: x['拉动SO_万'], reverse=True)
        return {'亮点案例_top5': records[:top_k]}

    # Fallback：独立跑（不推荐，慢）
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    from _visit_rfm_eval import list_all_salespeople, evaluate_salesperson

    sps = list_all_salespeople(conn, city, period.start, period.end)
    if not sps:
        return {'亮点案例_top5': []}

    pf_df = pd.read_sql("""
        SELECT 打卡人姓名, MAX(_打卡方) AS _打卡方
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
         GROUP BY 打卡人姓名
    """, conn, params=(city, period.start, period.end))
    sp_belong = {r['打卡人姓名']: ('大华' if '大华' in (r['_打卡方'] or '') else '代理商')
                 for _, r in pf_df.iterrows()}

    records = []
    for sp in sps:
        try:
            r = evaluate_salesperson(conn, city, sp, period.start, period.end)
        except Exception:
            continue
        v = r['visits_df']
        if v.empty:
            continue
        from _metrics_rfm import LABEL_RESCUE_SUCCESS, LABEL_NORMAL
        hits = v[v['分类'].isin([LABEL_RESCUE_SUCCESS, LABEL_NORMAL])]
        if hits.empty:
            continue
        for _, h in hits.iterrows():
            code = str(h['客户编码'])
            fv = h['首次拜访日']
            fv_str = fv.strftime('%Y-%m-%d')
            df_act = pd.read_sql("""
                SELECT COALESCE(SUM(产品现有分销价), 0) AS 拉动SO
                  FROM install_redpack_v
                 WHERE 上线客户编码 = ?
                   AND 上线时间 > ?
                   AND 上线时间 <= date(?, '+30 day')
            """, conn, params=(code, fv.strftime('%Y-%m-%d %H:%M:%S'), fv_str))
            so = float(df_act.iloc[0]['拉动SO'] or 0)
            if so <= 0:
                continue
            records.append({
                '业务员': sp,
                '所属': sp_belong.get(sp, '?'),
                '客户名称': h['客户名称'],
                '区县': h['区县'],
                '首拜访日': fv_str,
                '分类': h['分类'],
                '拉动SO_万': round(so / 10000, 2),
            })

    records.sort(key=lambda x: x['拉动SO_万'], reverse=True)
    return {'亮点案例_top5': records[:top_k]}


# ══════════════════════════════════════════════
# 主入口：汇总三个时段
# ══════════════════════════════════════════════

def gather_full_report(
    city: str,
    start: str,
    end: str,
    db_path: Path = None,
    year: int = 2026,
) -> dict:
    """跑三个时段（当期/同比/环比），每个时段跑 5 个维度。

    返回的 dict 结构（直接序列化成 JSON 给 V3 沙箱 AI 用）：
    {
        '地市': '...', '当期起止': '...', '当期月数': N,
        '时段': {'current': {start,end,n_months,...}, 'yoy': {...}, 'mom': {...}},
        '数据': {
            'so_overview':       {'current':{...}, 'yoy':{...}, 'mom':{...}, 'yoy_pct':..., 'mom_pct':...},
            'target_completion': {...},
            'district':          {...},
            'provider':          {...},
            'visit':             {...},
        },
        '对比表': {
            'so_overview': [{指标, 当期, 同期, 同比%, 环期, 环比%}, ...],
            ...
        }
    }
    """
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        periods = calculate_periods(start, end)

        out = {
            '地市': city,
            '当期起止': periods['current'].label,
            '当期月数': periods['current'].n_months,
            '时段': {k: asdict(v) for k, v in periods.items()},
            '数据': {},
        }

        # 跑 5 个维度 × 3 个时段（这些维度每个时段都跑）
        for dim_name, fn in [
            ('so_overview',       lambda c, p: query_so_overview(c, city, p)),
            ('target_completion', lambda c, p: query_target_completion(c, city, p, year=year)),
            ('district',          lambda c, p: query_district_breakdown(c, city, p)),
            ('provider',          lambda c, p: query_provider_profile(c, city, p)),
            ('visit',             lambda c, p: query_visit_summary(c, city, p)),
        ]:
            d = {}
            for pname, period in periods.items():
                d[pname] = fn(conn, period)
            out['数据'][dim_name] = d

        # 「原因穿透」类查询 — 只跑当期（不需要同环比对比）
        out['数据']['lifecycle'] = query_provider_lifecycle(conn, city, periods)
        out['数据']['keypoint_unvisited'] = query_keypoint_unvisited(conn, city, periods['current'])
        out['数据']['redpack_roi'] = query_redpack_roi(conn, city, periods['current'])
        out['数据']['visit_efficiency'] = query_visit_efficiency(conn, city, periods['current'])
        out['数据']['dispatch_alerts'] = query_dispatch_alerts(conn, city, periods['current'])
        out['数据']['salesperson_scope'] = query_salesperson_scope(conn, city)

        # ─── v3 新增：5 个查询（章 1.3 / 2 / 3 / 4.3 / 5.3）─────────
        out['数据']['activation_status'] = query_activation_status(conn, city, periods['current'])
        out['数据']['rfm_distribution']  = query_rfm_distribution(conn, city, periods['current'])
        out['数据']['abnormal_providers'] = query_abnormal_providers(conn, city, periods['current'])
        sp_eval = query_salesperson_evaluation(conn, city, periods['current'])
        # highlights 复用 salesperson_eval 已采集的 raw 数据，避免重跑 evaluate
        out['数据']['highlights']        = query_highlights(
            conn, city, periods['current'], _sp_eval_result=sp_eval,
        )
        # 落盘前剥掉 raw（不进 JSON）
        sp_eval.pop('_highlights_raw', None)
        out['数据']['salesperson_eval']  = sp_eval
        out['数据']['product_roi']       = query_product_redpack_roi(conn, city, periods['current'])

        # ─── 对比表（AI 直接读 _显示 字段填 docx）─────────────
        cur_so = out['数据']['so_overview']['current']
        yoy_so = out['数据']['so_overview']['yoy']
        mom_so = out['数据']['so_overview']['mom']

        cur_pv = out['数据']['provider']['current']
        yoy_pv = out['数据']['provider']['yoy']
        mom_pv = out['数据']['provider']['mom']

        cur_vt = out['数据']['visit']['current']
        yoy_vt = out['数据']['visit']['yoy']
        mom_vt = out['数据']['visit']['mom']

        cur_tc = out['数据']['target_completion']['current']

        out['对比表'] = {
            'so_overview': [
                _diff_row('全量感知 (台)', cur_so['全量感知_台数'], yoy_so['全量感知_台数'], mom_so['全量感知_台数'], unit='count'),
                _diff_row('全量感知 (万元)', cur_so['全量感知_金额_万'], yoy_so['全量感知_金额_万'], mom_so['全量感知_金额_万']),
                _diff_row('安装红包 (台)', cur_so['安装红包_台数'], yoy_so['安装红包_台数'], mom_so['安装红包_台数'], unit='count'),
                _diff_row('安装红包 (万元)', cur_so['安装红包_金额_万'], yoy_so['安装红包_金额_万'], mom_so['安装红包_金额_万']),
                _diff_row('绑定率',
                          _pct_to_pp(cur_so['绑定率']),
                          _pct_to_pp(yoy_so['绑定率']),
                          _pct_to_pp(mom_so['绑定率']),
                          unit='pp'),
            ],
            'provider': [
                _diff_row('当期活跃服务商 (家)', cur_pv['当期活跃服务商数'], yoy_pv['当期活跃服务商数'], mom_pv['当期活跃服务商数'], unit='count'),
                _diff_row('TOP10 服务商集中度',
                          _pct_to_pp(cur_pv['top10_集中度']),
                          _pct_to_pp(yoy_pv['top10_集中度']),
                          _pct_to_pp(mom_pv['top10_集中度']),
                          unit='pp'),
                # 新激活只在历史窗口够时给数；不够时表里也 N/A
                _diff_row('新激活服务商 (家)',
                          cur_pv['新激活'], yoy_pv['新激活'], mom_pv['新激活'],
                          unit='count'),
            ],
            'visit': [
                _diff_row('有效打卡 (次)', cur_vt.get('有效打卡', 0), yoy_vt.get('有效打卡', 0), mom_vt.get('有效打卡', 0), unit='count'),
                _diff_row('大华业务员 打卡 (次)', cur_vt.get('大华_有效', 0), yoy_vt.get('大华_有效', 0), mom_vt.get('大华_有效', 0), unit='count'),
                _diff_row('代理商业务员 打卡 (次)', cur_vt.get('代理商_有效', 0), yoy_vt.get('代理商_有效', 0), mom_vt.get('代理商_有效', 0), unit='count'),
                _diff_row('活跃业务员 (人)', cur_vt.get('活跃业务员数', 0), yoy_vt.get('活跃业务员数', 0), mom_vt.get('活跃业务员数', 0), unit='count'),
            ],
        }

        # ─── 等级分布对比（官方原始等级 V0-V5，每个等级一行）────────────────
        all_levels = ['V5', 'V4', 'V3', 'V2', 'V1', 'V0', '未签约']
        level_rows = []
        for lvl in all_levels:
            level_rows.append(_diff_row(
                lvl,
                cur_pv['等级分布'].get(lvl, 0),
                yoy_pv['等级分布'].get(lvl, 0),
                mom_pv['等级分布'].get(lvl, 0),
                unit='count',
            ))
        out['对比表']['provider_tier'] = level_rows

        # ─── 核心结论（一句话，给报告开头用）───────────
        out['核心结论'] = build_core_finding(out)

        # ─── 数据完整性自检 ──────────────────────────
        # 列出每个时段哪个表数据缺失，让 AI 在报告里给出说明
        gaps = []
        for pname, period in periods.items():
            so = out['数据']['so_overview'][pname]
            label = period.label
            if so.get('_全量感知数据缺失'):
                gaps.append(f"{pname} ({label}) — product_flow（全量感知）表无数据")
            if so.get('_安装红包数据缺失'):
                gaps.append(f"{pname} ({label}) — install_redpack（安装红包）表无数据")
        out['数据完整性'] = {
            '有缺口': bool(gaps),
            '缺口': gaps,
        }

        return out
    finally:
        conn.close()


def build_core_finding(report: dict) -> str:
    """根据数据自动生成一句话核心结论"""
    tc = report['数据']['target_completion']['current']
    so_curr = report['数据']['so_overview']['current']
    so_mom = report['数据']['so_overview']['mom']
    pv = report['数据']['provider']
    vt = report['数据']['visit']

    parts = []

    # 1. 完成率
    if tc.get('完成率') is not None:
        rate = tc['完成率']
        parts.append(
            f"完成率 {rate*100:.1f}%（应达成 {tc['应达成_万']:.1f} 万 / "
            f"实际 {tc['实际达成_万']:.1f} 万，缺口 {tc['应达成_万'] - tc['实际达成_万']:.1f} 万）"
        )
    else:
        parts.append(f"实际达成 {tc.get('实际达成_万', 0):.1f} 万（应达成无法计算）")

    # 2. 环比（全量感知口径，跟完成率同维度）
    if so_curr['全量感知_金额_万'] is not None and so_mom['全量感知_金额_万']:
        mom_change = (so_curr['全量感知_金额_万'] - so_mom['全量感知_金额_万']) / so_mom['全量感知_金额_万']
        verb = '上升' if mom_change > 0.01 else ('下滑' if mom_change < -0.01 else '持平')
        parts.append(f'全量感知口径环比{verb} {abs(mom_change)*100:.1f}%')

    # 3. 关键风险
    risks = []
    cur_active = pv['current'].get('当期活跃服务商数', 0)
    mom_active = pv['mom'].get('当期活跃服务商数', 0)
    if mom_active and cur_active < mom_active * 0.9:
        loss = mom_active - cur_active
        risks.append(f'活跃服务商环比净流失 {loss} 家')

    cur_visit = vt['current'].get('有效打卡', 0) or 0
    mom_visit = vt['mom'].get('有效打卡', 0) or 0
    if mom_visit and cur_visit < mom_visit * 0.9:
        risks.append(f'有效打卡环比下滑 {(mom_visit-cur_visit)*100/mom_visit:.1f}%')

    if risks:
        parts.append('风险信号：' + '、'.join(risks))

    return '；'.join(parts) + '。'


def _fmt_num(x, decimals=1) -> str:
    """格式化数字（千分位 + 小数控制）"""
    if x is None:
        return 'N/A'
    if isinstance(x, int) or (isinstance(x, float) and x == int(x)):
        return f'{int(x):,}'
    return f'{x:,.{decimals}f}'


def _pct_change(curr, base):
    """变化率（小数：0.066 = +6.6%）"""
    if curr is None or base is None or base == 0:
        return None
    return (curr - base) / base


def _pp_diff(curr, base):
    """百分点差异（curr/base 都是百分点）"""
    if curr is None or base is None:
        return None
    return curr - base


def _diff_row(name: str, curr, base_yoy, base_mom, unit: str = '%') -> dict:
    """构造对比表一行 —— **同时给出数值和已格式化的字符串**

    SKILL 里强制 AI 用 _显示 字段填 docx 表格，避免 AI 自己 format 出错。

    unit:
      '%':    指标本身是计数/金额，同环比 = (curr - base) / base * 100% 形式
      'pp':   指标本身是百分点（如绑定率 55.8 表示 55.8%），同环比用"百分点差异"
      'count': 整数计数，差异显示 +N / -N（不用百分号）
    """
    if unit == 'pp':
        # 指标本身是百分比（绑定率 / 完成率 / 集中度）—— 同环比 = 绝对百分点差
        # 显示用 % 后缀保持视觉统一（含义仍是绝对差值，不是相对变化率）
        yoy_delta = _pp_diff(curr, base_yoy)
        mom_delta = _pp_diff(curr, base_mom)
        return {
            '指标': name,
            '当期':    curr,    '当期_显示': f'{_fmt_num(curr)}%'    if curr     is not None else 'N/A',
            '同期':    base_yoy,'同期_显示': f'{_fmt_num(base_yoy)}%' if base_yoy is not None else 'N/A',
            '同比变化': yoy_delta,'同比_显示': f'{yoy_delta:+.1f}%'   if yoy_delta is not None else 'N/A',
            '环期':    base_mom,'环期_显示': f'{_fmt_num(base_mom)}%' if base_mom is not None else 'N/A',
            '环比变化': mom_delta,'环比_显示': f'{mom_delta:+.1f}%'   if mom_delta is not None else 'N/A',
        }
    elif unit == 'count':
        yoy = _pct_change(curr, base_yoy)
        mom = _pct_change(curr, base_mom)
        return {
            '指标': name,
            '当期':    curr,    '当期_显示': _fmt_num(curr, decimals=0),
            '同期':    base_yoy,'同期_显示': _fmt_num(base_yoy, decimals=0),
            '同比_变化率': yoy, '同比_显示': f'{yoy*100:+.1f}%' if yoy is not None else 'N/A',
            '环期':    base_mom,'环期_显示': _fmt_num(base_mom, decimals=0),
            '环比_变化率': mom, '环比_显示': f'{mom*100:+.1f}%' if mom is not None else 'N/A',
        }
    else:
        yoy = _pct_change(curr, base_yoy)
        mom = _pct_change(curr, base_mom)
        return {
            '指标': name,
            '当期':    curr,    '当期_显示': _fmt_num(curr),
            '同期':    base_yoy,'同期_显示': _fmt_num(base_yoy),
            '同比':    yoy,     '同比_显示': f'{yoy*100:+.1f}%' if yoy is not None else 'N/A',
            '环期':    base_mom,'环期_显示': _fmt_num(base_mom),
            '环比':    mom,     '环比_显示': f'{mom*100:+.1f}%' if mom is not None else 'N/A',
        }


def _pct_to_pp(x):
    """0.453 → 45.3（百分点）"""
    if x is None:
        return None
    return round(x * 100, 2)


# ══════════════════════════════════════════════
# 城市列表 helper（给 page UI 下拉用）
# ══════════════════════════════════════════════

def list_cities(db_path: Path = None) -> list[str]:
    """从 kpi_targets 里拿所有城市清单"""
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT 城市 FROM kpi_targets ORDER BY 城市")
        rows = [r[0] for r in cur.fetchall()]
        return rows
    finally:
        conn.close()


def list_months(db_path: Path = None) -> tuple[str, str]:
    """从 install_redpack_v 看数据覆盖的月份范围 (min, max)"""
    db_path = db_path or DB_PATH_DEFAULT
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(上线年月), MAX(上线年月)
              FROM install_redpack_v
             WHERE 上线年月 IS NOT NULL AND 上线年月 != ''
        """)
        row = cur.fetchone()
        return row[0] or '2025-01', row[1] or '2026-05'
    finally:
        conn.close()


if __name__ == '__main__':
    # 命令行试跑：python _monthly_city_report.py 金华市 2026-04 2026-04
    import sys
    import json
    city = sys.argv[1] if len(sys.argv) > 1 else '金华市'
    start = sys.argv[2] if len(sys.argv) > 2 else '2026-04'
    end = sys.argv[3] if len(sys.argv) > 3 else '2026-04'

    print(f'地市: {city}, 时段: {start} ~ {end}')
    print(f'城市列表 ({len(list_cities())}个): {list_cities()}')
    print(f'数据覆盖月份: {list_months()}')
    print()

    out = gather_full_report(city, start, end)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
