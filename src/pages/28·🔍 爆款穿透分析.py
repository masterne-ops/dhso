#!/usr/bin/env python3
"""🔍 爆款型号穿透分析

输入一段关键字(优先匹配内部型号 → fallback 外部型号),
对命中的 SKU 群做 9 章穿透分析,出一份 HTML。
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
from _hotsku_report_runner import run as gather_data  # noqa: E402
from _hotsku_report_html import render_report as render_html  # noqa: E402

st.set_page_config(page_title='爆款穿透分析', layout='wide')
require_auth()

st.markdown('### 🔍 爆款型号穿透分析')
st.caption(
    '输入关键字 → 优先匹配「内部型号」,匹配不到再匹配「外部型号」。'
    '对命中的 SKU 群做 9 章穿透:**SKU 列表 / 月度走势 / 价格 / 11 地市 / Top 区县 / 代理商 / 服务商生态 / 跨地域 / 自动诊断**。'
)


# 路径
PROJECT_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / 'reports' / 'hotsku'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────
# 参数选择
# ────────────────────────────────────────────────


@st.cache_data(ttl=600)
def get_month_range():
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        'SELECT MIN(上线年月) AS mn, MAX(上线年月) AS mx FROM install_redpack_v',
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
    st.error(f'❌ 读 DB 失败:{e}')
    st.stop()


def month_range(start, end):
    s = pd.Period(start, freq='M')
    e = pd.Period(end, freq='M')
    return [str(s + i) for i in range((e - s).n + 1)]


all_months = month_range(min_month, max_month)


c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 2])

with c1:
    keyword = st.text_input(
        '🔎 关键字(匹配型号)',
        value='T8',
        placeholder='如 T8 / HDW2449 / P5FC ...',
        help='对「内部型号」做模糊匹配,匹配不到自动 fallback 到「外部型号」。'
             '产品名称需在「高级选项」里显式选',
    )

with c2:
    period_start = st.selectbox(
        '📅 评估期 起',
        all_months,
        index=max(0, len(all_months) - 4),
    )

with c3:
    end_options = [m for m in all_months if m >= period_start]
    default_idx = min(3, len(end_options) - 1)
    period_end = st.selectbox(
        '📅 评估期 止',
        end_options,
        index=default_idx,
    )

with c4:
    city = st.selectbox(
        '🏙️ 地域范围',
        ['(全省)'] + cities,
        index=0,
    )
    city_filter = None if city == '(全省)' else city


with st.expander('⚙️ 高级选项', expanded=False):
    match_field_pick = st.radio(
        '匹配字段',
        options=['自动(内部 → 外部)', '内部型号', '外部型号', '产品名称'],
        index=0,
        horizontal=True,
        help='默认自动 fallback。产品名称会匹到标签号噪音,只在确实想搜中文描述时用',
    )
    match_field = None if match_field_pick.startswith('自动') else match_field_pick

    # 同/环期预览
    n = (pd.Period(period_end, freq='M') - pd.Period(period_start, freq='M')).n + 1
    yoy_s = pd.Period(period_start, freq='M') - 12
    yoy_e = pd.Period(period_end, freq='M') - 12
    mom_s = pd.Period(period_start, freq='M') - n
    mom_e = pd.Period(period_start, freq='M') - 1
    st.caption(
        f'📐 **同期** {yoy_s} ~ {yoy_e}({n} 个月,去年同期)  ·  '
        f'**环期** {mom_s} ~ {mom_e}({n} 个月,紧邻前)'
    )


run_btn = st.button('🚀 生成穿透报告', type='primary', use_container_width=True,
                    disabled=not keyword)


# ────────────────────────────────────────────────
# 执行
# ────────────────────────────────────────────────


if run_btn:
    safe_kw = keyword.replace('/', '_').replace(' ', '_').strip()
    label = f'{safe_kw}_{period_start}_to_{period_end}_{city_filter or "全省"}'
    out_path = REPORTS_DIR / f'爆款穿透_{label}.html'

    progress = st.empty()
    started = time.time()

    progress.info('📦 步骤 1/2:从 DB 查穿透数据(10-30 秒)...')
    try:
        t = time.time()
        data = gather_data(keyword, period_start, period_end,
                           city=city_filter, match_field=match_field)
        st.caption(f'  ✅ 数据查完 · 耗时 {time.time() - t:.1f}s')
    except Exception as e:
        progress.error(f'❌ 数据查询失败:{e}')
        import traceback
        st.code(traceback.format_exc())
        st.stop()

    # 命中检查
    if 'error' in data:
        progress.error(f'❌ {data["error"]}')
        st.stop()
    if not data.get('sku_list'):
        progress.warning(
            f'⚠️ 关键字「{keyword}」在评估期内无匹配数据。'
            '请扩大评估期或更换关键字。'
        )
        st.stop()

    # 命中提示
    meta = data['_meta']
    n_skus = len(data['sku_list'])
    total = data['sku_total']
    progress.success(
        f'✅ 命中 **{n_skus}** 个 SKU(匹配字段:{meta["match_field"]},'
        f'全周期 {meta["hit_rows_all_time"]:,} 行)· '
        f'评估期 {total["台数"]:,} 台 / ¥{total["货值"]/10000:.1f}万'
    )

    # Step 2:渲染 HTML
    try:
        t = time.time()
        body = render_html(data)
        full_html = (
            f"<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>爆款穿透 · {label}</title></head><body>\n"
            f"{body}\n</body></html>"
        )
        out_path.write_text(full_html, encoding='utf-8')
        st.caption(
            f'  ✅ HTML 渲染完 {out_path.name} '
            f'({len(full_html)/1024:.0f} KB · 耗时 {time.time() - t:.1f}s)'
        )
    except Exception as e:
        progress.error(f'❌ HTML 渲染失败:{e}')
        import traceback
        st.code(traceback.format_exc())
        st.stop()

    st.success(f'🎉 全部完成 · 总耗时 {time.time() - started:.1f} 秒')

    st.divider()
    st.markdown('### 📥 下载 / 预览')

    dl_col1, dl_col2 = st.columns([1, 3])
    with dl_col1:
        st.download_button(
            '⬇️ 下载 HTML',
            data=out_path.read_bytes(),
            file_name=out_path.name,
            mime='text/html',
            use_container_width=True,
            type='primary',
        )
    with dl_col2:
        st.caption(
            f'文件大小:{out_path.stat().st_size/1024:.0f} KB  ·  '
            f'路径:{out_path.relative_to(PROJECT_ROOT)}'
        )

    st.divider()
    st.markdown('### 👁️ 报告预览')
    st.components.v1.html(
        out_path.read_text(encoding='utf-8'),
        height=900, scrolling=True,
    )


# ────────────────────────────────────────────────
# 历史报告
# ────────────────────────────────────────────────

st.divider()
with st.expander('📚 历史已生成报告'):
    files = sorted(REPORTS_DIR.glob('爆款穿透_*.html'),
                   key=lambda p: -p.stat().st_mtime)
    if not files:
        st.info('暂无历史报告')
    else:
        for f in files[:30]:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            size_kb = f.stat().st_size / 1024
            cols = st.columns([6, 2, 2])
            cols[0].text(f.name)
            cols[1].caption(f'{size_kb:.0f} KB · {mtime}')
            cols[2].download_button(
                '下载',
                data=f.read_bytes(),
                file_name=f.name,
                mime='text/html',
                key=f'dl_{f.name}',
                use_container_width=True,
            )


# ════════════════════════════════════════════════════════
# 说明
# ════════════════════════════════════════════════════════

st.divider()
with st.expander('💡 关于本报告'):
    st.markdown("""
**关键字匹配规则**

1. **优先** `内部型号 LIKE '%kw%'`,命中即停
2. **没命中** → 自动 fallback 到 `外部型号`
3. **产品名称**(噪音大,会匹到标签号字符)— 仅在「高级选项」显式指定时启用

**双口径**

| 维度 | 数据源 |
|---|---|
| SKU 列表 / 走势 / 地市 / 区县 / 代理商 / 价格 | `product_flow_v.最新分销价`(🌐 全量感知)|
| 服务商生态 / 渠道健康度 | `install_redpack_v.产品现有分销价`(🎯 红包扫码,已剔除马甲)|

**对照期**

- 同期 = 评估期 - 12 个月(去年同期)
- 环期 = 紧邻评估期前 N 个月(N = 评估期月数)

**自动诊断评级**

- 🔥 爆款上扬:同比 ≥ +50% 且最近 2 月环比 ≥ +30%
- 📈 稳健增长:同比 ≥ +10% 且无明显降价
- 🟡 持平:-10% ≤ 同比 ≤ +10%
- 🟢 新品爬坡:同期(去年同月)无销量
- ⚠️ 衰退预警:同比 ≤ -20%

**报告分析能力**

| 因素 | 反应 |
|---|---|
| SKU 销量趋势 | ✅ 月度走势 + 同环比 |
| 价格变化 | ✅ 价格分布 + 均价同比 |
| 渠道扩散 | ✅ 服务商集合差(新增/流失)|
| 区域结构 | ✅ 11 地市 + Top 30 区县 + 跨地域 |
| 跨渠道穿透 | ✅ 签约状态分布 |
| 外部市场容量 / 竞品 | ❌(无数据)|
| 服务商画像 | ❌(本报告仅看采购量,不分析背后)|
""")
