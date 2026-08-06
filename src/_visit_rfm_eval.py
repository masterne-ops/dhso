"""业务员跑动 RFM-Impact 评估（page 06 + 月报 2.2 共用）

🔄 算法已抽取到 `_metrics_rfm` 模块,本文件仅做数据查询 + 适配。
所有 RFM / 等级 / 拜访分类 / 漏跑判定 阈值修改,改 `_metrics_rfm` 即可。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from _metrics_rfm import (
    calc_rfm,
    classify_visit,
    is_missed,
    missed_severity,
    tier_of,
    r_grade as _grade_R,
    f_grade as _grade_F,
    m_growth_grade as _grade_M,
    R_HEALTHY, R_WARN, F_HEALTHY, F_BAD,
    M_GROWTH_WARN, M_GROWTH_BAD,
)

DB_PATH_DEFAULT = Path(__file__).parent.parent / 'db' / 'product_flow.db'

# 兼容旧调用方
_level = tier_of


def _calc_rfm(acts_idx: dict, code: str, asof: pd.Timestamp) -> Optional[dict]:
    """asof 时刻该客户的 RFM(兼容旧签名,内部转调 _metrics_rfm.calc_rfm)"""
    g = acts_idx.get(str(code))
    return calc_rfm(g, asof) if g is not None and not g.empty else None


def _classify_visit(
    visit_row: dict,
    acts_idx: dict,
    db_max_dt: pd.Timestamp,
    period_end_dt: pd.Timestamp,
    sp_dealers: set,
    vest_codes: set,
) -> dict:
    """单次拜访 RFM-Impact 分类(兼容旧签名)"""
    return classify_visit(visit_row, acts_idx, db_max_dt, period_end_dt,
                          sp_dealers, vest_codes)


def evaluate_salesperson(
    conn: sqlite3.Connection,
    city: str,
    sp: str,
    period_start: str,
    period_end: str,
    db_max_dt: pd.Timestamp = None,
) -> dict:
    """单个业务员的两步评估结果

    返回：{
        'visits_df': DataFrame[客户编码, 客户名称, 区县, 首次拜访日, 当期拜访次数, SP历史拜访, 分类, 拜访意图, 结果, 依据, 马甲标识],
        'missed_df':  DataFrame 漏跑名单（V3+ 严重 / V2 一般），含期初R/期末R/累计货值/等级
        'sp_dealers': set 该业务员负责的代理商
        'sp_districts': set 该业务员负责的区县
        'scope_codes': set scope 内的客户编码
        'summary':    dict 统计摘要
    }
    """
    if db_max_dt is None:
        max_month = pd.read_sql(
            "SELECT MAX(上线年月) AS m FROM install_redpack_v WHERE 上线年月 IS NOT NULL", conn,
        ).iloc[0]['m']
        db_max_dt = pd.Timestamp(max_month + '-01') + pd.offsets.MonthEnd(0)

    period_end_dt = pd.Timestamp(period_end + '-01') + pd.offsets.MonthEnd(0)
    period_start_dt = pd.Timestamp(period_start + '-01')

    # 1. scope
    scope_df = pd.read_sql(
        "SELECT 代理商, 区县 FROM salesperson_scope WHERE 业务员 = ? AND 市 = ?",
        conn, params=(sp, city),
    )
    sp_dealers = set(scope_df['代理商'].dropna().tolist())
    sp_districts = set(scope_df['区县'].dropna().tolist())

    # 2. 当期拜访
    visits_df = pd.read_sql("""
        SELECT 客户编码, MAX(拜访客户) AS 客户名称, MAX(拜访客户区县) AS 区县,
               MIN(date(拜访时间)) AS 首次拜访日, COUNT(*) AS 当期拜访次数
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND 打卡人姓名 = ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 客户编码 IS NOT NULL AND 客户编码 != ''
         GROUP BY 客户编码
    """, conn, params=(city, period_start, period_end, sp))
    visits_df['首次拜访日'] = pd.to_datetime(visits_df['首次拜访日'])
    visited_codes = set(visits_df['客户编码'].astype(str))

    # 3. 历史拜访次数（含当期前）
    hist = pd.read_sql("""
        SELECT 客户编码, COUNT(*) AS SP历史拜访次数
          FROM visit_record_v
         WHERE 打卡人姓名 = ? AND 拜访客户城市 = ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
         GROUP BY 客户编码
    """, conn, params=(sp, city))
    visits_df = visits_df.merge(hist, on='客户编码', how='left')

    # 4. scope 内客户（按所属一级客户）
    scope_codes = set()
    if sp_dealers:
        placeholders = ','.join(['?'] * len(sp_dealers))
        scope_df_clients = pd.read_sql(f"""
            SELECT 上线客户编码, MAX(上线客户名称) AS 客户名称,
                   MAX(上线客户区县) AS 区县
              FROM install_redpack_v
             WHERE 上线客户地市 = ?
               AND 所属一级客户 IN ({placeholders})
               AND 上线客户编码 IS NOT NULL AND 上线客户编码 != ''
             GROUP BY 上线客户编码
        """, conn, params=(city, *sp_dealers))
        scope_codes = set(scope_df_clients['上线客户编码'].astype(str))
    else:
        scope_df_clients = pd.DataFrame(columns=['上线客户编码', '客户名称', '区县'])

    # 5. 一次性取所有相关客户的上线
    all_codes = list(visited_codes | scope_codes)
    if not all_codes:
        return {
            'visits_df': visits_df.assign(分类='', 拜访意图='', 结果=None, 依据='', 马甲标识=''),
            'missed_df': pd.DataFrame(),
            'sp_dealers': sp_dealers, 'sp_districts': sp_districts,
            'scope_codes': scope_codes,
            'summary': {'visits_total': 0, 'missed_total': 0},
        }
    codes_in = "','".join(all_codes)
    all_act = pd.read_sql(f"""
        SELECT 上线客户编码 AS 客户编码, 上线时间, 产品现有分销价
          FROM install_redpack_v
         WHERE 上线客户编码 IN ('{codes_in}')
    """, conn)
    all_act['上线时间'] = pd.to_datetime(all_act['上线时间'])
    acts_idx = {c: g for c, g in all_act.groupby('客户编码')}

    # 6. 马甲名单
    vest_codes = set(pd.read_sql(
        "SELECT 服务商客户编码 FROM vest_account", conn,
    )['服务商客户编码'].astype(str))

    # 7. 拜访分类
    classifications = []
    for _, row in visits_df.iterrows():
        c = _classify_visit(
            row.to_dict(), acts_idx, db_max_dt, period_end_dt,
            sp_dealers, vest_codes,
        )
        classifications.append(c)
    cls_df = pd.DataFrame(classifications)
    visits_df = pd.concat([visits_df.reset_index(drop=True), cls_df], axis=1)

    # 8. 漏跑识别(逻辑统一从 _metrics_rfm.is_missed / missed_severity 来)
    missed = []
    for code in scope_codes - visited_codes:
        rfm_start = _calc_rfm(acts_idx, code, period_start_dt)
        rfm_end = _calc_rfm(acts_idx, code, period_end_dt)
        if not is_missed(rfm_start, rfm_end):
            continue
        row_c = scope_df_clients[scope_df_clients['上线客户编码'] == code]
        if row_c.empty:
            continue
        client = row_c.iloc[0]
        lvl = tier_of(rfm_end['M_cur'])
        missed.append({
            '客户编码': code,
            '客户名称': client['客户名称'],
            '区县': client['区县'],
            '期初_R': rfm_start['R'],
            '期末_R': rfm_end['R'],
            'F_12mo': rfm_end['F'],
            '累计货值_万': round(rfm_end['M_cur'] / 10000, 2),
            '等级': lvl,
            '严重度': missed_severity(rfm_end),
            '马甲标识': '🎭' if code in vest_codes else '',
        })
    if missed:
        missed_df = pd.DataFrame(missed)
        # 🚨 严重 排在 ⚠️ 一般 之前
        missed_df['_sev_rank'] = missed_df['严重度'].map(
            {'🚨 严重': 0, '⚠️ 一般': 1},
        ).fillna(99)
        missed_df = (
            missed_df.sort_values(['_sev_rank', '累计货值_万', '期末_R'],
                                  ascending=[True, False, False])
            .drop(columns=['_sev_rank'])
            .reset_index(drop=True)
        )
    else:
        missed_df = pd.DataFrame()

    # 9. 摘要(新 7 分类口径)
    from _metrics_rfm import (
        LABEL_RESCUE_SUCCESS, LABEL_RESCUE_FAIL, LABEL_NORMAL, LABEL_LOST,
        LABEL_NEW, LABEL_OFFICE, LABEL_PENDING,
    )
    summary = {
        'visits_total': int(len(visits_df)),
        'missed_total': int(len(missed_df)),
        'missed_严重': int((missed_df['严重度'] == '🚨 严重').sum()) if not missed_df.empty else 0,
        'missed_一般': int((missed_df['严重度'] == '⚠️ 一般').sum()) if not missed_df.empty else 0,
        'sel_代理商办公室': int((visits_df['分类'] == LABEL_OFFICE).sum()),
        'sel_主动救援': int((visits_df['拜访意图'] == '救援').sum()),
        'sel_健康维护': int((visits_df['拜访意图'] == '维护').sum()),
        'sel_新客探访': int((visits_df['分类'] == LABEL_NEW).sum()),
        'sel_马甲拜访': int((visits_df['马甲标识'] == '🎭').sum()),
        'eval_可评数': int(visits_df['结果'].notna().sum()),
        'eval_待观察': int((visits_df['分类'] == LABEL_PENDING).sum()),
        # ─── 7 类拜访结果 ───
        'res_救援成功': int((visits_df['分类'] == LABEL_RESCUE_SUCCESS).sum()),
        'res_救援失败': int((visits_df['分类'] == LABEL_RESCUE_FAIL).sum()),
        'res_正常':    int((visits_df['分类'] == LABEL_NORMAL).sum()),
        'res_流失':    int((visits_df['分类'] == LABEL_LOST).sum()),
    }

    return {
        'visits_df': visits_df,
        'missed_df': missed_df,
        'sp_dealers': sp_dealers,
        'sp_districts': sp_districts,
        'scope_codes': scope_codes,
        'summary': summary,
    }


def list_all_salespeople(conn, city: str, period_start: str, period_end: str) -> list[str]:
    """当期在该城市有效拜访过的所有业务员（含大华+代理商）"""
    df = pd.read_sql("""
        SELECT DISTINCT 打卡人姓名
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
           AND _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 打卡人姓名 IS NOT NULL AND 打卡人姓名 != ''
         ORDER BY 打卡人姓名
    """, conn, params=(city, period_start, period_end))
    return df['打卡人姓名'].tolist()


def overview_all_salespeople(
    conn: sqlite3.Connection,
    city: str,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    """全市所有业务员的评估汇总（排行榜）

    返回 DataFrame[业务员, 所属(大华/代理商), 负责区县, 拜访客户数,
                  ✅ 选择合理, ⚠️ 代理商办公室, 🚨 严重漏跑, ⚠️ 一般漏跑,
                  救援成功, 救援未果, 维护成功, 维护稳定, 维护不到位, 维护失败]
    """
    sps = list_all_salespeople(conn, city, period_start, period_end)
    rows = []

    # 大华 vs 代理商：从 visit_record_v._打卡方 判定
    pf_df = pd.read_sql("""
        SELECT 打卡人姓名 AS 业务员, MAX(_打卡方) AS _打卡方
          FROM visit_record_v
         WHERE 拜访客户城市 = ?
           AND 拜访年月 BETWEEN ? AND ?
         GROUP BY 打卡人姓名
    """, conn, params=(city, period_start, period_end))
    pf_map = dict(zip(pf_df['业务员'], pf_df['_打卡方']))

    for sp in sps:
        try:
            r = evaluate_salesperson(conn, city, sp, period_start, period_end)
        except Exception as e:
            continue
        s = r['summary']
        rows.append({
            '业务员': sp,
            '所属': pf_map.get(sp, '?').replace('🏢 ', '').replace('🏪 ', ''),
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

    return pd.DataFrame(rows)
