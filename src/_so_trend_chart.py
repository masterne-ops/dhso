"""SO 月度走势图 — 全省/地市/区县/代理商全景共用。

matplotlib 折线(台数) + 每月节点标注「台数/金额万」(字小) + 黄底高亮当前评估时段 + 标最高点。
df 需含列: x_col(月份) / 台数 / 金额_万。返回 matplotlib fig,调用方 st.pyplot(fig)。
"""
import os

_FONT_READY = False


def _ensure_font():
    global _FONT_READY
    if _FONT_READY:
        return
    import matplotlib
    from matplotlib import font_manager
    for fp in ['/System/Library/Fonts/PingFang.ttc',
               '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
               '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
               'C:/Windows/Fonts/msyh.ttc']:
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            matplotlib.rcParams['font.sans-serif'] = [font_manager.FontProperties(fname=fp).get_name()]
            break
    matplotlib.rcParams['axes.unicode_minus'] = False
    _FONT_READY = True


def render_so_trend(df, x_col='月份', period_start=None, period_end=None):
    """SO 月度走势:台数折线 + 每月节点标注「台数/金额万」(字小)。
    df 需含列 x_col / 台数 / 金额_万。返回 matplotlib fig。"""
    _ensure_font()
    import matplotlib.pyplot as plt
    xs = df[x_col].astype(str).tolist()
    ys_t = [int(v) for v in df['台数'].tolist()]
    ys_a = [float(v) for v in df['金额_万'].tolist()]
    fig, ax = plt.subplots(figsize=(11, 3.4))
    ax.plot(range(len(xs)), ys_t, marker='o', color='#134074', linewidth=2, markersize=4)
    # 黄底高亮当前评估时段
    if period_start in xs and period_end in xs:
        ax.axvspan(xs.index(period_start) - 0.4, xs.index(period_end) + 0.4,
                   color='#FFD54F', alpha=0.45)
    # 每月节点标注:台数 / 金额万(字小)
    for i, (t, a) in enumerate(zip(ys_t, ys_a)):
        ax.annotate(f"{t:,}台\n{a:.0f}万", (i, t), textcoords="offset points",
                    xytext=(0, 7), ha='center', fontsize=5.5, color='#444',
                    linespacing=0.9)
    # 标最高台数点
    if ys_t:
        mxi = ys_t.index(max(ys_t))
        ax.scatter([mxi], [max(ys_t)], color='#C2410C', zorder=5, s=30)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, rotation=45, ha='right', fontsize=7)
    ax.set_ylabel('上线台数', fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    for sp in ['top', 'right']:
        ax.spines[sp].set_visible(False)
    ax.margins(y=0.20)  # 顶部留空给节点标注
    fig.tight_layout()
    return fig
