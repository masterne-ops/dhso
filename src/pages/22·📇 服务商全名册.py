#!/usr/bin/env python3
"""📇 服务商全名册

全省所有服务商一览，带：
  - 标签列（伞形/低效/无意向/竞品Top/马甲/下月重点 等）
  - 城市/区县/代理商/服务商等级 多维过滤
  - 按标签过滤（"显示所有 标签=低效签约 的服务商"）
  - 货值/红包数 快速排序
  - 点击客户名 → 跳转 page 05 服务商画像查看 & 打标

数据源：provider_profile + provider_contract + provider_tags_v 视图

权限：所有人都能看；scope 过滤生效。打标在 page 05 做。
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, is_admin, get_current_scope, filter_by_scope  # noqa: E402
from _loaders import (  # noqa: E402
    load_provider_master_shared, load_redpack_shared,
)
from _ai_log import (  # noqa: E402
    list_tag_defs, get_provider_tags_batch, codes_by_tag,
)
from _tag_widget import format_tag_text, format_tag_keys  # noqa: E402

require_auth()


st.markdown("### 📇 服务商全名册")
st.caption(
    "全省所有服务商一览。可按地市/标签/代理商过滤。"
    "**点击客户编码 → 复制到 🔭 服务商画像页 查看雷达 & 打标。**"
)


# ──────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────
@st.cache_data(ttl=600)
def _load_provider_target():
    """地市级服务商等级目标(provider_target,最新年度)。无 scope,可全局缓存。
    口径:V2/V3/V4及以上=本年上线金额分档,与原始服务商等级(v2~v5服务商)同口径。"""
    import sqlite3
    from _loaders import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return pd.read_sql(
            "SELECT 地市, 服务商签约数_含个人, 安装红包V2_家数, 安装红包V3_家数, 安装红包V4及以上_家数 "
            "FROM provider_target WHERE 年度=(SELECT MAX(年度) FROM provider_target)", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


# ⚠️ 不能直接用 @st.cache_data — 内部调用的 load_*_shared 已是 scope 过滤后的；
#    跨用户共用 cache 会让 admin 的全省数据泄露到业务员
def _load_master_with_metrics():
    """主档 + 红包 12 月聚合（底层 loader 已 scope 过滤）"""
    master = load_provider_master_shared()
    rp = load_redpack_shared()
    if master.empty:
        return pd.DataFrame()

    # 红包聚合（红包数 / 货值 / 最近上线日）
    if not rp.empty and '上线客户编码' in rp.columns:
        rp_g = rp.groupby('上线客户编码', observed=True).agg(
            红包数=('上线时间', 'count'),
            累计货值_元=('产品现有分销价', 'sum'),
            最近上线日=('上线时间', 'max'),
        ).reset_index()
        rp_g['累计货值_万'] = rp_g['累计货值_元'].fillna(0) / 10000
        rp_g = rp_g.rename(columns={'上线客户编码': '客户编码'})
        rp_g = rp_g[['客户编码', '红包数', '累计货值_万', '最近上线日']]
        rp_g['客户编码'] = rp_g['客户编码'].astype(str)
    else:
        rp_g = pd.DataFrame(columns=['客户编码', '红包数', '累计货值_万', '最近上线日'])

    out = master.copy()
    out['客户编码'] = out['客户编码'].astype(str)
    out = out.merge(rp_g, on='客户编码', how='left')
    return out


master_df = _load_master_with_metrics()
if master_df.empty:
    st.warning("⚠️ 主档为空 — 请先在「🏠 数据导入」上传 服务商管理沙盘 / 签约明细")
    st.stop()


# ──────────────────────────────────────────
# Scope 过滤（业务员只能看自己的地市/区县/代理商）
# ──────────────────────────────────────────
city_col = '地市' if '地市' in master_df.columns else None
dist_col = '区县' if '区县' in master_df.columns else None
dealer_col = '上级客户名称' if '上级客户名称' in master_df.columns else None
master_df = filter_by_scope(
    master_df,
    city_col=city_col, district_col=dist_col, dealer_col=dealer_col,
)


# ──────────────────────────────────────────
# 标签信息 — 批量查询并合并到主档
# ──────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner="🏷️ 加载标签...")
def _load_tags_for(codes_tuple):
    """缓存 batch tag 查询。tuple 化以便 hashable"""
    if not codes_tuple:
        return {}
    return get_provider_tags_batch(list(codes_tuple))


# 注意：codes 列表可能 >1万，传 tuple 给缓存
codes = tuple(master_df['客户编码'].dropna().astype(str).unique().tolist())
tag_map = _load_tags_for(codes)


def _tag_text_for(code):
    ts = tag_map.get(str(code), [])
    return format_tag_text(ts, max_count=4)


def _tag_keys_for(code):
    ts = tag_map.get(str(code), [])
    return format_tag_keys(ts)


master_df = master_df.copy()
master_df['🏷️ 标签'] = master_df['客户编码'].astype(str).map(_tag_text_for)
master_df['_tag_keys'] = master_df['客户编码'].astype(str).map(_tag_keys_for)


# ──────────────────────────────────────────
# 过滤器
# ──────────────────────────────────────────
fc1, fc2, fc3 = st.columns([2, 2, 2])
with fc1:
    cities = sorted(master_df[city_col].dropna().astype(str).unique()) if city_col else []
    selected_cities = st.multiselect(
        "城市", cities,
        placeholder="不选 = 全部",
    )

with fc2:
    if dist_col and selected_cities:
        d_pool = master_df[master_df[city_col].astype(str).isin(selected_cities)]
        districts = sorted(d_pool[dist_col].dropna().astype(str).unique())
    elif dist_col:
        districts = sorted(master_df[dist_col].dropna().astype(str).unique())
    else:
        districts = []
    selected_districts = st.multiselect(
        "区县", districts,
        placeholder="不选 = 全部",
    )

with fc3:
    if dealer_col:
        dealers = sorted(master_df[dealer_col].dropna().astype(str).unique())
        selected_dealers = st.multiselect(
            "签约代理商", dealers,
            placeholder="不选 = 全部",
        )
    else:
        selected_dealers = []

# 标签过滤
defs_df = list_tag_defs(only_enabled=True)
fc4, fc5, fc6 = st.columns([3, 2, 2])
with fc4:
    tag_options = [(r['tag_key'], f"{r['tag_icon']} {r['tag_name']}")
                    for _, r in defs_df.iterrows()]
    selected_tag_keys = st.multiselect(
        "🏷️ 必须含有这些标签（多选 = AND）",
        [k for k, _ in tag_options],
        format_func=lambda k: dict(tag_options).get(k, k),
        placeholder="不选 = 不过滤",
    )

with fc5:
    excl_tag_keys = st.multiselect(
        "🚫 排除这些标签",
        [k for k, _ in tag_options],
        format_func=lambda k: dict(tag_options).get(k, k),
        placeholder="不选 = 不排除",
    )

with fc6:
    # 服务商等级直接取明细表原始等级(服务商等级_原始)，不用系统按红包累计重算的「服务商等级」
    grade_col = None
    for c in ('服务商等级_原始', '服务商等级'):
        if c in master_df.columns:
            grade_col = c
            break
    if grade_col:
        grades = sorted(master_df[grade_col].dropna().astype(str).unique())
        selected_grades = st.multiselect(
            "服务商等级", grades, placeholder="不选 = 全部",
            help="取自服务商明细表的原始等级（V0-V5），非系统按红包累计重算",
        )
    else:
        selected_grades = []

# 签约/认证三态：认证(已签约·认证SMB) / 非认证(已签约·非认证) / 意向(未签约)。
# 口径：认证 = 渠道客户类型='认证SMB服务商'(同全景页,非「分销商认证/授牌」)；已签约 = 在签约表(_来源_签约)。
# 认证 + 非认证 = 已签约；三态互补 = 全量。
CERT_TYPE = '认证SMB服务商'
auth_col = next((c for c in ('渠道客户类型', '客户类型') if c in master_df.columns), None)
signed_col = '_来源_签约' if '_来源_签约' in master_df.columns else None
selected_cert = st.multiselect(
    "签约 / 认证状态", ['认证', '非认证', '意向'],
    placeholder="不选 = 全部（认证 + 非认证 + 意向）",
    help="认证 = 已签约·渠道客户类型='认证SMB服务商'；非认证 = 已签约·非认证；意向 = 未签约。"
         "勾「认证 + 非认证」即只看已签约。",
)


# 应用过滤
df = master_df.copy()
if city_col and selected_cities:
    df = df[df[city_col].astype(str).isin(selected_cities)]
if dist_col and selected_districts:
    df = df[df[dist_col].astype(str).isin(selected_districts)]
if dealer_col and selected_dealers:
    df = df[df[dealer_col].astype(str).isin(selected_dealers)]

# 签约概览基准:地理(地市/区县/代理商)筛选后、等级/认证状态/标签筛选前的快照
# —— 签约盘子完成率只随地理变,不被认证状态/等级/标签拆分
df_geo = df.copy()

if grade_col and selected_grades:
    df = df[df[grade_col].astype(str).isin(selected_grades)]
if selected_cert and auth_col:
    if signed_col:
        signed = df[signed_col].fillna(False).astype(bool)
    else:  # 无签约来源标记时退化：渠道客户类型非空 视为已签约
        signed = df[auth_col].astype(str).str.strip().replace('nan', '').ne('')
    is_cert = df[auth_col].astype(str).str.strip().eq(CERT_TYPE)
    cls = pd.Series('意向', index=df.index)
    cls[signed & is_cert] = '认证'
    cls[signed & ~is_cert] = '非认证'
    df = df[cls.isin(selected_cert)]

if selected_tag_keys:
    # 必须含所有勾选标签
    need = set(selected_tag_keys)
    def _has_all_tags(s):
        s = s or ''
        my = set(s.split(',')) if s else set()
        return need.issubset(my)
    df = df[df['_tag_keys'].apply(_has_all_tags)]

if excl_tag_keys:
    excl = set(excl_tag_keys)
    def _has_no_excl(s):
        s = s or ''
        my = set(s.split(',')) if s else set()
        return not (my & excl)
    df = df[df['_tag_keys'].apply(_has_no_excl)]


# ──────────────────────────────────────────
# 总览统计
# ──────────────────────────────────────────
n_total = len(df)
n_with_tag = (df['🏷️ 标签'].astype(str) != '').sum()
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("符合过滤的服务商", f"{n_total:,}")
mc2.metric("带标签的", f"{n_with_tag:,}",
           help="至少有一个标签（含规则自动算的）")
if '红包数' in df.columns:
    mc3.metric("有红包扫码的", f"{(df['红包数'].fillna(0) > 0).sum():,}")
if '累计货值_万' in df.columns:
    total_amt = float(df['累计货值_万'].fillna(0).sum())
    mc4.metric("累计货值合计", f"{total_amt:,.0f} 万",
               help="所有过滤后服务商的红包扫码累计货值")

# 签约目标概览（认证+非认证=已签约 / 签约总目标；地理范围内，不被认证状态/等级/标签筛选拆）
if auth_col and signed_col:
    _sg = df_geo[signed_col].fillna(False).astype(bool)
    _ic = df_geo[auth_col].astype(str).str.strip().eq(CERT_TYPE)
    n_cert = int((_sg & _ic).sum())
    n_noncert = int((_sg & ~_ic).sum())
    n_signed = n_cert + n_noncert
    n_intent = int((~_sg).sum())
    # 签约目标(同各等级范围判定:选了区县/代理商→无地市级目标)
    _t = _load_provider_target()
    sign_tgt = None
    if (not _t.empty and not selected_districts and not selected_dealers
            and '服务商签约数_含个人' in _t.columns):
        _sub = (_t[_t['地市'].isin(selected_cities)] if selected_cities
                else _t[_t['地市'] == '浙江合计'])
        if not _sub.empty:
            sign_tgt = int(_sub['服务商签约数_含个人'].sum())

    def _ov_cell(label, val, sub_html):
        return (f"<div style='line-height:1.35;padding:2px 0'>"
                f"<div style='font-size:0.76rem;color:#888'>{label}</div>"
                f"<div style='font-size:1.5rem;font-weight:600'>{val:,}</div>"
                f"<div style='font-size:0.72rem;color:#999'>{sub_html}</div></div>")

    if sign_tgt:
        _r = n_signed / sign_tgt if sign_tgt else 0
        _c = '#22a565' if _r >= 1 else ('#e8833a' if _r >= 0.6 else '#d04437')
        _signed_sub = f"目标 {sign_tgt:,} · <b style='color:{_c}'>{_r*100:.0f}%</b>"
    else:
        _signed_sub = "区县/代理商粒度无目标"
    _pc = lambda x: (f"占已签约 {x/n_signed*100:.0f}%" if n_signed else "—")
    st.caption("📝 签约目标（认证 + 非认证 = 已签约 · 当前地市范围）")
    sc = st.columns(4)
    sc[0].markdown(_ov_cell("已签约(认证+非认证)", n_signed, _signed_sub), unsafe_allow_html=True)
    sc[1].markdown(_ov_cell("认证", n_cert, _pc(n_cert)), unsafe_allow_html=True)
    sc[2].markdown(_ov_cell("非认证", n_noncert, _pc(n_noncert)), unsafe_allow_html=True)
    sc[3].markdown(_ov_cell("意向(未签约)", n_intent, "对应预测池子，非签约目标"), unsafe_allow_html=True)

# 各等级数量 + 目标 + 完成率（原始等级与 provider_target 同口径 = 本年上线金额分档）
if grade_col:
    gc = df[grade_col].dropna().astype(str)
    gc = gc[~gc.str.strip().isin(['', 'nan', '(空)'])]
    grade_counts = gc.value_counts().sort_index()
    if not grade_counts.empty:
        # 目标范围:选了区县/代理商 → 无地市级目标;只选地市 → 求和;都没选 → 浙江合计
        tgt_df = _load_provider_target()
        tgt = {}
        if not tgt_df.empty and not selected_districts and not selected_dealers:
            sub = (tgt_df[tgt_df['地市'].isin(selected_cities)] if selected_cities
                   else tgt_df[tgt_df['地市'] == '浙江合计'])
            if not sub.empty:
                tgt = {'v2服务商': int(sub['安装红包V2_家数'].sum()),
                       'v3服务商': int(sub['安装红包V3_家数'].sum()),
                       'v4服务商': int(sub['安装红包V4及以上_家数'].sum())}  # V4及以上(含v5)
        v4n = int(grade_counts.get('v4服务商', 0))
        v5n = int(grade_counts.get('v5服务商', 0))
        cap = "🎚️ 各等级数量（原始等级 · 当前过滤范围）"
        cap += "｜目标 = provider_target 本年口径" if tgt else "｜区县/代理商粒度无目标，仅显示数量"
        st.caption(cap)

        def _grade_cell(g, n):
            n = int(n)
            rate, sub = None, "—"
            if g == 'v4服务商' and tgt.get('v4服务商'):       # 目标为 V4及以上，完成率用 v4+v5
                t = tgt['v4服务商']; rate = (v4n + v5n) / t if t else None; sub = f"目标 {t:,}(V4+)"
            elif g == 'v5服务商' and tgt.get('v4服务商'):
                sub = "计入 V4+"
            elif tgt.get(g):
                t = tgt[g]; rate = n / t if t else None; sub = f"目标 {t:,}"
            if rate is not None:
                col = '#22a565' if rate >= 1 else ('#e8833a' if rate >= 0.6 else '#d04437')
                sub += f" · <b style='color:{col}'>{rate*100:.0f}%</b>"
            return (f"<div style='line-height:1.35;padding:2px 0'>"
                    f"<div style='font-size:0.76rem;color:#888'>{g}</div>"
                    f"<div style='font-size:1.5rem;font-weight:600'>{n:,}</div>"
                    f"<div style='font-size:0.72rem;color:#999'>{sub}</div></div>")

        gcols = st.columns(min(7, len(grade_counts)))
        for i, (g, n) in enumerate(grade_counts.items()):
            gcols[i % len(gcols)].markdown(_grade_cell(g, n), unsafe_allow_html=True)


# ──────────────────────────────────────────
# 主表
# ──────────────────────────────────────────
display_cols = ['客户编码']
# 客户名称取沙盘 公司名称，fallback 签约 客户名称
if '公司名称' in df.columns:
    display_cols.append('公司名称')
elif '外部客户名称' in df.columns:
    display_cols.append('外部客户名称')
elif '客户名称' in df.columns:
    display_cols.append('客户名称')

if city_col:
    display_cols.append(city_col)
if dist_col:
    display_cols.append(dist_col)
if dealer_col:
    display_cols.append(dealer_col)
if grade_col:
    display_cols.append(grade_col)
display_cols.append('🏷️ 标签')
for c in ('红包数', '累计货值_万', '最近上线日'):
    if c in df.columns:
        display_cols.append(c)
# 老板 / 联系人快览
for c in ('老板姓名', '老板电话', '联系人', '联系电话'):
    if c in df.columns:
        display_cols.append(c)

display_cols = [c for c in display_cols if c in df.columns]

# 排序：累计货值降序
sort_col = '累计货值_万' if '累计货值_万' in df.columns else display_cols[0]
sort_df = df[display_cols].sort_values(sort_col, ascending=False, na_position='last')

st.markdown(f"##### 📋 名册（{n_total:,} 家）")
if sort_df.empty:
    st.info("（没有符合条件的服务商）")
else:
    # 渲染日期为 yyyy-mm-dd 字符串
    sort_df = sort_df.copy()
    # 等级列统一显示为「服务商等级」(底层是明细表原始 服务商等级_原始)
    if grade_col and grade_col != '服务商等级' and grade_col in sort_df.columns:
        sort_df = sort_df.rename(columns={grade_col: '服务商等级'})
    if '最近上线日' in sort_df.columns:
        sort_df['最近上线日'] = pd.to_datetime(
            sort_df['最近上线日'], errors='coerce'
        ).dt.strftime('%Y-%m-%d')
    if '累计货值_万' in sort_df.columns:
        sort_df['累计货值_万'] = sort_df['累计货值_万'].fillna(0).round(2)

    st.dataframe(
        sort_df,
        use_container_width=True, hide_index=True,
        height=min(700, 40 + 35 * len(sort_df)),
        column_config={
            '客户编码': st.column_config.TextColumn(
                '客户编码', help='点击复制 → 到 🔭 服务商画像页选择该客户',
            ),
            '🏷️ 标签': st.column_config.TextColumn('🏷️ 标签', width='medium'),
            '累计货值_万': st.column_config.NumberColumn(
                '累计货值(万)', format='%.2f',
                help='红包扫码统计的产品现有分销价 / 万',
            ),
        },
    )

    # 下载
    csv = sort_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        "⬇️ 下载当前名册 CSV",
        csv,
        file_name=f"服务商全名册_{len(sort_df)}家.csv",
        mime='text/csv',
    )


# ──────────────────────────────────────────
# 标签 quick stats
# ──────────────────────────────────────────
st.divider()
st.markdown("##### 📊 当前过滤范围内各标签覆盖")
if defs_df.empty:
    st.info("（无标签）")
else:
    cnt_per_tag = {}
    for k in defs_df['tag_key']:
        # 计当前过滤后含 k 的服务商数
        cnt_per_tag[k] = df['_tag_keys'].apply(
            lambda s: k in (s or '').split(',') if s else False
        ).sum()

    qc = st.columns(min(6, len(defs_df)))
    for i, (_, r) in enumerate(defs_df.iterrows()):
        with qc[i % len(qc)]:
            st.metric(
                f"{r['tag_icon']} {r['tag_name']}",
                f"{cnt_per_tag.get(r['tag_key'], 0):,}",
            )
