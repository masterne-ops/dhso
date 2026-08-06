#!/usr/bin/env python3
"""📊 产品维度经营报告 — 选评估期一键生成双 HTML 报告

工作流(纯数据生成,不走 V3 沙箱):
  1. UI: 选评估期起 / 评估期止 / 城市(可选)
  2. 后端:用 SQL 查全量数据(子系列 × 区县/代理商/服务商 + 渠道健康度 + 焦点系列)
  3. 渲染 2 份 HTML:
     - 概览版(~40 KB,问题清单,简短汇报用)
     - 全量版(~3 MB,JS 交互筛选,详细分析用)
  4. UI 提供下载 + 内嵌预览

不依赖 LLM / Docker 沙箱 — 数据驱动,3-10 秒出报告。
"""
import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _product_report_runner import run as gather_data  # noqa: E402
from _product_report_html import render_report as render_v4_html  # noqa: E402
from _product_report_html_overview import render as render_overview_html  # noqa: E402

st.set_page_config(page_title="产品维度报告", layout="wide")
require_auth()

st.markdown("### 📊 产品维度经营报告")
st.caption(
    "以「产品子系列-新」为主轴,沿 区县 / 代理商 / 服务商 三个维度展开,"
    "识别增长 / 下降来源 + 自动诊断问题。"
    "**两份 HTML**:概览版(简短汇报)+ 全量版(JS 交互筛选)。"
)

# ────────────────────────────────────────────────
# 路径
PROJECT_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / 'reports' / 'product'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────
# 参数选择
# ────────────────────────────────────────────────

# 月份范围(从 DB 取)
@st.cache_data(ttl=600)
def get_month_range():
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT MIN(上线年月) AS mn, MAX(上线年月) AS mx FROM install_redpack_v",
        conn,
    )
    conn.close()
    return df.iloc[0]['mn'], df.iloc[0]['mx']


@st.cache_data(ttl=600)
def get_cities():
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("""
        SELECT DISTINCT 上线城市 FROM product_flow_v
         WHERE 上线城市 IS NOT NULL AND 上线城市 != ''
           AND 上线城市 NOT LIKE '%***%'
         ORDER BY 上线城市
    """, conn)
    conn.close()
    return df['上线城市'].tolist()


try:
    min_month, max_month = get_month_range()
    cities = get_cities()
except Exception as e:
    st.error(f"❌ 读 DB 失败:{e}")
    st.stop()


def month_range(start, end):
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    return [str(s + i) for i in range((e - s).n + 1)]


all_months = month_range(min_month, max_month)

c1, c2, c3, c4 = st.columns([1.5, 1.5, 2, 2])

with c1:
    period_start = st.selectbox(
        "📅 评估期 起",
        all_months,
        index=max(0, len(all_months) - 4),  # 默认倒数第 4 个月
    )

with c2:
    end_options = [m for m in all_months if m >= period_start]
    default_idx = min(3, len(end_options) - 1)
    period_end = st.selectbox(
        "📅 评估期 止",
        end_options,
        index=default_idx,
    )

with c3:
    city = st.selectbox(
        "🏙️ 地域范围",
        ['(全省)'] + cities,
        index=0,
    )
    city_filter = None if city == '(全省)' else city

with c4:
    n = (pd.Period(period_end, freq='M') - pd.Period(period_start, freq='M')).n + 1
    yoy_s = pd.Period(period_start, freq='M') - 12
    yoy_e = pd.Period(period_end, freq='M') - 12
    mom_s = pd.Period(period_start, freq='M') - n
    mom_e = pd.Period(period_start, freq='M') - 1
    st.caption(
        f"📐 **同期** {yoy_s} ~ {yoy_e}({n} 个月,去年同期)\n\n"
        f"📐 **环期** {mom_s} ~ {mom_e}({n} 个月,紧邻评估期前)"
    )


run_btn = st.button("🚀 生成报告", type='primary', use_container_width=True)


# ────────────────────────────────────────────────
# 执行
# ────────────────────────────────────────────────

if run_btn:
    label = f"{city_filter or '全省'}_{period_start}_to_{period_end}"
    overview_path = REPORTS_DIR / f'产品维度经营报告_概览_{label}.html'
    v4_path = REPORTS_DIR / f'产品维度经营报告_v4_{label}.html'

    progress = st.empty()
    started = time.time()

    # Step 1:取数
    progress.info("📦 步骤 1/3:从 DB 查全量数据(预计 30-60 秒)...")
    try:
        t = time.time()
        data = gather_data(period_start, period_end, city_filter)
        st.caption(f"  ✅ 数据查完 · 耗时 {time.time() - t:.1f}s")
    except Exception as e:
        progress.error(f"❌ 数据查询失败:{e}")
        st.stop()

    # 数据合理性检查
    n_subs = len(data.get('全量子系列', []))
    n_sp = len(data.get('全量_服务商汇总', []))
    if n_subs == 0 or n_sp == 0:
        progress.warning(
            f"⚠️ 评估期内数据稀少(子系列 {n_subs} / 服务商 {n_sp}),"
            "可能选错时段。请检查评估期内 install_redpack 是否有数据。"
        )

    # Step 2:渲染 V4
    progress.info("🎨 步骤 2/3:渲染全量版 HTML...")
    try:
        t = time.time()
        v4_html = render_v4_html(data)
        full_v4 = (
            f"<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>产品维度报告·全量 - {label}</title></head><body>\n"
            f"{v4_html}\n</body></html>"
        )
        v4_path.write_text(full_v4, encoding='utf-8')
        st.caption(f"  ✅ 全量版 {v4_path.name}({len(full_v4)/1024/1024:.2f} MB · 耗时 {time.time() - t:.1f}s)")
    except Exception as e:
        progress.error(f"❌ 全量版渲染失败:{e}")
        import traceback
        st.code(traceback.format_exc())
        st.stop()

    # Step 3:渲染概览
    progress.info("🎨 步骤 3/3:渲染概览版 HTML...")
    try:
        t = time.time()
        ov_html = render_overview_html(data, v4_path.name)
        full_ov = (
            f"<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>产品维度报告·概览 - {label}</title></head><body>\n"
            f"{ov_html}\n</body></html>"
        )
        overview_path.write_text(full_ov, encoding='utf-8')
        st.caption(f"  ✅ 概览版 {overview_path.name}({len(full_ov)/1024:.1f} KB · 耗时 {time.time() - t:.1f}s)")
    except Exception as e:
        progress.error(f"❌ 概览版渲染失败:{e}")
        import traceback
        st.code(traceback.format_exc())
        st.stop()

    progress.success(f"✅ 全部完成 · 总耗时 {time.time() - started:.1f} 秒")

    st.divider()
    st.markdown("### 📥 下载报告")

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.markdown("#### 📋 概览版(简短汇报)")
        st.caption(f"~ {overview_path.stat().st_size / 1024:.0f} KB · 5 分钟扫一遍")
        st.download_button(
            "⬇️ 下载概览 HTML",
            data=overview_path.read_bytes(),
            file_name=overview_path.name,
            mime='text/html',
            use_container_width=True,
        )

    with dl_col2:
        st.markdown("#### 📊 全量版(详细分析)")
        st.caption(f"~ {v4_path.stat().st_size / 1024 / 1024:.1f} MB · JS 交互筛选")
        st.download_button(
            "⬇️ 下载全量 HTML",
            data=v4_path.read_bytes(),
            file_name=v4_path.name,
            mime='text/html',
            use_container_width=True,
        )

    st.divider()
    st.markdown("### 👁️ 概览版预览(可滚动)")
    st.components.v1.html(
        overview_path.read_text(encoding='utf-8'),
        height=900, scrolling=True,
    )


# ────────────────────────────────────────────────
# 历史报告
# ────────────────────────────────────────────────
st.divider()
with st.expander("📚 历史已生成报告"):
    files = sorted(REPORTS_DIR.glob('产品维度经营报告_*.html'), key=lambda p: -p.stat().st_mtime)
    if not files:
        st.info("暂无历史报告")
    else:
        for f in files[:30]:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            size_kb = f.stat().st_size / 1024
            cols = st.columns([5, 2, 2])
            cols[0].text(f.name)
            cols[1].caption(f"{size_kb:.0f} KB · {mtime}")
            cols[2].download_button(
                "下载",
                data=f.read_bytes(),
                file_name=f.name,
                mime='text/html',
                key=f"dl_{f.name}",
                use_container_width=True,
            )


# ════════════════════════════════════════════════════════
# 说明
# ════════════════════════════════════════════════════════
st.divider()
with st.expander("💡 关于本报告"):
    st.markdown("""
**数据口径**

| 维度 | 数据源 / 字段 |
|---|---|
| 地市 / 区县 / 代理商 / 价格 | `product_flow_v.最新分销价`(🌐 全量感知)|
| 服务商 / 渠道健康度 | `install_redpack_v.产品现有分销价`(🎯 红包扫码)|

**对照期**

- 同期 = 评估期 - 12 个月(去年同期)
- 环期 = 紧邻评估期前 N 个月(N = 评估期月数)

**问题判定阈值(概览版)**

- 🔴 严重:同比 ≤ -20% / 渠道净流出 ≤ -20 / 整体腰斩
- 🟡 告警:-20% < 同比 ≤ -5% / 均价跌 ≤ -10% / 1-2 子系列下滑
- 🟢 健康:无触发

**服务商标识**

- 🎭 马甲(`vest_account`)— 已**排除**(不进任何服务商表)
- 🚫 已关闭 / 明确无意向(`closed_provider`)— **保留并标记**

**本报告分析能力**

| 因素 | 反应 |
|---|---|
| 自身涨/降价 | ⚠️ 单台均价同比 |
| 渠道健康度 | ✅ 同环比对称 |
| 市场需求 / 竞品定价 / 产品质量 | ❌(无外部数据)|
| 跑动 / 红包 | ❌(本版未分析)|
""")
