"""
固定周期键 → 日期区间。

period_key 只有两种形态，与前端下拉框一一对应：
  '2026-W31' → 该 ISO 周的周一~周日
  '2026-07'  → 该月首日~末日

ISO 周用 date.fromisocalendar()，与前端 isoWeekOf()/isoWeekRange() 同口径
（前端已按 Python isocalendar 逐日校验过，含跨年周：2027-01-01 属 2026-W53）。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def period_range(period_key: str) -> Tuple[str, str]:
    """返回 (start, end)，均为 'YYYY-MM-DD'。格式非法抛 ValueError。"""
    key = (period_key or "").strip()

    m = WEEK_RE.match(key)
    if m:
        year, week = int(m.group(1)), int(m.group(2))
        try:
            # ISO 周一~周日；周号非法（如 W53 在只有 52 周的年份）会抛 ValueError
            start = date.fromisocalendar(year, week, 1)
            end = date.fromisocalendar(year, week, 7)
        except ValueError as exc:
            raise ValueError(f"非法周期键 {period_key!r}: {exc}")
        return start.isoformat(), end.isoformat()

    m = MONTH_RE.match(key)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"非法月份 {period_key!r}")
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1).isoformat(), date(year, month, last).isoformat()

    raise ValueError(f"period_key 必须形如 '2026-W31' 或 '2026-07'，收到 {period_key!r}")


# ── 周期归属：周 → 年月 + 月内份额 ──────────────────────────────────────────────
# 目标节奏（kpi_rhythm）只到月粒度，所以周目标 = 该周所属月的目标 ÷ 该月周数。
#
# 「周属于哪个月」用**周四**判定，与 ISO 周归年同一套规则（ISO 周属于其周四所在
# 的年）。跨月周（如周一在 6/29、周日在 7/5）整周归到周四那个月，不做按天摊分：
# 摊分会让周目标出现小数、且两个月的周数之和不再是整年周数。
# 该定义下每月周数 4 或 5，全年 12 个月周数之和 = 52 或 53，与 ISO 周总数吻合。


def week_month(year: int, week: int) -> Tuple[int, int]:
    """该 ISO 周归属的 (年, 月) —— 按周四所在月。"""
    thu = date.fromisocalendar(year, week, 4)
    return thu.year, thu.month


def weeks_in_month(year: int, month: int) -> int:
    """该月包含的 ISO 周数（周四落在本月的周）。"""
    return len(weeks_of_month(year, month))


def weeks_of_month(year: int, month: int) -> List[str]:
    """周四落在该月的 ISO 周键，形如 '2026-W31'。与周目标归属同一套规则。"""
    if not 1 <= month <= 12:
        raise ValueError(f"非法月份 {month}")
    keys: List[str] = []
    d = date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    while d.day <= last and d.month == month:
        if d.isoweekday() == 4:
            iso_y, iso_w, _ = d.isocalendar()
            keys.append(f"{iso_y}-W{iso_w:02d}")
        d += timedelta(days=1)
    return keys


def period_label(period_key: str) -> str:
    """总览/侧栏用的短标签：周 → W31，月 → 月度。"""
    key = (period_key or "").strip()
    m = WEEK_RE.match(key)
    if m:
        return f"W{int(m.group(2)):02d}"
    if MONTH_RE.match(key):
        return "月度"
    return key


def period_meta(period_key: str) -> Dict[str, Any]:
    """
    解析周期键，返回目标分解所需的元信息：

      {"type": "week"|"month", "year": 2026, "month": 7,
       "week": 31|None, "week_share": 0.2|None,
       "start": "...", "end": "..."}

    week_share = 1/该月周数，月周期为 None。year/month 是**目标归属**的年月
    （周按周四归属），不一定等于周期键里的年份。
    """
    start, end = period_range(period_key)
    key = (period_key or "").strip()

    m = WEEK_RE.match(key)
    if m:
        iso_year, week = int(m.group(1)), int(m.group(2))
        year, month = week_month(iso_year, week)
        n = weeks_in_month(year, month)
        return {"type": "week", "year": year, "month": month, "week": week,
                "week_share": round(1.0 / n, 6), "weeks_in_month": n,
                "start": start, "end": end}

    m = MONTH_RE.match(key)
    year, month = int(m.group(1)), int(m.group(2))
    return {"type": "month", "year": year, "month": month, "week": None,
            "week_share": None, "weeks_in_month": weeks_in_month(year, month),
            "start": start, "end": end}
