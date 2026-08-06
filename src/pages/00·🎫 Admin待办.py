"""Admin 待办工作流(仅 admin 可见)

列表页 + 详情页双视图,URL 参数 ?issue=<id> 切换。

生命周期:待调研 → 调研中 → 思路就绪 → 任务定稿 → 执行中 → 待验证 → 已关闭
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from _auth import require_auth, current_user, is_admin  # noqa: E402
from _admin_issue_loader import (  # noqa: E402
    list_issues, get_issue, create_issue, update_issue, delete_issue,
    list_tasks, create_task, update_task, delete_task,
    list_logs, append_log,
    STATUSES, PRIORITIES, LABELS, TASK_STATUSES, TASK_OWNERS,
)


require_auth()
if not is_admin():
    st.error('🔒 仅 admin 可访问此页面')
    st.stop()
user = current_user()


# ─── URL 参数:?issue=<id> 决定列表 or 详情 ───
qp = st.query_params
issue_id = qp.get('issue')
try:
    issue_id = int(issue_id) if issue_id else None
except (TypeError, ValueError):
    issue_id = None


# ══════════════════════════════════════════════════════════
# 详情页
# ══════════════════════════════════════════════════════════

def render_detail(issue_id: int):
    issue = get_issue(issue_id)
    if not issue:
        st.error(f"Issue #{issue_id} 不存在")
        if st.button('← 返回列表'):
            st.query_params.clear()
            st.rerun()
        return

    # ─── 顶部:返回 + 标题 + 状态 + 操作 ───
    c1, c2 = st.columns([1, 8])
    with c1:
        if st.button('← 返回列表'):
            st.query_params.clear()
            st.rerun()
    with c2:
        prio_emoji = {'P0': '🔴', 'P1': '🟡', 'P2': '🟢'}.get(issue['优先级'], '⚪')
        st.markdown(f"### {prio_emoji} #{issue_id}  {issue['标题']}")
        meta_cols = st.columns([1, 1, 1, 1, 2])
        meta_cols[0].caption(f"**状态**:{issue['状态']}")
        meta_cols[1].caption(f"**优先级**:{issue['优先级']}")
        meta_cols[2].caption(f"**标签**:{issue['标签'] or '—'}")
        meta_cols[3].caption(f"**创建**:{(issue['创建时间'] or '')[:10]}")
        meta_cols[4].caption(f"**关联**:{issue['关联上下文'] or '—'}")

    st.divider()

    # ─── 状态/优先级/标签快速编辑(顶部一行)───
    with st.expander('⚙️ 编辑元信息', expanded=False):
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            new_status = st.selectbox('状态', STATUSES,
                                       index=STATUSES.index(issue['状态']) if issue['状态'] in STATUSES else 0,
                                       key='e_status')
        with ec2:
            new_priority = st.selectbox('优先级', PRIORITIES,
                                         index=PRIORITIES.index(issue['优先级']) if issue['优先级'] in PRIORITIES else 1,
                                         key='e_priority')
        with ec3:
            current_labels = (issue['标签'] or '').split(',') if issue['标签'] else []
            current_labels = [l.strip() for l in current_labels if l.strip()]
            new_labels = st.multiselect('标签', LABELS,
                                          default=[l for l in current_labels if l in LABELS],
                                          key='e_labels')
        new_context = st.text_input('关联上下文', value=issue['关联上下文'] or '', key='e_ctx',
                                      help='如 "page 32 段 20" / "周报 SI 口径"')
        if st.button('💾 保存元信息', type='primary'):
            update_issue(issue_id,
                          状态=new_status,
                          优先级=new_priority,
                          标签=','.join(new_labels),
                          关联上下文=new_context,
                          _operator=user)
            st.success('已保存')
            st.rerun()

        st.divider()
        st.markdown('**🚨 危险操作**')
        if st.checkbox('我确认要删除这个 issue 及其所有任务/日志', key='confirm_del'):
            if st.button('🗑️ 永久删除', type='secondary'):
                delete_issue(issue_id)
                st.query_params.clear()
                st.rerun()

    # ─── ① 背景描述 ───
    st.markdown('#### ① 背景描述')
    bg = st.text_area('Admin 输入', value=issue['背景描述'] or '', height=120,
                       key='bg', label_visibility='collapsed',
                       placeholder='描述要解决的问题...')
    if st.button('💾 保存背景', key='save_bg'):
        update_issue(issue_id, 背景描述=bg, _operator=user)
        st.success('已保存'); st.rerun()

    st.divider()

    # ─── ② 数据调研 ───
    st.markdown('#### ② 数据调研')
    st.caption('🤖 AI 自动调研按钮 Phase 2 接入,先手动填')
    rs = st.text_area('数据调研结果', value=issue['数据调研'] or '', height=180,
                       key='rs', label_visibility='collapsed',
                       placeholder='数据画像 / SQL / 异常发现 / 初步结论...')
    if st.button('💾 保存调研', key='save_rs'):
        update_issue(issue_id, 数据调研=rs, _operator=user)
        st.success('已保存'); st.rerun()

    st.divider()

    # ─── ③ 思路 ───
    st.markdown('#### ③ 思路')
    st.caption('🤖 AI 提案按钮 Phase 2 接入')
    th = st.text_area('解决思路 / 候选方案', value=issue['思路'] or '', height=140,
                       key='th', label_visibility='collapsed',
                       placeholder='方案 A: ... / 方案 B: ... / 选定方案 X 因为...')
    if st.button('💾 保存思路', key='save_th'):
        update_issue(issue_id, 思路=th, _operator=user)
        st.success('已保存'); st.rerun()

    st.divider()

    # ─── ④ 任务清单 ───
    st.markdown('#### ④ 任务清单')
    tasks = list_tasks(issue_id)
    if tasks.empty:
        st.info('暂无任务,在下方添加')
    else:
        for _, t in tasks.iterrows():
            tcol = st.columns([0.4, 0.6, 5, 1, 1, 0.5])
            with tcol[0]:
                done = t['状态'] == '完成'
                if st.checkbox('', value=done, key=f"t_done_{t['id']}", label_visibility='collapsed'):
                    if not done:
                        update_task(int(t['id']), 状态='完成')
                        st.rerun()
                else:
                    if done:
                        update_task(int(t['id']), 状态='待办')
                        st.rerun()
            with tcol[1]:
                st.caption(f"T{t['序号']}")
            with tcol[2]:
                txt = f"~~{t['描述']}~~" if done else t['描述']
                st.markdown(txt)
                if t['输出备注']:
                    st.caption(f"💡 {t['输出备注']}")
            with tcol[3]:
                owner_emoji = {'AI': '🤖', 'Admin': '👤', 'AI+Admin': '🤝'}.get(t['责任方'], '?')
                st.caption(f"{owner_emoji} {t['责任方']}")
            with tcol[4]:
                st.caption(t['状态'])
            with tcol[5]:
                if st.button('🗑️', key=f"t_del_{t['id']}", help='删除任务'):
                    delete_task(int(t['id']))
                    st.rerun()

    with st.expander('➕ 添加新任务', expanded=tasks.empty):
        nc1, nc2 = st.columns([4, 1])
        with nc1:
            new_task_desc = st.text_input('任务描述', key='nt_desc',
                                            placeholder='如:修改 _weekly_report.py 加新字段')
        with nc2:
            new_task_owner = st.selectbox('责任方', TASK_OWNERS, index=0, key='nt_owner')
        if st.button('➕ 添加', type='primary', key='nt_add'):
            if new_task_desc.strip():
                create_task(issue_id, new_task_desc.strip(), 责任方=new_task_owner)
                st.rerun()

    st.divider()

    # ─── ⑤ 执行记录(日志)───
    st.markdown('#### ⑤ 执行记录 / 日志')
    logs = list_logs(issue_id, limit=20)
    if logs.empty:
        st.caption('暂无日志')
    else:
        for _, l in logs.iterrows():
            type_emoji = {
                '创建': '🆕', '调研': '🔍', '思路': '💡', '任务': '📋',
                '执行': '⚡', '验证': '✅', '状态变更': '🔄', '评论': '💬',
                '背景描述': '📝', '数据调研': '🔍', '思路': '💡',
            }.get(l['类型'], '·')
            st.caption(f"{type_emoji} **{l['时间'][:16]}** [{l['操作人']}] {l['类型']}:{l['内容'][:120]}")

    st.divider()

    # ─── 底部:评论 + 关闭 ───
    cbc1, cbc2 = st.columns([4, 1])
    with cbc1:
        comment = st.text_input('💬 添加评论', key='comment', label_visibility='collapsed',
                                  placeholder='添加备注/决策记录...')
    with cbc2:
        if st.button('提交', key='add_cmt'):
            if comment.strip():
                append_log(issue_id, '评论', comment.strip(), user)
                st.rerun()

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        if issue['状态'] not in ('已关闭', '已归档'):
            if st.button('✅ 关闭 issue', type='primary', use_container_width=True):
                update_issue(issue_id, 状态='已关闭', _operator=user)
                st.rerun()
    with fc2:
        if issue['状态'] == '已关闭':
            if st.button('📦 归档', use_container_width=True):
                update_issue(issue_id, 状态='已归档', _operator=user)
                st.rerun()
    with fc3:
        if issue['状态'] in ('已关闭', '已归档'):
            if st.button('🔄 重开', use_container_width=True):
                update_issue(issue_id, 状态='执行中', _operator=user)
                st.rerun()


# ══════════════════════════════════════════════════════════
# 列表页
# ══════════════════════════════════════════════════════════

def render_list():
    st.markdown('### 🎫 Admin 待办工作流')
    st.caption('问题驱动的协作工作台 · Admin 提问 / AI 调研 / Admin 定思路 / AI 拆任务 / Admin 审批')

    # ─── 顶部筛选 + 新建 ───
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        filter_status = st.selectbox('状态', ['全部'] + STATUSES, key='f_status')
    with fc2:
        filter_label = st.selectbox('标签', ['全部'] + LABELS, key='f_label')
    with fc3:
        st.write('')
        if st.button('➕ 新建 issue', type='primary', use_container_width=True):
            st.session_state['_show_new'] = True

    # ─── 新建表单(展开)───
    if st.session_state.get('_show_new'):
        with st.container(border=True):
            st.markdown('**🆕 新建 issue**')
            nc1, nc2 = st.columns([3, 1])
            with nc1:
                new_title = st.text_input('标题 *', key='n_title',
                                            placeholder='如:周报段 20 竞品 top 转化口径不清')
            with nc2:
                new_prio = st.selectbox('优先级', PRIORITIES, index=1, key='n_prio')
            new_labels = st.multiselect('标签', LABELS, key='n_labels')
            new_ctx = st.text_input('关联上下文(可选)', key='n_ctx',
                                      placeholder='page 32 段 20')
            new_bg = st.text_area('背景描述', height=100, key='n_bg',
                                    placeholder='问题描述...')
            bcl, bcc = st.columns([1, 1])
            with bcl:
                if st.button('✅ 创建', type='primary', use_container_width=True):
                    if not new_title.strip():
                        st.error('标题必填')
                    else:
                        new_id = create_issue(
                            标题=new_title.strip(),
                            优先级=new_prio,
                            标签=','.join(new_labels),
                            背景描述=new_bg,
                            关联上下文=new_ctx,
                            创建人=user,
                        )
                        st.session_state['_show_new'] = False
                        st.query_params['issue'] = str(new_id)
                        st.rerun()
            with bcc:
                if st.button('取消', use_container_width=True):
                    st.session_state['_show_new'] = False
                    st.rerun()

    st.divider()

    # ─── 列表 ───
    df = list_issues(status=filter_status, label=filter_label)
    if df.empty:
        st.info('没有 issue,点上方"新建 issue"开始')
        return

    # 进度条 + 状态徽章
    df_show = df.copy()
    df_show['进度'] = df_show.apply(
        lambda r: f"{int(r['已完成'])}/{int(r['总任务'])} ({(r['已完成']/r['总任务']*100 if r['总任务'] else 0):.0f}%)"
        if r['总任务'] else '—', axis=1)
    df_show['优先级'] = df_show['优先级'].map({'P0': '🔴 P0', 'P1': '🟡 P1', 'P2': '🟢 P2'})

    # 用 dataframe + 链接点击(每行加按钮)
    for _, r in df_show.iterrows():
        cols = st.columns([0.5, 0.6, 5, 1.5, 1.5, 1.5, 1])
        cols[0].caption(f"#{int(r['id'])}")
        cols[1].markdown(r['优先级'])
        cols[2].markdown(f"**{r['标题']}**")
        cols[2].caption(f"{r['标签'] or ''} · {r['关联上下文'] or ''}")
        cols[3].caption(f"状态:{r['状态']}")
        cols[4].caption(f"进度:{r['进度']}")
        cols[5].caption(f"{(r['创建时间'] or '')[:10]}")
        if cols[6].button('打开', key=f"open_{int(r['id'])}"):
            st.query_params['issue'] = str(int(r['id']))
            st.rerun()


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

if issue_id:
    render_detail(issue_id)
else:
    render_list()
