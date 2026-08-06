#!/usr/bin/env python3
"""产品组一周工作：固定周节奏 + 可分派、可回填的本周特定工作。"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import current_user, get_current_role, is_admin, require_auth  # noqa: E402


DB_PATH = Path(__file__).parent.parent.parent / "db" / "product_flow.db"
TEAM_MEMBERS = ["金磊", "林初鼎", "金科佚", "耿培存"]
STATUSES = ["未开始", "进行中", "已完成", "逾期", "取消"]
PRODUCTS = ["无线", "夜视王", "场景化", "新品/其他", "综合"]

FIXED_WORK = [
    ("周一", "09:00前", "林初鼎", "—", "更新全省产品 SI、SO、激励进度、城市及代理商贡献和重点服务商转化数据", "本周产品经营数据表"),
    ("周一", "10:00前", "林初鼎", "金科佚、耿培存", "标记目标缺口、异常城市、异常代理商和上周逾期事项", "红黄灯预警清单"),
    ("周一", "10:00—11:00", "金磊", "产品组", "确定本周全省前三项产品任务、重点城市和资源安排", "本周作战重点"),
    ("周一", "12:00前", "金科佚、耿培存", "地市主管、分销经理", "按分片城市下发本周产品任务并落实到业务员", "地市任务清单"),
    ("周一", "17:00前", "地市主管", "分销经理", "把任务落实到代理商、服务商和销售机会", "业务员行动清单"),
    ("周二", "全天", "分销经理", "代理商业务员", "核对代理商激励差距，明确补差客户、产品、金额和下单时间", "代理商补差计划"),
    ("周二", "全天", "分销经理", "代理商业务员", "挖掘无线、夜视王、场景化和新品的真实销售机会", "新增有效机会"),
    ("周二", "17:00前", "金科佚、耿培存", "分销经理", "审核机会真实性，删除无客户、无场景、无节点的事项", "有效机会清单"),
    ("周三", "上午", "金科佚", "相关业务员、代理商业务员", "推动无线、新品重点服务商 GTM：首单、铺货/样机和回访", "客户转化节点"),
    ("周三", "上午", "耿培存", "相关业务员、代理商业务员", "推动夜视王、场景化重点服务商 GTM：方案、培训、首单和案例复制", "客户转化节点"),
    ("周三", "下午", "金科佚、耿培存", "地市业务员、代理商人员", "按真实销售需求开展产品培训，并形成后续客户动作", "测试结果和机会清单"),
    ("周三", "17:00前", "分销经理", "代理商业务员", "回访已首单、铺货或拿样客户，确认销售、库存、问题和复购时间", "回访及复购计划"),
    ("周四", "上午", "金科佚、耿培存", "地市主管、分销经理", "检查分片城市任务，形成未完成、逾期及补救安排", "逾期及补救清单"),
    ("周四", "下午", "林初鼎", "金科佚、耿培存", "汇总价格、质量、交付、售后和政策问题，落实责任人与节点", "产品问题闭环清单"),
    ("周四", "17:00前", "金科佚、耿培存", "地市业务员", "沉淀成功案例或失败复盘，明确复制城市和客户", "可复制案例"),
    ("周五", "10:00前", "林初鼎", "—", "更新本周产品结果、任务完成率和数据水位", "周度经营结果表"),
    ("周五", "上午", "金科佚、耿培存", "地市主管、分销经理", "完成分片城市复盘，提出下周调整建议", "城市复盘"),
    ("周五", "14:00—15:00", "金磊", "产品组", "内部复盘：只讨论异常、机会、逾期和需决策事项", "下周管理重点"),
    ("周五", "15:00—16:00", "金磊", "重点地市主管、相关业务员", "全省产品经营周会，确认下周责任人、目标和验收节点", "会议结论"),
    ("周五", "17:00前", "林初鼎", "全体责任人", "发布会议结论，未完成事项自动带入下周", "下周任务初稿"),
]


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pm_fixed_work_template (
                template_id TEXT PRIMARY KEY,
                weekday TEXT NOT NULL,
                time_slot TEXT,
                owner TEXT NOT NULL,
                collaborator TEXT,
                work_item TEXT NOT NULL,
                acceptance_output TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pm_fixed_work_week (
                week_start TEXT NOT NULL,
                template_id TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT '未完成',
                hidden INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT,
                PRIMARY KEY (week_start, template_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pm_weekly_work (
                task_id TEXT PRIMARY KEY,
                week_start TEXT NOT NULL,
                owner TEXT NOT NULL,
                collaborator TEXT,
                city TEXT,
                dealer_provider TEXT,
                product TEXT,
                work_item TEXT NOT NULL,
                target_result TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT '未开始',
                actual_result TEXT,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT,
                updated_by TEXT,
                updated_at TEXT
            )
        """)
        task_cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_weekly_work)").fetchall()}
        if "deleted" not in task_cols:
            conn.execute("ALTER TABLE pm_weekly_work ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pm_work_log (
                log_id TEXT PRIMARY KEY,
                week_start TEXT NOT NULL,
                task_key TEXT NOT NULL,
                detail TEXT NOT NULL,
                author TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_weekly_work_week ON pm_weekly_work(week_start)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_weekly_work_owner ON pm_weekly_work(owner)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_work_log_task ON pm_work_log(week_start,task_key)")
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, row in enumerate(FIXED_WORK, start=1):
            conn.execute("""
                INSERT OR IGNORE INTO pm_fixed_work_template(
                    template_id, weekday, time_slot, owner, collaborator,
                    work_item, acceptance_output, sort_order, enabled,
                    updated_by, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
            """, (f"fixed_{i:02d}", *row, i, "system", now))


def _display_name(username: str) -> str:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(NULLIF(full_name,''), username) FROM app_user WHERE username=?",
                (username,),
            ).fetchone()
        return str(row[0]) if row else username
    except Exception:
        return username


def _people():
    names = list(TEAM_MEMBERS)
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT COALESCE(NULLIF(full_name,''), username)
                  FROM app_user
                 WHERE enabled=1
                 ORDER BY full_name, username
            """).fetchall()
        names.extend(str(r[0]) for r in rows if r[0])
    except Exception:
        pass
    return list(dict.fromkeys(names))


def _load_tasks(week_start: dt.date) -> pd.DataFrame:
    with _conn() as conn:
        return pd.read_sql("""
            SELECT task_id, owner AS 负责人, collaborator AS 协同人, city AS 城市,
                   dealer_provider AS 代理商或服务商, product AS 产品,
                   work_item AS 本周特定工作, target_result AS 目标结果,
                   due_date AS 完成时间, status AS 状态, actual_result AS 实际结果,
                   COALESCE(deleted,0) AS 删除,
                   created_by AS 创建人, updated_at AS 更新时间
              FROM pm_weekly_work
             WHERE week_start=?
             ORDER BY CASE status WHEN '逾期' THEN 0 WHEN '进行中' THEN 1
                                  WHEN '未开始' THEN 2 WHEN '已完成' THEN 3 ELSE 4 END,
                      due_date, created_at
        """, conn, params=(str(week_start),))


def _load_fixed_work(week_start: dt.date) -> pd.DataFrame:
    with _conn() as conn:
        return pd.read_sql("""
            SELECT t.template_id, t.weekday AS 星期, t.time_slot AS 时间,
                   t.owner AS 负责人, t.collaborator AS 执行协同,
                   t.work_item AS 工作内容, t.acceptance_output AS 验收输出,
                   COALESCE(w.completed,0) AS 完成,
                   COALESCE(w.status,'未完成') AS 状态,
                   COALESCE(w.hidden,0) AS 本周删除
              FROM pm_fixed_work_template t
              LEFT JOIN pm_fixed_work_week w
                ON w.template_id=t.template_id AND w.week_start=?
             WHERE t.enabled=1
             ORDER BY t.sort_order, t.template_id
        """, conn, params=(str(week_start),))


def _save_fixed_work(week_start: dt.date, edited: pd.DataFrame, username: str,
                     can_manage_all: bool):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        for _, row in edited.iterrows():
            template_id = str(row["template_id"])
            if can_manage_all:
                owner = str(row["负责人"]).strip()
                if owner:
                    conn.execute("""
                        UPDATE pm_fixed_work_template
                           SET owner=?, updated_by=?, updated_at=?
                         WHERE template_id=?
                    """, (owner, username, now, template_id))
            completed = int(bool(row["完成"]))
            hidden = int(bool(row["本周删除"])) if can_manage_all else 0
            conn.execute("""
                INSERT INTO pm_fixed_work_week(
                    week_start, template_id, completed, status, hidden,
                    updated_by, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(week_start,template_id) DO UPDATE SET
                    completed=excluded.completed,
                    status=excluded.status,
                    hidden=excluded.hidden,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
            """, (
                str(week_start), template_id, completed,
                "已完成" if completed else "未完成", hidden, username, now,
            ))


def _save_new_task(values: dict, username: str):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        conn.execute("""
            INSERT INTO pm_weekly_work(
                task_id, week_start, owner, collaborator, city, dealer_provider,
                product, work_item, target_result, due_date, status, actual_result,
                created_by, created_at, updated_by, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            uuid.uuid4().hex, values["week_start"], values["owner"],
            values["collaborator"], values["city"], values["dealer_provider"],
            values["product"], values["work_item"], values["target_result"],
            values["due_date"], "未开始", "", username, now, username, now,
        ))


def _update_task(task_id: str, status: str, actual_result: str, username: str):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        conn.execute("""
            UPDATE pm_weekly_work
               SET status=?, actual_result=?, updated_by=?, updated_at=?
             WHERE task_id=?
        """, (status, actual_result, username, now, task_id))


def _load_all_work(week_start: dt.date) -> pd.DataFrame:
    fixed = _load_fixed_work(week_start)
    fixed_rows = pd.DataFrame({
        "task_key": "fixed:" + fixed["template_id"].astype(str),
        "完成": fixed["完成"].astype(bool),
        "类型": "每周模板",
        "星期": fixed["星期"],
        "时间": fixed["时间"],
        "负责人": fixed["负责人"],
        "协同人": fixed["执行协同"],
        "城市": "",
        "代理商或服务商": "",
        "产品": "综合",
        "工作内容": fixed["工作内容"],
        "目标结果": fixed["验收输出"],
        "状态": fixed["状态"],
        "删除": fixed["本周删除"].astype(bool),
    })

    specific = _load_tasks(week_start)
    specific_rows = pd.DataFrame({
        "task_key": "specific:" + specific["task_id"].astype(str),
        "完成": specific["状态"].eq("已完成"),
        "类型": "本周新增",
        "星期": "",
        "时间": specific["完成时间"],
        "负责人": specific["负责人"],
        "协同人": specific["协同人"],
        "城市": specific["城市"],
        "代理商或服务商": specific["代理商或服务商"],
        "产品": specific["产品"],
        "工作内容": specific["本周特定工作"],
        "目标结果": specific["目标结果"],
        "状态": specific["状态"],
        "删除": specific["删除"].astype(bool),
    })
    return pd.concat([fixed_rows, specific_rows], ignore_index=True)


def _save_all_work(week_start: dt.date, edited: pd.DataFrame, username: str,
                   can_manage_all: bool):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        for _, row in edited.iterrows():
            task_key = str(row["task_key"])
            completed = int(bool(row["完成"]))
            deleted = int(bool(row["删除"])) if can_manage_all else None
            owner = str(row["负责人"]).strip()
            if task_key.startswith("fixed:"):
                task_id = task_key.split(":", 1)[1]
                if can_manage_all and owner:
                    conn.execute("""
                        UPDATE pm_fixed_work_template
                           SET owner=?, updated_by=?, updated_at=?
                         WHERE template_id=?
                    """, (owner, username, now, task_id))
                prior = conn.execute("""
                    SELECT hidden FROM pm_fixed_work_week
                     WHERE week_start=? AND template_id=?
                """, (str(week_start), task_id)).fetchone()
                hidden = deleted if deleted is not None else (int(prior[0]) if prior else 0)
                conn.execute("""
                    INSERT INTO pm_fixed_work_week(
                        week_start,template_id,completed,status,hidden,updated_by,updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(week_start,template_id) DO UPDATE SET
                        completed=excluded.completed,status=excluded.status,
                        hidden=excluded.hidden,updated_by=excluded.updated_by,
                        updated_at=excluded.updated_at
                """, (str(week_start), task_id, completed,
                      "已完成" if completed else "未完成", hidden, username, now))
            elif task_key.startswith("specific:"):
                task_id = task_key.split(":", 1)[1]
                current = conn.execute(
                    "SELECT status,deleted FROM pm_weekly_work WHERE task_id=?", (task_id,)
                ).fetchone()
                if not current:
                    continue
                status = "已完成" if completed else (
                    "未开始" if current[0] == "已完成" else current[0]
                )
                deleted_value = deleted if deleted is not None else int(current[1] or 0)
                if can_manage_all and owner:
                    conn.execute("""
                        UPDATE pm_weekly_work
                           SET owner=?,status=?,deleted=?,updated_by=?,updated_at=?
                         WHERE task_id=?
                    """, (owner, status, deleted_value, username, now, task_id))
                else:
                    conn.execute("""
                        UPDATE pm_weekly_work
                           SET status=?,deleted=?,updated_by=?,updated_at=?
                         WHERE task_id=?
                    """, (status, deleted_value, username, now, task_id))


def _load_logs(week_start: dt.date, task_key: str) -> pd.DataFrame:
    with _conn() as conn:
        return pd.read_sql("""
            SELECT created_at AS 时间, author AS 填写人, detail AS 工作日志
              FROM pm_work_log
             WHERE week_start=? AND task_key=?
             ORDER BY created_at DESC
        """, conn, params=(str(week_start), task_key))


def _add_log(week_start: dt.date, task_key: str, detail: str, author: str):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO pm_work_log(log_id,week_start,task_key,detail,author,created_at)
            VALUES (?,?,?,?,?,?)
        """, (
            uuid.uuid4().hex, str(week_start), task_key, detail, author,
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))


require_auth()
_ensure_table()

username = current_user()
display_name = _display_name(username)
role = get_current_role()
can_manage_all = is_admin() or role in {"product_manager", "manager"}

today = dt.date.today()
this_monday = today - dt.timedelta(days=today.weekday())
week_options = [this_monday + dt.timedelta(days=7 * i) for i in range(-8, 5)]

st.markdown("### 🗓️ 产品组一周工作")
st.caption("选择一周查看固定工作和本周特定工作。固定工作明确周节奏；特定工作必须写到具体人员、客户、结果和时间。")

selected_week = st.selectbox(
    "选择周",
    options=week_options,
    index=8,
    format_func=lambda d: f"{d} — {d + dt.timedelta(days=6)}"
                            + ("（本周）" if d == this_monday else ""),
)

tab_work, tab_new = st.tabs(["本周工作", "新增本周特定工作"])

with tab_work:
    all_work = _load_all_work(selected_week)
    keyword = st.text_input(
        "关键字搜索",
        placeholder="输入工作内容、目标结果、负责人、城市、代理商/服务商或产品",
        key=f"work_keyword_{selected_week}",
    ).strip()
    owner_options = ["全部"] + sorted(
        x for x in all_work["负责人"].dropna().astype(str).unique() if x
    )
    f1, f2, f3 = st.columns(3)
    owner_filter = f1.selectbox(
        "筛选负责人", owner_options, key=f"owner_filter_{selected_week}"
    )
    done_filter = f2.selectbox(
        "筛选完成状态", ["全部", "未完成", "已完成"],
        key=f"done_filter_{selected_week}",
    )
    delete_filter = f3.selectbox(
        "筛选删除状态", ["未删除", "已删除", "全部"],
        key=f"delete_filter_{selected_week}",
    )

    show = all_work.copy()
    if keyword:
        search_columns = [
            "类型", "星期", "时间", "负责人", "协同人", "城市",
            "代理商或服务商", "产品", "工作内容", "目标结果", "状态",
        ]
        searchable = (
            show[search_columns]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
        )
        show = show[searchable.str.contains(keyword, case=False, regex=False)]
    if owner_filter != "全部":
        show = show[show["负责人"] == owner_filter]
    if done_filter == "已完成":
        show = show[show["完成"]]
    elif done_filter == "未完成":
        show = show[~show["完成"]]
    if delete_filter == "未删除":
        show = show[~show["删除"]]
    elif delete_filter == "已删除":
        show = show[show["删除"]]

    active = all_work[~all_work["删除"]]
    total = len(active)
    done = int(active["完成"].sum()) if total else 0
    wm1, wm2, wm3 = st.columns(3)
    wm1.metric("本周工作", total)
    wm2.metric("已完成", done)
    wm3.metric("完成率", f"{done / total:.0%}" if total else "—")

    if show.empty:
        st.info("当前筛选条件下没有工作。")
    else:
        disabled_cols = [
            "类型", "星期", "时间", "协同人", "城市", "代理商或服务商",
            "产品", "工作内容", "目标结果", "状态",
        ]
        if not can_manage_all:
            disabled_cols.extend(["负责人", "删除"])
        edited_work = st.data_editor(
            show,
            hide_index=True,
            use_container_width=True,
            height=min(700, 100 + 36 * len(show)),
            disabled=disabled_cols,
            column_order=[
                "完成", "类型", "星期", "时间", "负责人", "协同人", "城市",
                "代理商或服务商", "产品", "工作内容", "目标结果", "状态", "删除",
            ],
            column_config={
                "task_key": None,
                "完成": st.column_config.CheckboxColumn(
                    "完成", help="完成状态按所选周写入数据库"
                ),
                "负责人": st.column_config.TextColumn(
                    "负责人", help="每周模板任务修改负责人后，以后每周沿用"
                ),
                "删除": st.column_config.CheckboxColumn(
                    "删除", help="每周模板任务只对所选周隐藏"
                ),
            },
            key=f"all_work_editor_{selected_week}_{owner_filter}_{done_filter}_{delete_filter}",
        )
        if st.button("💾 保存本周工作", type="primary", key=f"save_all_{selected_week}"):
            _save_all_work(selected_week, edited_work, username, can_manage_all)
            st.success("已保存。完成和删除状态已按周入库。")
            st.rerun()

        st.markdown("#### 工作明细与日志")
        task_labels = {
            row["task_key"]: (
                f"{row['负责人']}｜{row['城市'] or '全省'}｜"
                f"{row['产品'] or '综合'}｜{row['类型']}｜"
                f"{str(row['工作内容'])[:90]}"
            )
            for _, row in show.iterrows()
        }
        selected_task_key = st.selectbox(
            "选择一项工作填写明细",
            options=list(task_labels),
            format_func=lambda x: task_labels[x],
            key=f"log_task_{selected_week}_{owner_filter}_{done_filter}_{delete_filter}",
        )
        selected_row = show[show["task_key"] == selected_task_key].iloc[0]
        st.caption(
            f"负责人：{selected_row['负责人']}　｜　"
            f"目标结果：{selected_row['目标结果'] or '—'}"
        )
        logs = _load_logs(selected_week, selected_task_key)
        if not logs.empty:
            st.dataframe(logs, hide_index=True, use_container_width=True)
        with st.form(f"log_form_{selected_week}_{selected_task_key}", clear_on_submit=True):
            detail = st.text_area(
                "本次工作日志",
                placeholder="填写本次完成了什么、结果数据、存在问题和下一步。",
            )
            if st.form_submit_button("保存工作日志", use_container_width=True):
                if not detail.strip():
                    st.error("请填写工作日志。")
                else:
                    _add_log(selected_week, selected_task_key, detail.strip(), display_name)
                    st.success("工作日志已保存。")
                    st.rerun()

    st.info("“本周工作”由每周固定模板和本周新增任务共同组成。完成、删除和工作日志均保存到数据库，可按历史周回溯。")

with tab_new:
    st.markdown("#### 新增本周特定工作")
    people = _people()
    with st.form(f"new_pm_work_{selected_week}", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        owner = c1.selectbox(
            "负责人*",
            people,
            index=(people.index(display_name) if display_name in people else 0),
            disabled=not can_manage_all,
        )
        collaborator = c2.text_input("协同人", placeholder="如：陈琪能、某代理商业务员")
        product = c3.selectbox("产品", PRODUCTS)
        c4, c5 = st.columns(2)
        city = c4.text_input("城市", placeholder="如：杭州")
        dealer_provider = c5.text_input("代理商或服务商", placeholder="必须尽量落实到具体客户")
        work_item = st.text_area(
            "本周特定工作*",
            placeholder="使用可验收动词：完成首单、完成报价、新增有效机会、完成回访、完成复购……",
        )
        target_result = st.text_input("目标结果*", placeholder="如：形成2万元机会，周四前完成报价")
        due_date = st.date_input(
            "完成时间",
            value=selected_week + dt.timedelta(days=4),
            min_value=selected_week,
            max_value=selected_week + dt.timedelta(days=6),
        )
        submitted = st.form_submit_button("保存本周工作", type="primary", use_container_width=True)
        if submitted:
            if not work_item.strip() or not target_result.strip():
                st.error("请填写“本周特定工作”和“目标结果”。")
            else:
                _save_new_task({
                    "week_start": str(selected_week),
                    "owner": owner if can_manage_all else display_name,
                    "collaborator": collaborator.strip(),
                    "city": city.strip(),
                    "dealer_provider": dealer_provider.strip(),
                    "product": product,
                    "work_item": work_item.strip(),
                    "target_result": target_result.strip(),
                    "due_date": str(due_date),
                }, username)
                st.success("已保存，任务已进入“本周工作”。")
                st.rerun()

st.caption("任务状态统一为：未开始、进行中、已完成、逾期、取消。禁止使用“持续跟进、加强推动、重点关注”等无法验收的工作描述。")
