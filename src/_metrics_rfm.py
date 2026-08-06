"""服务商 RFM 度量 + 客户状态 + 拜访分类 — 单一真相源

此模块统一所有 RFM 相关算法。

================ 客户状态(3 档,基于 R)================

  🟢 活跃     R ≤ R_THRESHOLD(默认 30 天)
  🚨 流失     R > R_THRESHOLD 且 累计货值 > 0
  💤 未激活   累计货值 = 0

  → 沉睡 / 失维 / 维护失败 / 漏跑导致流失 ... 全部归到 🚨 流失

================ 拜访结果分类(7 类)================

  ✅ 救援成功         救援意图 + 效果窗口内有上线
  ❌ 救援失败         救援意图 + 效果窗口内无上线
  🟢 正常             维护意图 + 拜访后客户期末仍活跃
  🚨 流失             维护意图 + 拜访后客户期末已流失
  🌱 新客探访         历史无上线
  ⚠️ 代理商办公室打卡  选择失误(拜访客户名 = 业务员负责代理商)
  ⏳ 待观察           拜访 + 效果窗口 > 数据末日

================ 单一阈值 ================

  R_THRESHOLD = 30           客户状态 + 救援/维护意图分界
  EFFECT_WINDOW_DAYS = 30    拜访后效果窗口
  F_RESCUE = 3               F < 3 也算救援意图(辅助)

  9 宫格可视化仍用三档(R 🟢≤30 / 🟡30-90 / 🔴>90 / F 🟢≥12 / 🟡3-11 / 🔴<3)
  但状态判定只用 R_THRESHOLD 单值。

================ 漏跑判定(独立概念)================

  漏跑 = 客户由活转流失(R 期初≤30 → 期末>30)+ 累计 M>0 + 评估期内业务员没拜访

  与"流失"的关系:漏跑是流失客户的一个子集(没拜访的那部分)。
  业务员考核口径不同,所以保留独立。

================ 等级判定(基于近 12 月累计货值,元)================

  V4 ≥ 30,000   V3 ≥ 10,000   V2 ≥ 1,000   已激活 > 0   v0 = 0
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd

# ══════════════════════════════════════════════════════════
# 阈值常量(集中,改这一处全系统跟着变)
# ══════════════════════════════════════════════════════════

# 等级(累计货值 元)
TIER_V4_FLOOR = 30000
TIER_V3_FLOOR = 10000
TIER_V2_FLOOR = 1000

# 单阈值:客户状态 + 救援/维护意图分界
R_THRESHOLD = 30
F_RESCUE = 3                # F<3 也算救援意图

# 拜访效果窗口
EFFECT_WINDOW_DAYS = 30

# 9 宫格可视化(纯展示用,跟状态判定无关)
R_VISUAL_GREEN = 30
R_VISUAL_YELLOW = 90        # >90 算长期流失
F_VISUAL_HEALTHY = 12
F_VISUAL_BAD = 3

# M 同比增长
M_GROWTH_WARN = 0.0
M_GROWTH_BAD = -0.10

# RFM 窗口
DEFAULT_WINDOW_DAYS = 365


# ══════════════════════════════════════════════════════════
# 客户状态(3 档)
# ══════════════════════════════════════════════════════════

def customer_status(R, cum_val=None) -> str:
    """客户状态判定 — 3 档,全系统统一

    Args:
        R: 最近上线距今天数(None 视为未交易过)
        cum_val: 累计货值(元),用于区分流失 vs 未激活
    """
    if cum_val is None or cum_val <= 0:
        return '💤 未激活'
    if R is None or R > R_THRESHOLD:
        return '🚨 流失'
    return '🟢 活跃'


def is_lost(R, cum_val=None) -> bool:
    """流失判定 — 包括沉睡 / 失维 / 漏跑导致流失"""
    if cum_val is not None and cum_val <= 0:
        return False   # 未激活,不算流失
    return R is not None and R > R_THRESHOLD


def is_active(R, cum_val=None) -> bool:
    """活跃判定 — 跟 is_lost 互斥"""
    if cum_val is None or cum_val <= 0:
        return False   # 未激活
    return R is not None and R <= R_THRESHOLD


# ══════════════════════════════════════════════════════════
# 等级(基于累计货值)
# ══════════════════════════════════════════════════════════

def tier_of(cum_val: float) -> str:
    if cum_val is None:
        return 'v0'
    v = float(cum_val or 0)
    if v >= TIER_V4_FLOOR: return 'V4'
    if v >= TIER_V3_FLOOR: return 'V3'
    if v >= TIER_V2_FLOOR: return 'V2'
    if v > 0: return '已激活'
    return 'v0'


def tier_case_sql(col: str = '累计货值') -> str:
    """SQL CASE 表达式,跟 tier_of 共用阈值"""
    return f"""
        CASE
            WHEN {col} >= {TIER_V4_FLOOR} THEN 'V4'
            WHEN {col} >= {TIER_V3_FLOOR} THEN 'V3'
            WHEN {col} >= {TIER_V2_FLOOR} THEN 'V2'
            WHEN {col} > 0 THEN '已激活'
            ELSE 'v0'
        END
    """.strip()


# ══════════════════════════════════════════════════════════
# R/F 分桶(9 宫格可视化用,纯标签,不参与判定)
# ══════════════════════════════════════════════════════════

def r_grade(r) -> str:
    """R 短标:🟢/🟡/🔴/?"""
    if r is None: return '?'
    if r <= R_VISUAL_GREEN: return '🟢'
    if r <= R_VISUAL_YELLOW: return '🟡'
    return '🔴'


def r_bucket(r, style: str = 'long') -> str:
    """R 分桶
    long  → R🟢(≤30) / R🟡(30-90) / R🔴(>90)
    short → 🟢 / 🟡 / 🔴
    """
    if r is None: return '?'
    if style == 'short': return r_grade(r)
    if r <= R_VISUAL_GREEN: return f'R🟢(≤{R_VISUAL_GREEN})'
    if r <= R_VISUAL_YELLOW: return f'R🟡({R_VISUAL_GREEN}-{R_VISUAL_YELLOW})'
    return f'R🔴(>{R_VISUAL_YELLOW})'


def f_grade(f) -> str:
    if f is None: return '?'
    if f >= F_VISUAL_HEALTHY: return '🟢'
    if f >= F_VISUAL_BAD: return '🟡'
    return '🔴'


def f_bucket(f, style: str = 'long') -> str:
    if f is None: return '?'
    if style == 'short': return f_grade(f)
    if f >= F_VISUAL_HEALTHY: return f'F🟢(≥{F_VISUAL_HEALTHY})'
    if f >= F_VISUAL_BAD: return f'F🟡({F_VISUAL_BAD}-{F_VISUAL_HEALTHY - 1})'
    return f'F🔴(<{F_VISUAL_BAD})'


def m_growth_grade(g) -> str:
    if g is None: return '?'
    if g > M_GROWTH_WARN: return '🟢'
    if g >= M_GROWTH_BAD: return '🟡'
    return '🔴'


# ══════════════════════════════════════════════════════════
# RFM 计算
# ══════════════════════════════════════════════════════════

def calc_rfm(
    activities: pd.DataFrame,
    asof: pd.Timestamp,
    window_days: int = DEFAULT_WINDOW_DAYS,
    with_growth: bool = True,
) -> Optional[dict]:
    """asof 时刻该客户的 RFM。

    Returns:
        {R, F, M_cur, M_万, M_prev, M_growth, _last_active}
    """
    if activities is None or activities.empty:
        return None
    if not pd.api.types.is_datetime64_any_dtype(activities['上线时间']):
        activities = activities.copy()
        activities['上线时间'] = pd.to_datetime(activities['上线时间'], errors='coerce')

    before = activities[activities['上线时间'] < asof]
    if before.empty:
        return None

    last = before['上线时间'].max()
    cur_win = asof - timedelta(days=window_days)
    cur = before[before['上线时间'] >= cur_win]
    F = cur['上线时间'].dt.date.nunique()
    M_cur = float(cur['产品现有分销价'].sum())

    result = {
        'R': int((asof - last).days),
        'F': int(F),
        'M_cur': M_cur,
        'M_万': round(M_cur / 10000, 2),
        '_last_active': last,
    }

    if with_growth:
        prev_win = asof - timedelta(days=2 * window_days)
        prev = before[(before['上线时间'] >= prev_win) & (before['上线时间'] < cur_win)]
        M_prev = float(prev['产品现有分销价'].sum())
        result['M_prev'] = M_prev
        result['M_growth'] = (M_cur - M_prev) / M_prev if M_prev > 0 else None

    return result


# ══════════════════════════════════════════════════════════
# 拜访意图 + 拜访结果分类(7 类)
# ══════════════════════════════════════════════════════════

# 拜访结果分类常量(供外部 startswith / equals 匹配)
LABEL_RESCUE_SUCCESS = '✅ 救援成功'
LABEL_RESCUE_FAIL = '❌ 救援失败'
LABEL_NORMAL = '🟢 正常'
LABEL_LOST = '🚨 流失'
LABEL_NEW = '🌱 新客探访'
LABEL_OFFICE = '⚠️ 代理商办公室打卡'
LABEL_PENDING = '⏳ 待观察'


def is_rescue_intent(R, F=None) -> bool:
    """救援意图判定 — 拜访那一刻客户 R 已超阈值 或 F 严重不足"""
    if R is None:
        return False
    if R > R_THRESHOLD:
        return True
    if F is not None and F < F_RESCUE:
        return True
    return False


def classify_visit(
    visit_row: dict,
    activities_by_code: dict,
    db_max_dt: pd.Timestamp,
    period_end_dt: pd.Timestamp,
    sp_dealers: set,
    vest_codes: set,
    effect_window_days: int = EFFECT_WINDOW_DAYS,
) -> dict:
    """单次拜访(业务员对某客户)的分类。

    Returns:
        {'分类', '拜访意图', '结果', '依据', '马甲标识'}
    """
    code = str(visit_row['客户编码'])
    name = visit_row['客户名称']
    fv = visit_row['首次拜访日']

    vest_tag = '🎭' if code in vest_codes else ''

    # 选择不当
    if name in sp_dealers:
        return {
            '分类': LABEL_OFFICE,
            '拜访意图': '—',
            '结果': None,
            '依据': f'「{name}」是该业务员负责的代理商',
            '马甲标识': vest_tag,
        }

    g = activities_by_code.get(code, pd.DataFrame())
    rfm = calc_rfm(g, fv, with_growth=False) if not g.empty else None

    # 历史无上线 → 新客探访
    if rfm is None:
        return {
            '分类': LABEL_NEW,
            '拜访意图': '探客',
            '结果': None,
            '依据': '历史无上线,前 2 次拜访合理',
            '马甲标识': vest_tag,
        }

    R, F = rfm['R'], rfm['F']
    rfm_label = f'R={R}{r_grade(R)} F={F}{f_grade(F)}'

    # 效果窗口超 DB → 待观察
    win_within_db = (fv + timedelta(days=effect_window_days)) <= db_max_dt
    if not win_within_db:
        intent = '救援' if is_rescue_intent(R, F) else '维护'
        return {
            '分类': LABEL_PENDING,
            '拜访意图': intent,
            '结果': None,
            '依据': f'{rfm_label}, 拜访后 {effect_window_days} 天窗口超过数据末日',
            '马甲标识': vest_tag,
        }

    # 效果窗口内是否有上线
    post = g[(g['上线时间'] > fv) & (g['上线时间'] <= fv + timedelta(days=effect_window_days))]
    has_post = not post.empty

    rescue = is_rescue_intent(R, F)

    if rescue:
        if has_post:
            return {
                '分类': LABEL_RESCUE_SUCCESS,
                '拜访意图': '救援',
                '结果': '成功',
                '依据': f'{rfm_label}, 拜访后 {effect_window_days} 天有激活',
                '马甲标识': vest_tag,
            }
        return {
            '分类': LABEL_RESCUE_FAIL,
            '拜访意图': '救援',
            '结果': '失败',
            '依据': f'{rfm_label}, 拜访后 {effect_window_days} 天无激活',
            '马甲标识': vest_tag,
        }

    # 维护意图:看期末状态
    rfm_end = calc_rfm(g, period_end_dt, with_growth=False) if not g.empty else None
    end_R = rfm_end['R'] if rfm_end else None

    if has_post or (end_R is not None and end_R <= R_THRESHOLD):
        return {
            '分类': LABEL_NORMAL,
            '拜访意图': '维护',
            '结果': '正常',
            '依据': f'{rfm_label}, 拜访后{"有激活" if has_post else f"期末 R={end_R} 仍活跃"}',
            '马甲标识': vest_tag,
        }

    return {
        '分类': LABEL_LOST,
        '拜访意图': '维护',
        '结果': '流失',
        '依据': f'{rfm_label}, 期末 R={end_R}(>{R_THRESHOLD},已流失)',
        '马甲标识': vest_tag,
    }


# ══════════════════════════════════════════════════════════
# 漏跑判定(独立概念)
# ══════════════════════════════════════════════════════════

def is_missed(rfm_start: Optional[dict], rfm_end: Optional[dict]) -> bool:
    """漏跑判定 — 期初活跃,期末流失,累计有货

    注:在外层调用方还需附加条件「业务员评估期内未拜访该客户」
    """
    if rfm_start is None or rfm_end is None:
        return False
    if rfm_start['R'] > R_THRESHOLD:
        return False    # 期初就流失,不算漏跑
    if rfm_end['R'] <= R_THRESHOLD:
        return False    # 期末仍活,没流失
    if rfm_end.get('M_cur', 0) <= 0:
        return False    # 累计 0 货,不重要
    return True


def missed_severity(rfm_end: dict) -> str:
    """漏跑严重度 — V4/V3 = 🚨 严重,其余 = ⚠️ 一般"""
    lvl = tier_of(rfm_end.get('M_cur', 0))
    return '🚨 严重' if lvl in ('V4', 'V3') else '⚠️ 一般'


# ══════════════════════════════════════════════════════════
# 9 宫格(R 三档 × F 三档,可视化用)
# ══════════════════════════════════════════════════════════

NINE_CELL_LABEL = {
    ('R🟢(≤30)',   'F🟢(≥12)'):  '核心客户',
    ('R🟢(≤30)',   'F🟡(3-11)'): '健康偶发',
    ('R🟢(≤30)',   'F🔴(<3)'):   '偶发活跃',
    ('R🟡(30-90)', 'F🟢(≥12)'):  '高频疏远',
    ('R🟡(30-90)', 'F🟡(3-11)'): '预警客户',
    ('R🟡(30-90)', 'F🔴(<3)'):   '预警低频',
    ('R🔴(>90)',   'F🟢(≥12)'):  '历史活跃已沉睡',
    ('R🔴(>90)',   'F🟡(3-11)'): '沉睡待救援',
    ('R🔴(>90)',   'F🔴(<3)'):   '低价值死户',
}


def nine_cell_label(r_b: str, f_b: str) -> str:
    return NINE_CELL_LABEL.get((r_b, f_b), '?')


# ══════════════════════════════════════════════════════════
# 兼容旧 API(逐步淘汰)
# ══════════════════════════════════════════════════════════

# 旧名映射 — 让既有代码 import 不破
_level = tier_of
_grade_R = r_grade
_grade_F = f_grade
_grade_M = m_growth_grade

# 旧的 R_HEALTHY / R_WARN 兼容(指向新单阈值)
R_HEALTHY = R_THRESHOLD
R_WARN = R_THRESHOLD
F_HEALTHY = F_VISUAL_HEALTHY
F_BAD = F_VISUAL_BAD
