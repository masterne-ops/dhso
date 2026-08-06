#!/usr/bin/env python3
"""
⚔️ 大 PK
- 3 个 Tab：代理商 / 地市 / 区县
- 共用 5 维度雷达图 + Excel 风格五维卡片
- 每个 Tab 都用 st.form：选好后点「⚔️ 开战」才计算
- 红蓝方都有搜索框过滤候选
- 雷达图小尺寸 + 一键 PNG 下载
"""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 引入共享加载（跨 page 共享缓存）
sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import load_main_shared, load_redpack_shared  # noqa: E402

require_auth()
st.markdown("### ⚔️ 大 PK")
st.caption("三种对象的 PK 玩法：代理商 / 地市 / 区县。点击「开战」按钮才会计算，调整选项不会立即触发。")
st.caption(
    "💡 **口径**：销量主体用 **🌐 全量感知**（product_flow），服务商数量/签约比例用 **🎯 安装红包**（install_redpack）。"
)


# ──────────────────────────────────────────
# 共享数据
# ──────────────────────────────────────────

main_full = load_main_shared()
rp_full = load_redpack_shared()

if main_full.empty:
    st.warning("📦 主表暂无数据，请到主页『📥 数据导入』上传 Excel。")
    st.stop()

years = sorted([int(y) for y in main_full['上线年份'].dropna().unique() if 2024 <= y <= 2030])
if not years:
    st.warning("数据里没有有效年份")
    st.stop()


# ──────────────────────────────────────────
# 中文字体探测（雷达图用）
# ──────────────────────────────────────────

def get_chinese_font():
    import matplotlib.font_manager as fm
    candidate_paths = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/Library/Fonts/Songti.ttc',
        '/System/Library/Fonts/Supplemental/Songti.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/msyh.ttf',
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
    ]
    for p in candidate_paths:
        if Path(p).exists():
            try:
                return fm.FontProperties(fname=p, size=11)
            except Exception:
                continue
    chinese_only_kws = [
        'PingFang', 'STHeiti', 'STSong', 'STKaiti', 'STFangsong',
        'Microsoft YaHei', 'SimHei', 'SimSun', 'NSimSun', 'KaiTi', 'FangSong',
        'Noto Sans CJK', 'Noto Sans SC', 'Noto Sans TC',
        'Source Han Sans', 'Source Han Serif',
        'AR PL', 'WenQuanYi', 'Hiragino Sans GB',
    ]
    for font in fm.fontManager.ttflist:
        for kw in chinese_only_kws:
            if kw.lower() in font.name.lower():
                try:
                    return fm.FontProperties(fname=font.fname, size=11)
                except Exception:
                    pass
                break
    return None


# ──────────────────────────────────────────
# 工具：格式化、归一化、文件名安全化
# ──────────────────────────────────────────

def fmt_value(key, val):
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        if key == 'yoy':
            return "—（去年无基线）"
        return "—"
    if key == 'yoy':
        return f"{val * 100:+.1f}%"
    if key == 'loyal':
        return f"{val * 100:.1f}%"
    if key == 'amt':
        return f"¥{val:,.0f}"
    if key == 'days':
        return f"{val:.1f} 天"
    return f"{val:,}"


def _safe_num(v):
    """把 None / NaN / Inf 统一变成 0"""
    if v is None:
        return 0
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return 0
    return v


def normalize(a, b, lower_is_better=False, allow_negative=False):
    """两值归一化到 [0, 100]，最大值方为 100"""
    a_v = _safe_num(a)
    b_v = _safe_num(b)
    if allow_negative:
        # 同比可能为负，平移到正区间
        m = min(a_v, b_v, 0)
        a_v -= m
        b_v -= m
    if lower_is_better:
        mx = max(a_v, b_v)
        if mx == 0:
            return 100.0, 100.0
        return ((mx - a_v) / mx) * 100, ((mx - b_v) / mx) * 100
    mx = max(a_v, b_v)
    if mx == 0:
        return 0.0, 0.0
    return (a_v / mx) * 100, (b_v / mx) * 100


def safe_filename(s):
    return ''.join(c if c.isalnum() or c in '_-（）' else '_' for c in str(s))[:40]


# ──────────────────────────────────────────
# PK 计算逻辑（按 level 分支）
# ──────────────────────────────────────────

def compute_dealer_stats(name, year):
    sub = main_full[
        (main_full['出库客户名称'] == name)
        & (main_full['上线年份'] == year)
    ]
    rp_sub = (rp_full[(rp_full['上线年份'] == year) & (rp_full['所属一级客户'] == name)]
              if not rp_full.empty else pd.DataFrame())

    qty = len(sub)
    amt = float(sub['最新分销价'].sum()) if '最新分销价' in sub.columns else 0.0
    median_days = (sub['流通天数'].dropna().median()
                   if '流通天数' in sub.columns else float('nan'))
    n_provider = (rp_sub['上线客户编码'].nunique()
                  if not rp_sub.empty and '上线客户编码' in rp_sub.columns else 0)
    if not rp_sub.empty and '出货客户名称' in rp_sub.columns:
        loyal = float((rp_sub['出货客户名称'].astype(str).str.strip() == name).mean())
    else:
        loyal = float('nan')
    return {
        'qty': qty, 'amt': amt, 'days': median_days,
        'providers': n_provider, 'loyal': loyal,
    }


def compute_geo_stats(name, year, level):
    """level: 'city' / 'district'"""
    field_main = '上线城市' if level == 'city' else '上线区县_全'
    field_rp = '安装城市' if level == 'city' else '安装区县_全'

    sub = main_full[
        (main_full[field_main] == name)
        & (main_full['上线年份'] == year)
    ]
    sub_prev = main_full[
        (main_full[field_main] == name)
        & (main_full['上线年份'] == year - 1)
    ]

    qty = len(sub)
    amt = float(sub['最新分销价'].sum()) if '最新分销价' in sub.columns else 0.0
    qty_prev = len(sub_prev)

    # 同比：上一年无数据时返回 NaN（UI 会显示"—"）
    if qty_prev > 0:
        yoy = (qty - qty_prev) / qty_prev
    else:
        yoy = float('nan')

    # 活跃服务商数 / 代理商数（红包表）
    if not rp_full.empty and field_rp in rp_full.columns:
        rp_sub = rp_full[
            (rp_full[field_rp] == name)
            & (rp_full['上线年份'] == year)
        ]
        n_providers = (rp_sub['上线客户编码'].nunique()
                       if '上线客户编码' in rp_sub.columns else 0)
        n_dealers = (rp_sub['出货客户名称'].nunique()
                     if '出货客户名称' in rp_sub.columns else 0)
    else:
        n_providers = 0
        n_dealers = 0

    return {
        'qty': qty, 'amt': amt, 'yoy': yoy,
        'providers': n_providers, 'dealers': n_dealers,
    }


# ──────────────────────────────────────────
# 维度配置（不同 level 维度不同）
# ──────────────────────────────────────────

DIM_CONFIG = {
    'dealer': {
        'metrics': [
            ('出货台数', 'qty', False),
            ('出货金额', 'amt', False),
            ('流通天数中位', 'days', True),  # 越低越好
            ('签约服务商数', 'providers', False),
            ('服务商忠诚率', 'loyal', False),
        ],
        'radar_labels': ['出货台数', '出货金额', '流通效率', '服务商规模', '服务商忠诚率'],
    },
    'city': {
        'metrics': [
            ('SO 台数', 'qty', False),
            ('SO 金额', 'amt', False),
            ('同比增长率', 'yoy', False),
            ('活跃服务商数', 'providers', False),
            ('出货代理商数量', 'dealers', False),
        ],
        'radar_labels': ['SO 台数', 'SO 金额', '同比增长', '服务商数', '出货代理商数'],
    },
    'district': {
        'metrics': [
            ('SO 台数', 'qty', False),
            ('SO 金额', 'amt', False),
            ('同比增长率', 'yoy', False),
            ('活跃服务商数', 'providers', False),
            ('出货代理商数量', 'dealers', False),
        ],
        'radar_labels': ['SO 台数', 'SO 金额', '同比增长', '服务商数', '出货代理商数'],
    },
}


# ──────────────────────────────────────────
# 渲染单个 PK Tab（通用）
# ──────────────────────────────────────────

def render_pk(level, options, key_prefix, compute_fn, label_singular):
    """level: 'dealer' / 'city' / 'district'
    options: 候选项列表（按热度排序）
    key_prefix: streamlit widget key 前缀
    compute_fn: 计算函数 lambda name, year -> dict
    label_singular: '代理商' / '地市' / '区县'
    """
    if not options:
        st.warning(f"暂无 {label_singular} 数据")
        return

    # 红蓝方搜索（在 form 之外，输入时实时过滤候选）
    s_cols = st.columns(2)
    with s_cols[0]:
        blue_search = st.text_input(
            f"🟦 蓝方搜索（{label_singular}名片段）",
            placeholder="不输 = 显示全部",
            key=f"{key_prefix}_blue_search",
        )
    with s_cols[1]:
        red_search = st.text_input(
            f"🟥 红方搜索（{label_singular}名片段）",
            placeholder="不输 = 显示全部",
            key=f"{key_prefix}_red_search",
        )

    blue_options = (
        [d for d in options if blue_search.strip().lower() in str(d).lower()]
        if blue_search else options
    )
    red_options = (
        [d for d in options if red_search.strip().lower() in str(d).lower()]
        if red_search else options
    )
    if not blue_options:
        blue_options = ["（无匹配，请清空搜索）"]
    if not red_options:
        red_options = ["（无匹配，请清空搜索）"]

    # Form
    with st.form(f"{key_prefix}_form"):
        cols = st.columns([1, 2, 2])
        with cols[0]:
            year_pick = st.selectbox(
                "对战年份",
                options=years,
                index=len(years) - 1,
                key=f"{key_prefix}_year",
            )
        with cols[1]:
            d1 = st.selectbox(
                f"🟦 蓝方（候选 {len(blue_options)} 个）",
                options=blue_options,
                index=0,
                key=f"{key_prefix}_d1",
            )
        with cols[2]:
            d2 = st.selectbox(
                f"🟥 红方（候选 {len(red_options)} 个）",
                options=red_options,
                index=min(1, len(red_options) - 1),
                key=f"{key_prefix}_d2",
            )

        submitted = st.form_submit_button("⚔️ 开战！", type="primary", use_container_width=True)

    if not submitted:
        st.info(f"👆 选好年份和红蓝方{label_singular}后，点击「⚔️ 开战！」按钮开始 PK。")
        return

    if d1 == d2:
        st.warning(f"两方不能选同一{label_singular}，请重新选择。")
        return
    if "无匹配" in str(d1) or "无匹配" in str(d2):
        st.warning("有一方没有匹配项，请清空搜索后重选。")
        return

    with st.spinner("分析中…"):
        s1 = compute_fn(d1, year_pick)
        s2 = compute_fn(d2, year_pick)

    # 五维卡片
    st.markdown(f"#### 🥊 {year_pick} 年 · {d1}（蓝） vs {d2}（红）")

    metric_cols = st.columns(5)
    metrics = DIM_CONFIG[level]['metrics']
    for i, (label, key, lower_better) in enumerate(metrics):
        with metric_cols[i]:
            v1 = s1.get(key)
            v2 = s2.get(key)
            # 双方都无效 → 不分胜负
            v1_invalid = v1 is None or (isinstance(v1, float) and (np.isnan(v1) or np.isinf(v1)))
            v2_invalid = v2 is None or (isinstance(v2, float) and (np.isnan(v2) or np.isinf(v2)))
            v1d = _safe_num(v1)
            v2d = _safe_num(v2)
            st.markdown(f"**{label}**")
            st.write(f"🟦 {fmt_value(key, v1)}")
            st.write(f"🟥 {fmt_value(key, v2)}")
            if v1_invalid and v2_invalid:
                st.caption("⚖ 无可比")
            elif v1d == v2d:
                st.caption("⚖ 平")
            else:
                blue_win = (v1d <= v2d) if lower_better else (v1d >= v2d)
                st.caption("🟦 胜" if blue_win else "🟥 胜")

    # 雷达图
    st.markdown("---")
    st.markdown("##### 🎯 雷达图")

    blue_norm = []
    red_norm = []
    for _, key, lower_better in metrics:
        a, b = normalize(
            s1.get(key), s2.get(key),
            lower_is_better=lower_better,
            allow_negative=(key == 'yoy'),
        )
        # loyal 是 0~1，norm 后已经 0~100；但单独单位用 % 显示更直观——这里 normalize 已统一 [0,100]
        # 对于 loyal/yoy，乘 100 后用，还是用 normalize？保持 normalize 简单一致
        blue_norm.append(a)
        red_norm.append(b)

    labels = DIM_CONFIG[level]['radar_labels']

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        cn_font = get_chinese_font()
        matplotlib.rcParams['axes.unicode_minus'] = False

        N = len(labels)
        angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
        blue_close = blue_norm + blue_norm[:1]
        red_close = red_norm + red_norm[:1]
        angles_close = angles + angles[:1]

        fig, ax = plt.subplots(figsize=(4.5, 4.5), subplot_kw=dict(polar=True))
        ax.plot(angles_close, blue_close, 'o-', linewidth=1.8, color='#3B82F6', label=d1)
        ax.fill(angles_close, blue_close, alpha=0.25, color='#3B82F6')
        ax.plot(angles_close, red_close, 'o-', linewidth=1.8, color='#EF4444', label=d2)
        ax.fill(angles_close, red_close, alpha=0.25, color='#EF4444')

        if cn_font is not None:
            ax.set_thetagrids(np.degrees(angles), labels, fontproperties=cn_font, fontsize=9)
            ax.set_title(f"{year_pick} 年 {label_singular}擂台",
                          fontproperties=cn_font, fontsize=11, pad=16)
            leg = ax.legend(loc='upper right', bbox_to_anchor=(1.4, 1.1), fontsize=8)
            for text in leg.get_texts():
                text.set_fontproperties(cn_font)
                text.set_fontsize(8)
        else:
            st.warning("⚠️ 未找到系统中文字体，雷达图标签可能显示为方块。")
            ax.set_thetagrids(np.degrees(angles), labels, fontsize=9)
            ax.legend(loc='upper right', bbox_to_anchor=(1.4, 1.1), fontsize=8)
            ax.set_title(f"{year_pick} Battle", fontsize=11, pad=16)

        ax.set_ylim(0, 110)
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_yticklabels(['20', '40', '60', '80', '100'], fontsize=7)

        img_buf = io.BytesIO()
        fig.savefig(img_buf, format='png', dpi=180, bbox_inches='tight')
        img_buf.seek(0)
        plt.close(fig)

        show_cols = st.columns([1, 2, 1])
        with show_cols[1]:
            st.image(img_buf.getvalue(), use_container_width=True)

        fname = f"{level}_PK_{year_pick}_{safe_filename(d1)}_VS_{safe_filename(d2)}.png"
        st.download_button(
            "📥 下载雷达图（PNG）",
            data=img_buf.getvalue(),
            file_name=fname,
            mime="image/png",
            key=f"{key_prefix}_radar_dl",
        )
    except Exception as e:
        st.error(f"雷达图绘制失败：{e}")

    # 综合得分
    st.markdown("---")
    blue_score = sum(blue_norm)
    red_score = sum(red_norm)
    total = blue_score + red_score
    if total > 0:
        blue_pct = blue_score / total * 100
        red_pct = red_score / total * 100
        if blue_score > red_score:
            st.success(f"🏆 **综合得分**：🟦 {d1} 以 {blue_pct:.0f}% : {red_pct:.0f}% 胜出！")
        elif red_score > blue_score:
            st.success(f"🏆 **综合得分**：🟥 {d2} 以 {red_pct:.0f}% : {blue_pct:.0f}% 胜出！")
        else:
            st.info("⚖ 综合得分平局！")


# ──────────────────────────────────────────
# 三个 Tab
# ──────────────────────────────────────────

tab_dealer, tab_city, tab_district = st.tabs([
    "🏢 代理商 PK",
    "🏙️ 地市 PK",
    "🗺️ 区县 PK",
])

# 代理商候选（按出货台数倒序）
dealer_options = (
    main_full.dropna(subset=['出库客户名称'])
    .groupby('出库客户名称').size().sort_values(ascending=False).index.tolist()
)

# 地市候选（按 SO 台数倒序）
city_options = (
    main_full.dropna(subset=['上线城市'])
    .groupby('上线城市').size().sort_values(ascending=False).index.tolist()
)

# 区县候选（按 SO 台数倒序，使用「上线区县_全」避免不同城市同名区县混淆）
district_options = (
    main_full.dropna(subset=['上线区县_全'])
    .groupby('上线区县_全').size().sort_values(ascending=False).index.tolist()
)


with tab_dealer:
    st.caption("📋 维度：出货台数 / 出货金额 / 流通天数中位 / 签约服务商数 / 服务商忠诚率")
    render_pk(
        level='dealer',
        options=dealer_options,
        key_prefix='pk_dealer',
        compute_fn=compute_dealer_stats,
        label_singular='代理商',
    )

with tab_city:
    st.caption("📋 维度：SO 台数 / SO 金额 / 同比增长率 / 活跃服务商数 / 出货代理商数量")
    render_pk(
        level='city',
        options=city_options,
        key_prefix='pk_city',
        compute_fn=lambda name, year: compute_geo_stats(name, year, 'city'),
        label_singular='地市',
    )

with tab_district:
    st.caption(
        "📋 维度：SO 台数 / SO 金额 / 同比增长率 / 活跃服务商数 / 出货代理商数量 · "
        "区县名带城市前缀，避免不同城市同名区县混淆"
    )
    render_pk(
        level='district',
        options=district_options,
        key_prefix='pk_district',
        compute_fn=lambda name, year: compute_geo_stats(name, year, 'district'),
        label_singular='区县',
    )
