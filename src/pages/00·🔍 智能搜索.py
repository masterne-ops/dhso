#!/usr/bin/env python3
"""🔍 智能搜索 — 用户输入模糊需求 → 推荐最贴近的功能 page"""
import sys
from pathlib import Path
from urllib.parse import quote

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth, current_user, get_current_role, is_admin  # noqa: E402
from _ai_search import search  # noqa: E402
from _ai_log import log_search, list_search_logs  # noqa: E402
from _page_registry import PAGES  # noqa: E402

require_auth()

st.markdown("# 🔍 智能搜索")
st.caption("不知道用哪个功能？输入你的需求，AI 帮你推荐最贴近的页面。")

user = current_user() or '(未登录)'
role = get_current_role() or 'guest'

# 搜索框
query = st.text_input(
    "你想做什么？",
    placeholder="如：「想看代理商业绩排名」「找今天该跑哪些客户」「上个月哪些区县完成不好」",
    key='search_query',
)

if query:
    with st.spinner('AI 正在匹配最贴近的功能……'):
        results = search(query, role=role)

    # 写日志（不显示给用户）
    try:
        log_id = log_search(
            user=user, role=role, query=query,
            results=[{'url': r['url'], 'title': r['title'], 'reason': r.get('_reason', '')}
                     for r in results],
        )
        st.session_state['_search_log_id'] = log_id
    except Exception:
        pass

    st.markdown("---")
    if not results:
        st.info("🤷 没找到匹配的功能。换个说法试试，比如：「我要查 RFM」「业务员跑动情况」")
    else:
        st.markdown(f"**为你推荐**：")
        for i, p in enumerate(results, 1):
            with st.container(border=True):
                cols = st.columns([4, 1])
                with cols[0]:
                    st.markdown(f"### {p['icon']} {p['title']}")
                    st.caption(f"📂 {p['group']}")
                    st.write(p['description'])
                    if p.get('_reason'):
                        st.caption(f"💡 {p['_reason']}")
                with cols[1]:
                    # 用 page_link 是最优雅的（streamlit 1.30+ 原生支持）
                    try:
                        st.page_link(p['file'], label=f"打开 →", icon="➡️")
                    except Exception:
                        st.write(f"📄 `{p['file']}`")

# 历史搜索（admin 可见）
if is_admin():
    st.markdown("---")
    with st.expander("📜 搜索历史（管理员可见）"):
        logs = list_search_logs(limit=100)
        if logs.empty:
            st.info("暂无搜索记录")
        else:
            st.dataframe(
                logs[['时间', '用户', '用户角色', '查询', '推荐结果', '点击的url']],
                use_container_width=True, hide_index=True,
                height=400,
            )
            csv = logs.to_csv(index=False).encode('utf-8-sig')
            st.download_button('⬇️ 导出 CSV', csv,
                               file_name='ai_search_log.csv', mime='text/csv')

# 底部小提示
with st.expander("💡 想直接看全部功能？"):
    st.markdown("左侧侧边栏按分组列出了所有功能，你可以直接点开。")
    from _page_registry import grouped
    for grp, pgs in grouped().items():
        if grp == '🔍 智能搜索':
            continue
        st.markdown(f"**{grp}**")
        for p in pgs:
            if p.get('role') == 'admin' and not is_admin():
                continue
            st.markdown(f"- {p['icon']} {p['title']} — {p['description']}")
