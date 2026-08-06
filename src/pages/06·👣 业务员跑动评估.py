#!/usr/bin/env python3
"""👣 业务员跑动评估 — RFM-Impact 两步框架

Step 1 · 跑动选择合理性:
    ✅ 主动救援 / 健康维护 / 新客探访
    ⚠️ 选择失误 = 代理商办公室打卡 + 漏跑(V3+ 严重 / V2 一般)
    🎭 马甲拜访仅标识,不算选择失误

Step 2 · 跑动结果(7 类):
    ✅ 救援成功    ❌ 救援失败
    🟢 正常        🚨 流失
    🌱 新客探访
    ⚠️ 代理商办公室
    ⏳ 待观察(拜访 + 30 天超 DB 末日)

单一阈值(_metrics_rfm):
    R_THRESHOLD = 30(健康/流失分界 + 救援/维护意图分界)
    EFFECT_WINDOW_DAYS = 30
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH, scoped_cities  # noqa: E402
from _visit_rfm_eval import (  # noqa: E402
    evaluate_salesperson,
    list_all_salespeople,
    overview_all_salespeople,
)
from _monthly_city_report import list_cities, list_months  # noqa: E402
from _ai_log import (  # noqa: E402
    mark_invalid_clients,
    revoke_invalid_mark,
    list_invalid_marks,
)

require_auth()

st.markdown("### 👣 业务员跑动评估")
st.caption(
    "**两步框架**：① 跑动选择合理性（救援/维护/探访 vs 选择失误） "
    "② 跑动结果（首拜访 + 30 天内是否激活 / 当期末 R 是否退化）。 "
    "把客户从「待救援」状态拉回「健康」状态、防止「健康」滑到「待救援」，就是业务员的核心价值。"
)

# 顶部 metric 紧凑
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.0rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# 1. 顶部选参
# ──────────────────────────────────────────────
try:
    cities = scoped_cities(list_cities())   # 🔒 按当前用户 scope 过滤候选城市
    min_month, max_month = list_months()
except Exception as e:
    st.error(f"读 DB 失败：{e}")
    st.stop()

if not cities:
    st.warning("⚠️ 您的 scope 没有任何可访问的地市，请联系管理员配置数据权限。")
    st.stop()


def month_range(start: str, end: str) -> list[str]:
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    return [str(s + i) for i in range((e - s).n + 1)]


all_months = month_range(min_month, max_month)

col1, col2, col3 = st.columns([2, 2, 2])
with col1:
    city = st.selectbox(
        "📍 地市",
        cities,
        index=cities.index('杭州市') if '杭州市' in cities else 0,
    )
with col2:
    period_start = st.selectbox(
        "📅 开始月份",
        all_months,
        index=max(0, len(all_months) - 4),
    )
with col3:
    period_end = st.selectbox(
        "📅 结束月份",
        all_months,
        index=len(all_months) - 1,
    )

if period_start > period_end:
    st.error("开始月份必须 ≤ 结束月份")
    st.stop()


# ──────────────────────────────────────────────
# 2. 缓存层 — 全市评估
# ──────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner="正在评估全市业务员跑动……")
def cached_overview(city: str, period_start: str, period_end: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return overview_all_salespeople(conn, city, period_start, period_end)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner="正在评估该业务员……")
def cached_evaluate(city: str, sp: str, period_start: str, period_end: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        r = evaluate_salesperson(conn, city, sp, period_start, period_end)
        return r
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def cached_sp_list(city: str, period_start: str, period_end: str) -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return list_all_salespeople(conn, city, period_start, period_end)
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 3. Tabs：全市排行榜 / 单业务员钻取 / 无效标记历史
# ──────────────────────────────────────────────
tab_rank, tab_drill, tab_invalid = st.tabs([
    "🏆 全市排行榜", "🔍 单业务员钻取", "📋 无效标记历史",
])

# ────────── Tab 1：全市排行榜 ──────────
with tab_rank:
    df = cached_overview(city, period_start, period_end)
    if df.empty:
        st.info(f"{city} 在 {period_start} ~ {period_end} 没有跑动记录。")
    else:
        # 衍生:选择合理 = 主动救援 + 健康维护 + 新客探访
        df['✅ 选择合理'] = df['sel_主动救援'] + df['sel_健康维护'] + df['sel_新客探访']
        df['⚠️ 代理商办公室'] = df['sel_代理商办公室']
        df['🎭 含马甲'] = df['sel_马甲拜访']

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("业务员数", len(df))
        c2.metric("拜访客户总数", int(df['拜访客户数'].sum()))
        c3.metric("🚨 严重漏跑总数", int(df['🚨 严重漏跑'].sum()))
        c4.metric("⚠️ 一般漏跑总数", int(df['⚠️ 一般漏跑'].sum()))

        st.markdown("#### 排行榜(按救援成功 + 正常 降序)")
        df['_有效产出'] = df['✅ 救援成功'] + df['🟢 正常']
        df_sorted = df.sort_values(
            ['_有效产出', '🚨 严重漏跑'], ascending=[False, True],
        ).drop(columns=['_有效产出'])

        display_cols = [
            '业务员', '所属', '负责区县', '拜访客户数',
            '✅ 选择合理', '⚠️ 代理商办公室', '🎭 含马甲',
            '🚨 严重漏跑', '⚠️ 一般漏跑',
            '✅ 救援成功', '❌ 救援失败',
            '🟢 正常', '🚨 流失',
            '⏳ 待观察',
        ]
        st.dataframe(
            df_sorted[display_cols],
            use_container_width=True,
            hide_index=True,
            height=min(600, 40 + 35 * len(df_sorted)),
        )

        # 下载
        csv = df_sorted[display_cols].to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "⬇️ 下载排行榜 CSV",
            csv,
            file_name=f"业务员跑动排行_{city}_{period_start}_{period_end}.csv",
            mime='text/csv',
        )


# ────────── Tab 2：单业务员钻取 ──────────
with tab_drill:
    sps = cached_sp_list(city, period_start, period_end)
    if not sps:
        st.info(f"{city} 在 {period_start} ~ {period_end} 没有跑动记录。")
    else:
        sp = st.selectbox("👤 选择业务员", sps, key='drill_sp')
        result = cached_evaluate(city, sp, period_start, period_end)
        s = result['summary']
        visits = result['visits_df']
        missed = result['missed_df']

        # ─── Step 1: 跑动选择合理性 ───
        st.markdown(f"#### Step 1 · 跑动选择合理性 · {sp}")
        st.caption(
            f"负责区县：{', '.join(sorted(result['sp_districts'])) if result['sp_districts'] else '—'} | "
            f"负责代理商：{', '.join(sorted(result['sp_dealers'])) if result['sp_dealers'] else '—'}"
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("总拜访客户", s['visits_total'])
        c2.metric("✅ 主动救援", s['sel_主动救援'])
        c3.metric("✅ 健康维护", s['sel_健康维护'])
        c4.metric("✅ 新客探访", s['sel_新客探访'])
        c5.metric("⚠️ 代理商办公室", s['sel_代理商办公室'])

        c6, c7, c8 = st.columns(3)
        c6.metric("🚨 严重漏跑", s['missed_严重'])
        c7.metric("⚠️ 一般漏跑", s['missed_一般'])
        c8.metric("🎭 马甲拜访", s['sel_马甲拜访'])

        # ─── Step 2:跑动结果 ───
        st.markdown(f"#### Step 2 · 跑动结果(7 类)")
        st.caption(
            f"可评估数 = {s['eval_可评数']}(首拜 + 30 天 ≤ 数据末日的样本,"
            f"窗口超数据末日的 {s['eval_待观察']} 个标为 ⏳ 待观察)"
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("✅ 救援成功", s['res_救援成功'])
        c2.metric("❌ 救援失败", s['res_救援失败'])
        c3.metric("🟢 正常", s['res_正常'])
        c4.metric("🚨 流失", s['res_流失'])

        st.markdown("---")
        sub_tabs = st.tabs([
            "📑 拜访明细",
            "🚨 漏跑名单",
            "🛡️ 一键标记无效",
        ])

        # ── 2.1 拜访明细 ──
        with sub_tabs[0]:
            if visits.empty:
                st.info("当期无有效拜访。")
            else:
                fcol1, fcol2 = st.columns([2, 2])
                with fcol1:
                    cls_filter = st.multiselect(
                        "筛选分类",
                        sorted(visits['分类'].dropna().unique()),
                        default=[],
                    )
                with fcol2:
                    intent_filter = st.multiselect(
                        "筛选意图",
                        sorted(visits['拜访意图'].dropna().unique()),
                        default=[],
                    )
                show = visits.copy()
                if cls_filter:
                    show = show[show['分类'].isin(cls_filter)]
                if intent_filter:
                    show = show[show['拜访意图'].isin(intent_filter)]

                show['首次拜访日'] = show['首次拜访日'].dt.strftime('%Y-%m-%d')
                cols = [
                    '分类', '客户名称', '区县', '首次拜访日',
                    '当期拜访次数', 'SP历史拜访次数',
                    '拜访意图', '结果', '依据', '马甲标识',
                ]
                # 加只读「🏷️ 标签」列（先理解再打 — 打标在 page 05 服务商画像）
                from _tag_widget import attach_tags_column as _attach_tags
                show_with_tags = _attach_tags(show, code_col='客户编码')
                show_cols = cols + (['🏷️ 标签']
                                     if '🏷️ 标签' in show_with_tags.columns else [])
                st.dataframe(
                    show_with_tags[show_cols],
                    use_container_width=True, hide_index=True,
                    height=min(600, 40 + 35 * len(show)),
                )
                st.caption(
                    "💡 想给某个服务商打标？→ 打开 **🔭 服务商画像** 页面，"
                    "搜索该客户名，在「🏷️ 标签」栏一键添加/撤销。"
                )

                csv = show[cols].to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    "⬇️ 下载拜访明细 CSV",
                    csv,
                    file_name=f"{sp}_拜访明细_{period_start}_{period_end}.csv",
                    mime='text/csv',
                )

        # ── 2.2 漏跑名单 ──
        with sub_tabs[1]:
            if missed.empty:
                st.success("🎉 本期内没有 scope 客户 R 从 ≤60 滑落到 >60 — 没有漏跑。")
            else:
                st.caption(
                    "**漏跑定义**：scope 内客户在本期开始时 R ≤60（还活着），到当期末 R >60（沉睡），且 SP 当期没拜访过。"
                    "  🚨 严重 = V3/V4（累计货值 ≥ 1 万）；⚠️ 一般 = 已激活/V2。"
                )
                show = missed.copy()
                cols = [
                    '严重度', '客户名称', '区县', '等级',
                    '期初_R', '期末_R', 'F_12mo', '累计货值_万', '马甲标识',
                ]
                from _tag_widget import attach_tags_column as _attach_tags
                show_with_tags = _attach_tags(show, code_col='客户编码')
                show_cols = cols + (['🏷️ 标签']
                                     if '🏷️ 标签' in show_with_tags.columns else [])
                st.dataframe(
                    show_with_tags[show_cols],
                    use_container_width=True, hide_index=True,
                    height=min(600, 40 + 35 * len(show)),
                )
                st.caption(
                    "💡 想给某个服务商打标？→ 打开 **🔭 服务商画像** 页面，"
                    "搜索该客户名，在「🏷️ 标签」栏一键添加/撤销。"
                )

                csv = show[cols].to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    "⬇️ 下载漏跑名单 CSV",
                    csv,
                    file_name=f"{sp}_漏跑名单_{period_start}_{period_end}.csv",
                    mime='text/csv',
                )

        # ── 2.3 一键标记无效 ──
        with sub_tabs[2]:
            st.markdown("##### 把救援未果的客户标记为「无效」")
            st.caption(
                "把「B1/B2 救援未果」类拜访的客户从未来运营中剥离（不计算同环比、不参与派单评分）。 "
                "标记后可在「📋 无效标记历史」Tab 撤销。"
            )

            cand = visits[visits['分类'].str.startswith('B', na=False)].copy()
            if cand.empty:
                st.info("本期无 B1/B2 救援未果案例。")
            else:
                cand['首次拜访日_str'] = cand['首次拜访日'].dt.strftime('%Y-%m-%d')
                already = list_invalid_marks(city=city, active_only=True, limit=10000)
                already_codes = set(already['客户编码'].astype(str)) if not already.empty else set()

                rows = []
                for _, r in cand.iterrows():
                    code = str(r['客户编码'])
                    if code in already_codes:
                        prefix = "✅ 已标记"
                    else:
                        prefix = ""
                    rows.append({
                        '勾选': False,
                        '状态': prefix,
                        '客户编码': code,
                        '客户名称': r['客户名称'],
                        '分类': r['分类'],
                        '首次拜访日': r['首次拜访日_str'],
                        '区县': r['区县'],
                        '依据': r['依据'],
                    })
                edit_df = pd.DataFrame(rows)

                edited = st.data_editor(
                    edit_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        '勾选': st.column_config.CheckboxColumn(required=False),
                        '状态': st.column_config.TextColumn(disabled=True),
                        '客户编码': st.column_config.TextColumn(disabled=True),
                        '客户名称': st.column_config.TextColumn(disabled=True),
                        '分类': st.column_config.TextColumn(disabled=True),
                        '首次拜访日': st.column_config.TextColumn(disabled=True),
                        '区县': st.column_config.TextColumn(disabled=True),
                        '依据': st.column_config.TextColumn(disabled=True),
                    },
                    key=f'invalid_editor_{sp}_{period_start}_{period_end}',
                )

                to_mark = edited[(edited['勾选'] == True) & (edited['状态'] == '')]
                if st.button(
                    f"🛡️ 标记选中的 {len(to_mark)} 个客户为「救援无效」",
                    type='primary',
                    disabled=len(to_mark) == 0,
                ):
                    records = []
                    for _, r in to_mark.iterrows():
                        reason = (
                            '救援未果(R已恶化)' if 'B1' in r['分类']
                            else '救援未果(R预警)' if 'B2' in r['分类']
                            else '手动'
                        )
                        records.append({
                            '客户编码': r['客户编码'],
                            '客户名称': r['客户名称'],
                            '城市': city,
                            '区县': r['区县'],
                            '标记业务员': sp,
                            '标记原因': reason,
                            '评估期间_start': period_start,
                            '评估期间_end': period_end,
                        })
                    n = mark_invalid_clients(records=records)
                    st.success(f"✅ 已标记 {n} 个客户为「救援无效」")
                    st.cache_data.clear()
                    st.rerun()


# ────────── Tab 3：无效标记历史 ──────────
with tab_invalid:
    st.markdown(f"#### {city} · 当前生效的无效标记")
    show_all = st.checkbox("显示已撤销的", value=False)
    marks = list_invalid_marks(city=city, active_only=not show_all, limit=2000)

    if marks.empty:
        st.info("暂无标记记录。")
    else:
        st.caption(f"共 {len(marks)} 条")

        # 给每行一个撤销按钮
        for _, r in marks.iterrows():
            cols = st.columns([1, 3, 1, 1, 2, 2, 2, 1])
            cols[0].text(f"#{r['id']}")
            cols[1].text(r['客户名称'])
            cols[2].text(r['区县'] or '—')
            cols[3].text(r['标记业务员'] or '—')
            cols[4].text(r['标记原因'] or '—')
            cols[5].text(r['标记时间'][:10] if r['标记时间'] else '')
            if r['撤销时间']:
                cols[6].text(f"❌ 已撤销 {r['撤销时间'][:10]}")
                cols[7].text('—')
            else:
                cols[6].text(f"📅 {r['评估期间_start']}~{r['评估期间_end']}")
                if cols[7].button("撤销", key=f"revoke_{r['id']}"):
                    revoke_invalid_mark(int(r['id']), reason='页面撤销')
                    st.cache_data.clear()
                    st.rerun()

        # 下载全部
        csv = marks.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "⬇️ 下载标记记录 CSV",
            csv,
            file_name=f"无效标记记录_{city}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv',
        )


# ──────────────────────────────────────────────
# 4. 底部说明
# ──────────────────────────────────────────────
with st.expander("ℹ️ 评估方法说明（点击展开）"):
    st.markdown("""
**两步框架**

| Step | 视角 | 评估对象 |
|------|------|---------|
| Step 1 · 选择 | 拜访意图是否合理 | 全部当期拜访 |
| Step 2 · 结果 | 拜访后是否产生影响 | 首拜访 + 30 天 ≤ DB 末日的拜访 |

**Step 1 分类**

- ✅ **主动救援**:客户 R > 30 天 或 F < 3 → 业务员需要救
- ✅ **健康维护**:客户 R ≤ 30 天 且 F ≥ 3 → 业务员需要稳
- ✅ **新客探访**:客户历史无上线 → 前 2 次拜访合理(培育期)
- ⚠️ **代理商办公室打卡**:拜访客户名 = 该业务员负责的代理商 → 选择失误
- 🚨 **严重漏跑(V3+)**:scope 内 V3/V4 客户期初 R≤30 → 期末 R>30,且 SP 未拜访
- ⚠️ **一般漏跑(已激活/V2)**:scope 内 V2/已激活 客户期初 R≤30 → 期末 R>30,且 SP 未拜访
- 🎭 **马甲拜访**:标识但不算选择失误

**Step 2 分类(7 类,合并精简后)**

- ✅ **救援成功**:救援意图 + 30 天内有激活
- ❌ **救援失败**:救援意图 + 30 天内无激活
- 🟢 **正常**:维护意图 + 拜访后客户期末仍 R≤30
- 🚨 **流失**:维护意图 + 拜访后客户期末已 R>30
- 🌱 **新客探访**:历史无上线,暂不评结果
- ⚠️ **代理商办公室打卡**:选择失误
- ⏳ **待观察**:首拜访 + 30 天超过数据库最新日 → 暂无法评估

**单一阈值**

| 维度 | 阈值 |
|------|------|
| R(最近上线距今天数)| **30 天** — 健康 / 流失分界,也是救援/维护意图分界 |
| 效果窗口 | **30 天** — 拜访后多少天内有上线 = 成功 |
| F(救援辅助)| F < 3 也算救援意图 |

**客户状态(全系统统一)**

- 🟢 活跃:R ≤ 30
- 🚨 流失:R > 30 且累计货值 > 0
- 💤 未激活:累计货值 = 0
""")
