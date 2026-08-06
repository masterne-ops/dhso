"""AI 对话日志 — 记录每次提问、工具调用、答案、用户反馈

用途：
  - 看哪些问题被反复问 → 抽象成快捷按钮
  - 看哪些工具高频调用 → 优化它的性能/接口
  - 看 👎 反馈 → 改 prompt / 加新工具
  - 看用户问的问题里 LLM 没工具能解 → 扩展工具集
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from _loaders import DB_PATH


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_conversation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    turn_id         INTEGER,
    timestamp       TEXT,
    user_question   TEXT,
    llm_provider    TEXT,
    llm_model       TEXT,
    tool_calls_json TEXT,
    answer_text     TEXT,
    duration_ms     INTEGER,
    n_tool_calls    INTEGER,
    feedback        INTEGER,
    feedback_note   TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_aclog_session ON ai_conversation_log(session_id);
CREATE INDEX IF NOT EXISTS idx_aclog_time    ON ai_conversation_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_aclog_feedback ON ai_conversation_log(feedback);


CREATE TABLE IF NOT EXISTS meeting_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id        TEXT,             -- 一次完整校验的 ID
    timestamp       TEXT,
    file_name       TEXT,
    meeting_date    TEXT,             -- 会议日期（用户填或 AI 推断）
    raw_text        TEXT,             -- 纪要原文（节选）
    n_claims_extracted   INTEGER,
    n_claims_audited     INTEGER,
    n_consistent         INTEGER,     -- ✅ 一致
    n_deviation          INTEGER,     -- ⚠️ 偏差
    n_severe             INTEGER,     -- 🚨 严重不符
    n_inconclusive       INTEGER,     -- ❓ 无法验证
    n_caliber_diff       INTEGER,     -- 📅 口径差异
    audit_results_json   TEXT,        -- 完整结果 JSON
    duration_ms          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_audit_id   ON meeting_audit_log(audit_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON meeting_audit_log(timestamp);


-- V3 Code Agent 沙箱执行日志
-- 一行 = 一次 docker run（含完整轮对话、工具调用、生成的文件）
CREATE TABLE IF NOT EXISTS code_agent_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ui_session_id       TEXT,        -- streamlit 端会话 ID（同 UI 多轮对话共享）
    claude_session_id   TEXT,        -- claude code CLI 自己的 session ID（用于 --resume）
    resumed             INTEGER,     -- 0/1，本轮是否是 resume 之前的 session
    timestamp           TEXT,
    user_question       TEXT,
    answer_text         TEXT,        -- 拼接的 assistant 文本
    tool_calls_json     TEXT,        -- [{name, input, id}, ...]
    n_tool_calls        INTEGER,
    files_json          TEXT,        -- 生成的文件 [{rel, size}, ...]
    n_files             INTEGER,
    returncode          INTEGER,     -- docker run 退出码
    duration_ms         INTEGER,
    cost_usd            REAL,
    num_turns           INTEGER,
    model               TEXT,
    timed_out           INTEGER,
    error               TEXT,
    feedback            INTEGER,     -- 1=👍 / -1=👎
    feedback_note       TEXT
);

CREATE INDEX IF NOT EXISTS idx_calog_ui_session ON code_agent_log(ui_session_id);
CREATE INDEX IF NOT EXISTS idx_calog_time       ON code_agent_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_calog_feedback   ON code_agent_log(feedback);


-- 月度地市经营报告归档（page 10）
-- 一行 = 一份生成的报告。同地市同时段 UNIQUE，重新生成时 UPDATE
CREATE TABLE IF NOT EXISTS monthly_report_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    城市                TEXT NOT NULL,
    时段_start          TEXT NOT NULL,
    时段_end            TEXT NOT NULL,
    时段_label          TEXT NOT NULL,
    生成时间            TEXT NOT NULL,
    docx_path           TEXT,
    docx_size           INTEGER,
    核心结论            TEXT,
    data_summary_json   TEXT,        -- 输入给 AI 的 JSON 快照（重新生成时复算）
    ui_session_id       TEXT,        -- 沙箱执行 ID（关联 code_agent_log）
    returncode          INTEGER,
    duration_ms         INTEGER,
    cost_usd            REAL,
    UNIQUE(城市, 时段_start, 时段_end)
);

CREATE INDEX IF NOT EXISTS idx_mrl_city_period ON monthly_report_log(城市, 时段_start, 时段_end);
CREATE INDEX IF NOT EXISTS idx_mrl_time        ON monthly_report_log(生成时间);


-- 客户救援无效标记（业务员跑动评估时复盘的）
-- 一行 = 一次「救援未果」复盘记录，下次派单系统应过滤这些客户避免重复派
CREATE TABLE IF NOT EXISTS invalid_client_mark (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    客户编码          TEXT NOT NULL,
    客户名称          TEXT,
    城市              TEXT,
    区县              TEXT,
    标记时间          TEXT NOT NULL,
    标记业务员        TEXT,
    标记原因          TEXT,         -- "救援未果(R已恶化)" / "救援未果(R预警)" / "手动"
    评估期间_start    TEXT,
    评估期间_end      TEXT,
    撤销时间          TEXT DEFAULT NULL,
    撤销原因          TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_icm_code   ON invalid_client_mark(客户编码);
CREATE INDEX IF NOT EXISTS idx_icm_active ON invalid_client_mark(客户编码, 撤销时间);
CREATE INDEX IF NOT EXISTS idx_icm_time   ON invalid_client_mark(标记时间);


-- 业务方人工标注的「明确无采购意向」客户名单（最高置信度低效服务商）
-- 来源：业务侧 excel 导入（wx123.xlsx 等），覆盖写
-- 用途：page 03 假商挖掘 / page 07 跑动任务管理 / panorama / 派单台 都应过滤掉
CREATE TABLE IF NOT EXISTS closed_provider (
    客户编码        TEXT PRIMARY KEY,
    客户名称        TEXT,
    城市            TEXT,
    区县            TEXT,
    标记类型        TEXT,              -- '无采购意向' (默认) / '已注销' / '其他'
    上级客户        TEXT,
    服务商等级_原始 TEXT,
    年安防采购量_万 REAL,
    签约日期        TEXT,
    联系人          TEXT,
    联系电话        TEXT,
    导入时间        TEXT,
    导入文件        TEXT,
    备注            TEXT
);

CREATE INDEX IF NOT EXISTS idx_cp_city  ON closed_provider(城市);
CREATE INDEX IF NOT EXISTS idx_cp_type  ON closed_provider(标记类型);


-- ⚔️ 竞品 Top 服务商（海康为主的核心服务商，大华重点开拓目标）
-- 与 closed_provider 语义相反：不是放弃，是「资源应优先倾斜，把竞品份额转化为大华」
-- 来源：业务方 excel 导入（竞品top.xlsx 等）
CREATE TABLE IF NOT EXISTS competitor_top_provider (
    客户编码        TEXT PRIMARY KEY,
    客户名称        TEXT,
    省份            TEXT,
    城市            TEXT,
    区县            TEXT,
    责任人角色      TEXT,         -- 浙江SMB总监 / 分销经理 / SMB片区主管 等
    责任人姓名      TEXT,         -- 大华内部员工，可能是业务员也可能是部门领导
    老板姓名        TEXT,
    老板手机号      TEXT,
    客户经营品牌    TEXT,         -- 海康 / 海康、大华 / 大华、萤石、海康 等
    在售大华        INTEGER,      -- 0/1 派生：客户经营品牌含"大华"
    竞品体量_万     REAL,         -- 25 年竞品分销安防体量
    拜访内容        TEXT,
    转化策略        TEXT,
    备注            TEXT,
    导入时间        TEXT,
    导入文件        TEXT
);

CREATE INDEX IF NOT EXISTS idx_ctp_city ON competitor_top_provider(城市);
CREATE INDEX IF NOT EXISTS idx_ctp_role ON competitor_top_provider(责任人姓名);


-- 业务员月度跑动任务表（page 07）
-- 一行 = 某业务员某月对某客户的一条跑动任务
-- 分配 → 业务员确认/剔除/替换 → 月底核验完成情况
CREATE TABLE IF NOT EXISTS salesperson_task (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    任务年月     TEXT NOT NULL,         -- '2026-06'
    地市         TEXT NOT NULL,
    业务员       TEXT NOT NULL,
    客户编码     TEXT NOT NULL,
    客户名称     TEXT,
    区县         TEXT,
    所属一级客户 TEXT,
    任务类型     TEXT NOT NULL,         -- 严重漏跑救援 / R预警救援 / 维护到期复访 / F偏弱补访 / 手动添加
    任务说明     TEXT,                  -- "R=85天/F=2次/累计货值3.5万/V3"
    优先级       TEXT,                  -- 🚨高 / ⚠️中 / 🟢低
    分配时间     TEXT,
    确认状态     TEXT DEFAULT '待确认', -- 待确认 / 已确认 / 已剔除 / 业务员替换
    确认时间     TEXT,
    剔除原因     TEXT,
    完成状态     TEXT,                  -- 未完成 / 已拜访 / 已激活 / 逾期
    实际拜访日   TEXT,
    实际激活日   TEXT,
    备注         TEXT,
    UNIQUE(任务年月, 业务员, 客户编码)
);

CREATE INDEX IF NOT EXISTS idx_task_month_sp  ON salesperson_task(任务年月, 业务员);
CREATE INDEX IF NOT EXISTS idx_task_city      ON salesperson_task(任务年月, 地市);
CREATE INDEX IF NOT EXISTS idx_task_status    ON salesperson_task(任务年月, 确认状态, 完成状态);


-- 应用用户 + 数据权限（page 00 用户管理）— Phase 2 权限系统
-- bound_salesperson: 把用户账号绑定到「业务数据里的业务员名」— 用于：
--   1. 任务系统识别"我的任务"
--   2. 跑动评估系统识别"评估我自己"
--   3. 未来的手机端"我的跑动手帐"
-- 一般 role=salesperson 的用户必须绑定；admin/manager 可选
CREATE TABLE IF NOT EXISTS app_user (
    username           TEXT PRIMARY KEY,
    password_hash      TEXT NOT NULL,
    role               TEXT NOT NULL,         -- admin / manager / salesperson / guest
    full_name          TEXT,
    bound_salesperson  TEXT,                  -- 绑定的业务员名（对应 salesperson_scope.业务员）
    enabled            INTEGER DEFAULT 1,
    created_at         TEXT,
    last_login         TEXT,
    备注                TEXT
);

CREATE TABLE IF NOT EXISTS app_user_scope (
    username    TEXT NOT NULL,
    scope_type  TEXT NOT NULL,           -- city | district | salesperson | dealer | page(产品经理页面白名单)
    scope_value TEXT NOT NULL,
    PRIMARY KEY (username, scope_type, scope_value)
);

CREATE INDEX IF NOT EXISTS idx_user_scope_user ON app_user_scope(username);


-- 智能搜索查询日志（page 00 智能搜索）
CREATE TABLE IF NOT EXISTS ai_search_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    时间      TEXT NOT NULL,
    用户      TEXT,
    用户角色  TEXT,
    查询      TEXT NOT NULL,
    推荐结果  TEXT,        -- JSON: [{url, title, reason}, ...]
    点击的url TEXT         -- 用户最终点哪个（select=null 表示未点）
);

CREATE INDEX IF NOT EXISTS idx_search_time ON ai_search_log(时间);
CREATE INDEX IF NOT EXISTS idx_search_user ON ai_search_log(用户);


-- ══════════════════════════════════════════════
-- 🏷️ 服务商标签字典 (admin 维护)
-- ══════════════════════════════════════════════
-- tag_key:    机器可读小写 ASCII（vest / umbrella / low_eff / closed / competitor_top / next_focus）
-- tag_name:   中文显示名（可改名，但 tag_key 锁定）
-- source_type: 标签来源类型
--   - manual:     纯人工打标（如 next_focus 下月重点）
--   - imported:   外部导入维护（vest 来自 vest_account；competitor_top 来自竞品 top excel）
--   - heuristic:  规则实时计算（umbrella 同老板同电话；low_eff 签约60+天但红包<=2）
--   - hybrid:     既有规则又支持人工补打（closed 明确无采购意向）
-- 设计：不允许删除标签，只允许改名 / 禁用
CREATE TABLE IF NOT EXISTS provider_tag_def (
    tag_key      TEXT PRIMARY KEY,
    tag_name     TEXT NOT NULL UNIQUE,
    tag_icon     TEXT,
    tag_color    TEXT,                  -- streamlit 色名：red / orange / blue / gray / yellow / green / violet
    tag_desc     TEXT,
    source_type  TEXT NOT NULL DEFAULT 'manual',
    enabled      INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER DEFAULT 100,
    created_at   TEXT,
    created_by   TEXT,
    updated_at   TEXT,
    updated_by   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tag_def_enabled ON provider_tag_def(enabled);


-- ══════════════════════════════════════════════
-- 🏷️ 服务商×标签 分配（多对多）
-- ══════════════════════════════════════════════
-- 用户对服务商人工挂标签的记录。撤销采用软删（撤销时间字段）
-- 同（客户编码 + tag_key）当前最多一条 active（撤销时间 IS NULL）
CREATE TABLE IF NOT EXISTS provider_tag_assignment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    客户编码    TEXT NOT NULL,
    tag_key     TEXT NOT NULL,
    客户名称    TEXT,
    城市        TEXT,
    区县        TEXT,
    标记来源    TEXT,                    -- 'page05_radar' / 'page21_competitor' / 'import:wx123' 等
    标记人      TEXT,
    标记时间    TEXT,
    备注        TEXT,
    撤销时间    TEXT,
    撤销人      TEXT,
    撤销原因    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tag_asgn_code   ON provider_tag_assignment(客户编码);
CREATE INDEX IF NOT EXISTS idx_tag_asgn_key    ON provider_tag_assignment(tag_key);
CREATE INDEX IF NOT EXISTS idx_tag_asgn_active ON provider_tag_assignment(客户编码, tag_key, 撤销时间);
-- 同（客户编码+tag_key）只允许一条 active
CREATE UNIQUE INDEX IF NOT EXISTS uq_tag_asgn_active
    ON provider_tag_assignment(客户编码, tag_key) WHERE 撤销时间 IS NULL;


-- ══════════════════════════════════════════════
-- ☂️ 伞形组（人工建立的明确关联组）
-- ══════════════════════════════════════════════
-- 伞形不是单户标签，是组关系：一组共享同一个伞形身份
-- 启发式（同老板姓名+电话）继续作为自动候选推荐，但明确分组以人工建立的为准
-- 一个客户同时只能属于一个 active 组（uq_umb_active 索引）
CREATE TABLE IF NOT EXISTS provider_umbrella_group (
    group_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name      TEXT,                  -- 组名（默认 = 主账号公司名）
    主账号_客户编码  TEXT NOT NULL,
    主账号_客户名称  TEXT,
    城市            TEXT,
    区县            TEXT,
    创建时间        TEXT,
    创建人          TEXT,
    备注            TEXT,
    解散时间        TEXT,                  -- NULL = 仍有效
    解散人          TEXT,
    解散原因        TEXT
);

CREATE INDEX IF NOT EXISTS idx_umb_group_city ON provider_umbrella_group(城市);
CREATE INDEX IF NOT EXISTS idx_umb_group_active ON provider_umbrella_group(解散时间);

-- 组成员
CREATE TABLE IF NOT EXISTS provider_umbrella_member (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      INTEGER NOT NULL,
    客户编码      TEXT NOT NULL,
    客户名称      TEXT,
    加入时间      TEXT,
    加入人        TEXT,
    备注          TEXT,
    撤销时间      TEXT,
    撤销人        TEXT,
    撤销原因      TEXT
);

CREATE INDEX IF NOT EXISTS idx_umb_mem_grp  ON provider_umbrella_member(group_id);
CREATE INDEX IF NOT EXISTS idx_umb_mem_code ON provider_umbrella_member(客户编码);
-- 一个客户在「active 状态」下只能属于一个组
CREATE UNIQUE INDEX IF NOT EXISTS uq_umb_mem_active
    ON provider_umbrella_member(客户编码) WHERE 撤销时间 IS NULL;


-- ══════════════════════════════════════════════
-- 🏷️ 打标事件流水（所有标签每次 打标/撤销 都追加一条，永不覆盖）
-- ══════════════════════════════════════════════
-- provider_tag_assignment 只保留每个 (客户+标签) 的最新一条（幂等覆盖）；
-- 本表记录完整历史时间线 —— 用于「本月待激活客户」按打标月份归集 + 跨月遗留追踪。
CREATE TABLE IF NOT EXISTS provider_tag_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    客户编码    TEXT NOT NULL,
    tag_key     TEXT NOT NULL,
    事件类型    TEXT NOT NULL,            -- 'assign' / 'revoke'
    客户名称    TEXT,
    城市        TEXT,
    区县        TEXT,
    标记来源    TEXT,
    操作人      TEXT,
    事件时间    TEXT NOT NULL,
    事件年月    TEXT,                     -- 派生 YYYY-MM，按月归集用
    备注        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tag_evt_code ON provider_tag_event(客户编码, tag_key);
CREATE INDEX IF NOT EXISTS idx_tag_evt_key  ON provider_tag_event(tag_key, 事件年月);
CREATE INDEX IF NOT EXISTS idx_tag_evt_time ON provider_tag_event(事件时间);


-- ══════════════════════════════════════════════
-- ⏳ 待激活客户跟进备注（按 客户×月 一条，可覆盖更新）
-- ══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS pending_followup_note (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    客户编码    TEXT NOT NULL,
    归属年月    TEXT NOT NULL,            -- YYYY-MM
    备注        TEXT,
    更新人      TEXT,
    更新时间    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_followup_note
    ON pending_followup_note(客户编码, 归属年月);
"""


def _migrate_closed_provider_cols():
    """给 closed_provider 表加 4 列（如果已存在则忽略）— 支持页面级打标 / 撤销"""
    conn = sqlite3.connect(DB_PATH)
    try:
        for col_def in [
            "ALTER TABLE closed_provider ADD COLUMN 标记来源 TEXT",
            "ALTER TABLE closed_provider ADD COLUMN 标记人 TEXT",
            "ALTER TABLE closed_provider ADD COLUMN 撤销时间 TEXT",
            "ALTER TABLE closed_provider ADD COLUMN 撤销人 TEXT",
            "ALTER TABLE closed_provider ADD COLUMN 撤销原因 TEXT",
        ]:
            try:
                conn.execute(col_def)
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


def _migrate_app_user_cols():
    """给 app_user 表加新列 — 业务员绑定"""
    conn = sqlite3.connect(DB_PATH)
    try:
        for col_def in [
            "ALTER TABLE app_user ADD COLUMN bound_salesperson TEXT",
        ]:
            try:
                conn.execute(col_def)
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6 个初始标签定义（首次启动 _ensure_table 时插入）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INITIAL_TAG_DEFS = [
    # tag_key,  tag_name,        icon,  color,    source_type,  desc,                                                        sort
    ('vest',           '马甲',            '🎭', 'red',     'imported',
     '已确认的马甲账号 — 来自 vest_account 表导入', 10),
    ('umbrella',       '伞形',            '☂️', 'orange',  'heuristic',
     '同老板姓名+电话注册≥2家不同服务商 — 实时识别', 20),
    ('low_eff',        '低效签约',         '❌', 'blue',    'heuristic',
     '签约60+天但红包扫码≤2台 — 实时识别', 30),
    ('closed',         '明确无采购意向',   '🚫', 'gray',    'hybrid',
     '业务人工标注：电话/线下沟通已确认放弃 — 排除派单/任务', 40),
    ('competitor_top', '竞品 Top',        '⚔️', 'violet',  'imported',
     '海康/宇视核心服务商 — 重点开拓目标', 50),
    ('next_focus',     '下月重点',         '🎯', 'green',   'manual',
     '业务规划：下月需要重点拜访/激活/激励的服务商', 60),
]


def _seed_initial_tags():
    """首次启动时插入 6 个预置标签（如已存在不动）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        for tag_key, tag_name, icon, color, source_type, desc, sort_order in INITIAL_TAG_DEFS:
            # 用 INSERT OR IGNORE 防覆盖（不动 admin 已 rename 的）
            conn.execute("""
                INSERT OR IGNORE INTO provider_tag_def
                  (tag_key, tag_name, tag_icon, tag_color, tag_desc,
                   source_type, enabled, sort_order, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'system')
            """, (tag_key, tag_name, icon, color, desc, source_type, sort_order, now))
        conn.commit()
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 服务商标签聚合视图 provider_tags_v
# 一行 = (客户编码, tag_key) — 来自 5 个来源 UNION ALL
#   1. vest_account               → vest
#   2. closed_provider (active)   → closed
#   3. competitor_top_provider    → competitor_top
#   4. provider_tag_assignment    → any user-defined tag (active)
#   5. heuristic 伞形 (老板姓名+电话 ≥ 2 家)        → umbrella
#   6. heuristic 低效 (签约60+天 红包≤2 台)         → low_eff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROVIDER_TAGS_VIEW_SQL = """
DROP VIEW IF EXISTS provider_tags_v;

CREATE VIEW provider_tags_v AS
-- 1️⃣ 马甲账号（vest_account 全表）
SELECT
    服务商客户编码 AS 客户编码,
    'vest'        AS tag_key,
    '导入'        AS 标记来源,
    '系统'        AS 标记人,
    NULL          AS 标记时间,
    ''            AS 备注,
    'imported'    AS origin
FROM vest_account
WHERE 服务商客户编码 IS NOT NULL AND 服务商客户编码 != ''

UNION ALL

-- 2️⃣ 明确无采购意向（active）
SELECT
    客户编码,
    'closed'      AS tag_key,
    COALESCE(NULLIF(标记来源, ''), '导入')  AS 标记来源,
    COALESCE(NULLIF(标记人, ''),   '系统')  AS 标记人,
    导入时间      AS 标记时间,
    COALESCE(备注, '')                      AS 备注,
    'hybrid'      AS origin
FROM closed_provider
WHERE 撤销时间 IS NULL

UNION ALL

-- 3️⃣ 竞品 Top 服务商
SELECT
    客户编码,
    'competitor_top' AS tag_key,
    '导入'           AS 标记来源,
    '系统'           AS 标记人,
    导入时间         AS 标记时间,
    COALESCE(备注, '') AS 备注,
    'imported'       AS origin
FROM competitor_top_provider
WHERE 客户编码 IS NOT NULL AND 客户编码 != ''

UNION ALL

-- 4️⃣ 用户分配的标签（active 任意 tag_key）
SELECT
    pta.客户编码,
    pta.tag_key,
    COALESCE(pta.标记来源, '') AS 标记来源,
    COALESCE(pta.标记人, '')   AS 标记人,
    pta.标记时间,
    COALESCE(pta.备注, '')     AS 备注,
    'manual'                   AS origin
FROM provider_tag_assignment pta
WHERE pta.撤销时间 IS NULL

UNION ALL

-- 5a️⃣ 伞形 — 明确建组的成员（最高优先，标 group_id）
SELECT
    m.客户编码,
    'umbrella' AS tag_key,
    'group:' || m.group_id AS 标记来源,
    COALESCE(m.加入人, '系统') AS 标记人,
    m.加入时间 AS 标记时间,
    COALESCE(m.备注, '组主账号: ' || COALESCE(g.主账号_客户名称, g.主账号_客户编码)) AS 备注,
    'manual' AS origin
FROM provider_umbrella_member m
JOIN provider_umbrella_group g ON g.group_id = m.group_id
WHERE m.撤销时间 IS NULL
  AND g.解散时间 IS NULL

UNION ALL

-- 5b️⃣ 伞形 — 启发式（老板姓名 + 老板电话 ≥ 2 家）
-- 排除已经在明确组里的，避免双计
SELECT
    pp.客户编码,
    'umbrella' AS tag_key,
    '系统识别' AS 标记来源,
    '系统'     AS 标记人,
    NULL       AS 标记时间,
    '同老板/同电话 ≥2 家'  AS 备注,
    'heuristic' AS origin
FROM provider_profile pp
WHERE COALESCE(pp.老板姓名, '') <> ''
  AND COALESCE(pp.老板电话, '') <> ''
  AND (pp.老板姓名 || '|' || pp.老板电话) IN (
        SELECT 老板姓名 || '|' || 老板电话
          FROM provider_profile
         WHERE COALESCE(老板姓名, '') <> ''
           AND COALESCE(老板电话, '') <> ''
         GROUP BY 老板姓名, 老板电话
         HAVING COUNT(*) >= 2
  )
  -- 排除已在 active 组的（避免一个客户既是启发式又是 group:N）
  AND pp.客户编码 NOT IN (
        SELECT m2.客户编码
          FROM provider_umbrella_member m2
          JOIN provider_umbrella_group g2 ON g2.group_id = m2.group_id
         WHERE m2.撤销时间 IS NULL AND g2.解散时间 IS NULL
  )

UNION ALL

-- 6️⃣ 低效签约（签约 60+ 天但红包扫码 ≤ 2 台）
SELECT
    pc.客户编码,
    'low_eff'  AS tag_key,
    '系统识别' AS 标记来源,
    '系统'     AS 标记人,
    NULL       AS 标记时间,
    '签约60+天 红包≤2台'  AS 备注,
    'heuristic' AS origin
FROM provider_contract pc
LEFT JOIN (
    SELECT 上线客户编码 AS 客户编码, COUNT(*) AS n_rp
      FROM install_redpack
     GROUP BY 上线客户编码
) ca ON ca.客户编码 = pc.客户编码
WHERE pc.签约日期 IS NOT NULL
  AND date(pc.签约日期) <= date('now', '-60 day')
  AND COALESCE(ca.n_rp, 0) <= 2
;
"""


def _ensure_tag_view():
    """创建/重建 provider_tags_v 视图（依赖 vest_account / closed_provider / competitor_top_provider / provider_tag_assignment / provider_profile / provider_contract / install_redpack 都存在）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # 检查依赖表都在；缺一个就不建（开发环境可能还没导数据）
        needed = ['vest_account', 'closed_provider', 'competitor_top_provider',
                  'provider_tag_assignment', 'provider_profile', 'provider_contract',
                  'install_redpack', 'provider_tag_def',
                  'provider_umbrella_group', 'provider_umbrella_member']
        have = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in needed if t not in have]
        if missing:
            # 缺哪些表就跳过 view 创建（避免启动报错）
            return False
        cur.executescript(PROVIDER_TAGS_VIEW_SQL)
        conn.commit()
        return True
    finally:
        conn.close()


def _ensure_table():
    """首次调用时建表 + 老表加新列迁移 + 视图 + 预置标签"""
    if not DB_PATH.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()
    # 增量迁移老表新列（幂等）
    _migrate_closed_provider_cols()
    _migrate_app_user_cols()
    # 预置 6 个标签（幂等）
    try:
        _seed_initial_tags()
    except Exception:
        pass
    # 建/重建 provider_tags_v 视图（依赖表齐全时）
    try:
        _ensure_tag_view()
    except Exception:
        pass


def new_session_id() -> str:
    """生成新的会话 ID"""
    return uuid.uuid4().hex[:12]


def log_turn(
    *,
    session_id: str,
    turn_id: int,
    user_question: str,
    llm_provider: str = '',
    llm_model: str = '',
    tool_calls: list = None,
    answer_text: str = '',
    duration_ms: int = 0,
    error: str = None,
) -> int:
    """记录一次提问 / 答案。返回 row id（用于后续写反馈）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ai_conversation_log
              (session_id, turn_id, timestamp, user_question,
               llm_provider, llm_model, tool_calls_json, answer_text,
               duration_ms, n_tool_calls, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            turn_id,
            datetime.now().isoformat(timespec='seconds'),
            user_question,
            llm_provider,
            llm_model,
            json.dumps(tool_calls or [], ensure_ascii=False),
            answer_text,
            duration_ms,
            len(tool_calls or []),
            error,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_feedback(row_id: int, feedback: int, note: str = None):
    """写用户反馈：1 = 👍 / -1 = 👎"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            UPDATE ai_conversation_log
               SET feedback = ?, feedback_note = ?
             WHERE id = ?
        """, (feedback, note, row_id))
        conn.commit()
    finally:
        conn.close()


def read_logs(*, limit: int = 200, session_id: str = None,
              feedback: int = None, since: str = None) -> pd.DataFrame:
    """读日志，可按 session / feedback / 时间过滤"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM ai_conversation_log WHERE 1=1"
        params = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if feedback is not None:
            sql += " AND feedback = ?"
            params.append(feedback)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        df = pd.read_sql(sql, conn, params=params)
        return df
    finally:
        conn.close()


def stats_summary() -> dict:
    """日志总览统计"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ai_conversation_log")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ai_conversation_log WHERE feedback = 1")
        n_up = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ai_conversation_log WHERE feedback = -1")
        n_down = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT session_id) FROM ai_conversation_log")
        n_sessions = cur.fetchone()[0]
        cur.execute("SELECT AVG(duration_ms) FROM ai_conversation_log")
        avg_ms = cur.fetchone()[0] or 0
        return {
            'total_turns': total,
            'distinct_sessions': n_sessions,
            'feedback_up': n_up,
            'feedback_down': n_down,
            'avg_duration_ms': int(avg_ms),
        }
    finally:
        conn.close()


def log_audit(
    *,
    audit_id: str,
    file_name: str,
    meeting_date: str,
    raw_text: str,
    n_claims_extracted: int,
    n_claims_audited: int,
    status_counts: dict,  # {"一致": N, "偏差": N, ...}
    audit_results: list,
    duration_ms: int = 0,
) -> int:
    """记录一次会议纪要校验"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO meeting_audit_log
              (audit_id, timestamp, file_name, meeting_date, raw_text,
               n_claims_extracted, n_claims_audited,
               n_consistent, n_deviation, n_severe, n_inconclusive, n_caliber_diff,
               audit_results_json, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            audit_id,
            datetime.now().isoformat(timespec='seconds'),
            file_name,
            meeting_date,
            raw_text[:5000],  # 截断长文
            n_claims_extracted,
            n_claims_audited,
            status_counts.get('一致', 0),
            status_counts.get('偏差', 0),
            status_counts.get('严重不符', 0),
            status_counts.get('无法验证', 0),
            status_counts.get('口径差异', 0),
            json.dumps(audit_results, ensure_ascii=False, default=str),
            duration_ms,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def read_audits(limit: int = 50) -> pd.DataFrame:
    """读历史校验"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            "SELECT * FROM meeting_audit_log ORDER BY timestamp DESC LIMIT ?",
            conn, params=[limit],
        )
        return df
    finally:
        conn.close()


def tool_usage_stats(limit: int = 20) -> pd.DataFrame:
    """工具调用频次统计"""
    df = read_logs(limit=10000)
    if df.empty:
        return pd.DataFrame()
    counts = {}
    for tc in df['tool_calls_json']:
        try:
            calls = json.loads(tc) if tc else []
        except Exception:
            continue
        for c in calls:
            name = c.get('name') if isinstance(c, dict) else None
            if name:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return pd.DataFrame()
    out = pd.DataFrame([{'工具': k, '调用次数': v} for k, v in counts.items()])
    return out.sort_values('调用次数', ascending=False).head(limit)


# ══════════════════════════════════════════════
# V3 Code Agent 日志
# ══════════════════════════════════════════════

def log_code_agent_turn(
    *,
    ui_session_id: str,
    claude_session_id: str = '',
    resumed: bool = False,
    user_question: str = '',
    answer_text: str = '',
    tool_calls: list = None,
    files: list = None,
    returncode: int = -1,
    duration_ms: int = 0,
    cost_usd: float = None,
    num_turns: int = None,
    model: str = '',
    timed_out: bool = False,
    error: str = None,
) -> int:
    """记录一次 V3 Code Agent 沙箱执行。返回 row id"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO code_agent_log
              (ui_session_id, claude_session_id, resumed, timestamp,
               user_question, answer_text,
               tool_calls_json, n_tool_calls,
               files_json, n_files,
               returncode, duration_ms, cost_usd, num_turns,
               model, timed_out, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ui_session_id,
            claude_session_id,
            1 if resumed else 0,
            datetime.now().isoformat(timespec='seconds'),
            user_question,
            answer_text[:50000] if answer_text else '',  # 截断 50KB
            json.dumps(tool_calls or [], ensure_ascii=False, default=str)[:200000],
            len(tool_calls or []),
            json.dumps(files or [], ensure_ascii=False, default=str)[:50000],
            len(files or []),
            returncode,
            duration_ms,
            cost_usd,
            num_turns,
            model,
            1 if timed_out else 0,
            error,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_code_agent_feedback(row_id: int, feedback: int, note: str = None):
    """V3 Code Agent 用户反馈：1 = 👍 / -1 = 👎"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            UPDATE code_agent_log
               SET feedback = ?, feedback_note = ?
             WHERE id = ?
        """, (feedback, note, row_id))
        conn.commit()
    finally:
        conn.close()


def read_code_agent_logs(
    *,
    limit: int = 200,
    ui_session_id: str = None,
    feedback: int = None,
    since: str = None,
) -> pd.DataFrame:
    """读 V3 Code Agent 日志"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM code_agent_log WHERE 1=1"
        params = []
        if ui_session_id:
            sql += " AND ui_session_id = ?"
            params.append(ui_session_id)
        if feedback is not None:
            sql += " AND feedback = ?"
            params.append(feedback)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def code_agent_stats() -> dict:
    """V3 Code Agent 统计概况"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM code_agent_log")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT ui_session_id) FROM code_agent_log")
        n_sessions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM code_agent_log WHERE returncode = 0")
        n_ok = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM code_agent_log WHERE feedback = 1")
        n_up = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM code_agent_log WHERE feedback = -1")
        n_down = cur.fetchone()[0]
        cur.execute("SELECT AVG(duration_ms) FROM code_agent_log WHERE returncode = 0")
        avg_ms = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(cost_usd) FROM code_agent_log WHERE cost_usd IS NOT NULL")
        total_cost = cur.fetchone()[0] or 0
        return {
            'total_turns': total,
            'distinct_sessions': n_sessions,
            'success_turns': n_ok,
            'success_rate': n_ok / total if total else 0,
            'feedback_up': n_up,
            'feedback_down': n_down,
            'avg_duration_ms': int(avg_ms),
            'total_cost_usd': total_cost,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 月度地市经营报告归档（page 10）
# ══════════════════════════════════════════════

def save_monthly_report(
    *,
    城市: str,
    时段_start: str,
    时段_end: str,
    时段_label: str,
    docx_path: str = None,
    核心结论: str = '',
    data_summary: dict = None,
    ui_session_id: str = '',
    returncode: int = -1,
    duration_ms: int = 0,
    cost_usd: float = None,
) -> int:
    """归档一份月报。同地市同时段已存在 → UPDATE（覆盖）。返回 row id。"""
    from pathlib import Path as _Path

    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # 算 docx 文件大小
        size = 0
        if docx_path:
            try:
                p = _Path(docx_path)
                if p.exists():
                    size = p.stat().st_size
            except OSError:
                pass

        # 看有没有
        cur.execute("""
            SELECT id FROM monthly_report_log
             WHERE 城市 = ? AND 时段_start = ? AND 时段_end = ?
        """, (城市, 时段_start, 时段_end))
        existing = cur.fetchone()

        now = datetime.now().isoformat(timespec='seconds')
        data_json = json.dumps(data_summary or {}, ensure_ascii=False, default=str)[:500000]

        if existing:
            cur.execute("""
                UPDATE monthly_report_log
                   SET 生成时间 = ?, docx_path = ?, docx_size = ?,
                       核心结论 = ?, data_summary_json = ?,
                       ui_session_id = ?, returncode = ?,
                       duration_ms = ?, cost_usd = ?
                 WHERE id = ?
            """, (
                now, docx_path, size,
                核心结论, data_json,
                ui_session_id, returncode,
                duration_ms, cost_usd,
                existing[0],
            ))
            row_id = existing[0]
        else:
            cur.execute("""
                INSERT INTO monthly_report_log
                  (城市, 时段_start, 时段_end, 时段_label,
                   生成时间, docx_path, docx_size, 核心结论,
                   data_summary_json, ui_session_id,
                   returncode, duration_ms, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                城市, 时段_start, 时段_end, 时段_label,
                now, docx_path, size, 核心结论,
                data_json, ui_session_id,
                returncode, duration_ms, cost_usd,
            ))
            row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def get_monthly_report(城市: str, 时段_start: str, 时段_end: str) -> dict:
    """查同地市同时段的已生成报告。没有返回 None"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("""
            SELECT * FROM monthly_report_log
             WHERE 城市 = ? AND 时段_start = ? AND 时段_end = ?
        """, conn, params=(城市, 时段_start, 时段_end))
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    finally:
        conn.close()


def list_monthly_reports(limit: int = 50) -> pd.DataFrame:
    """列历史报告，按生成时间倒序"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql(f"""
            SELECT id, 城市, 时段_label, 生成时间, docx_size, 核心结论,
                   returncode, duration_ms, cost_usd, docx_path
              FROM monthly_report_log
             ORDER BY 生成时间 DESC
             LIMIT {int(limit)}
        """, conn)
    finally:
        conn.close()


def delete_monthly_report(report_id: int, delete_file: bool = True):
    """删除一条记录（可选同时删除磁盘 docx 文件）"""
    from pathlib import Path as _Path
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        if delete_file:
            cur.execute("SELECT docx_path FROM monthly_report_log WHERE id = ?", (report_id,))
            row = cur.fetchone()
            if row and row[0]:
                try:
                    p = _Path(row[0])
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
        cur.execute("DELETE FROM monthly_report_log WHERE id = ?", (report_id,))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 客户救援无效标记
# ══════════════════════════════════════════════

def mark_invalid_clients(
    *,
    records: list,  # [{客户编码, 客户名称, 城市, 区县, 标记业务员, 标记原因, 评估期间_start, 评估期间_end}]
) -> int:
    """批量标记客户为救援无效。返回插入行数"""
    _ensure_table()
    if not records:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat(timespec='seconds')
        n_inserted = 0
        for r in records:
            cur.execute("""
                INSERT INTO invalid_client_mark
                  (客户编码, 客户名称, 城市, 区县, 标记时间,
                   标记业务员, 标记原因, 评估期间_start, 评估期间_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r['客户编码'], r.get('客户名称', ''),
                r.get('城市', ''), r.get('区县', ''),
                now, r.get('标记业务员', ''), r.get('标记原因', ''),
                r.get('评估期间_start', ''), r.get('评估期间_end', ''),
            ))
            n_inserted += 1
        conn.commit()
        return n_inserted
    finally:
        conn.close()


def revoke_invalid_mark(mark_id: int, reason: str = ''):
    """撤销一条无效标记"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            UPDATE invalid_client_mark
               SET 撤销时间 = ?, 撤销原因 = ?
             WHERE id = ?
        """, (datetime.now().isoformat(timespec='seconds'), reason, mark_id))
        conn.commit()
    finally:
        conn.close()


def list_invalid_marks(*, city: str = None, active_only: bool = True, limit: int = 500) -> pd.DataFrame:
    """查询无效标记列表"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM invalid_client_mark WHERE 1=1"
        params = []
        if city:
            sql += " AND 城市 = ?"
            params.append(city)
        if active_only:
            sql += " AND 撤销时间 IS NULL"
        sql += " ORDER BY 标记时间 DESC LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def active_invalid_codes(city: str = None) -> set:
    """返回当前生效的无效客户编码集合（给派单系统过滤用）"""
    df = list_invalid_marks(city=city, active_only=True, limit=100000)
    if df.empty:
        return set()
    return set(df['客户编码'].astype(str))


# ══════════════════════════════════════════════
# 「明确无采购意向」低效服务商名单（业务方人工标注）
# ══════════════════════════════════════════════

def import_closed_providers(records: list[dict], source_file: str = '') -> dict:
    """批量导入「无采购意向」服务商名单。同客户编码已存在则覆盖。

    Returns: {'inserted': N, 'updated': N, 'total': N}
    """
    _ensure_table()
    if not records:
        return {'inserted': 0, 'updated': 0, 'total': 0}
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat(timespec='seconds')
        n_ins = n_upd = 0
        for r in records:
            code = r.get('客户编码')
            if not code:
                continue
            existed = cur.execute(
                "SELECT 1 FROM closed_provider WHERE 客户编码 = ?", (str(code),)
            ).fetchone()
            cur.execute("""
                INSERT OR REPLACE INTO closed_provider
                  (客户编码, 客户名称, 城市, 区县, 标记类型,
                   上级客户, 服务商等级_原始, 年安防采购量_万,
                   签约日期, 联系人, 联系电话,
                   导入时间, 导入文件, 备注)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(code), r.get('客户名称'),
                r.get('城市'), r.get('区县'),
                r.get('标记类型', '无采购意向'),
                r.get('上级客户'), r.get('服务商等级_原始'),
                r.get('年安防采购量_万'),
                r.get('签约日期'), r.get('联系人'), r.get('联系电话'),
                now, source_file, r.get('备注'),
            ))
            if existed:
                n_upd += 1
            else:
                n_ins += 1
        conn.commit()
        return {'inserted': n_ins, 'updated': n_upd, 'total': n_ins + n_upd}
    finally:
        conn.close()


def list_closed_providers(*, city: str = None,
                          标记类型: str = None,
                          limit: int = 10000) -> pd.DataFrame:
    """查询明确无采购意向 低效服务商列表"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM closed_provider WHERE 1=1"
        params = []
        if city:
            sql += " AND 城市 = ?"
            params.append(city)
        if 标记类型:
            sql += " AND 标记类型 = ?"
            params.append(标记类型)
        sql += " ORDER BY 城市, 区县, 客户编码 LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def clear_closed_providers(city: str = None) -> int:
    """清空（可选按城市）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        if city:
            cur = conn.execute("DELETE FROM closed_provider WHERE 城市 = ?", (city,))
        else:
            cur = conn.execute("DELETE FROM closed_provider")
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def mark_closed_provider(
    *,
    客户编码: str, 客户名称: str = '',
    城市: str = '', 区县: str = '',
    标记来源: str = 'manual', 标记人: str = '',
    备注: str = '',
) -> dict:
    """单条标记「明确无采购意向」— 支持页面级打标。

    幂等：同客户编码已存在则更新；已撤销的会被复活（清空撤销字段）

    Returns: {'action': 'inserted' / 'updated' / 'revived', '客户编码': ...}
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        cur = conn.execute(
            "SELECT 撤销时间 FROM closed_provider WHERE 客户编码 = ?",
            (str(客户编码),),
        ).fetchone()

        if cur is None:
            conn.execute("""
                INSERT INTO closed_provider
                  (客户编码, 客户名称, 城市, 区县, 标记类型,
                   导入时间, 导入文件, 备注, 标记来源, 标记人)
                VALUES (?, ?, ?, ?, '无采购意向', ?, ?, ?, ?, ?)
            """, (str(客户编码), 客户名称, 城市, 区县,
                  now, '', 备注, 标记来源, 标记人))
            action = 'inserted'
        else:
            # 已存在 — 复活（清撤销字段）+ 更新元信息
            conn.execute("""
                UPDATE closed_provider
                   SET 客户名称 = COALESCE(NULLIF(?, ''), 客户名称),
                       城市 = COALESCE(NULLIF(?, ''), 城市),
                       区县 = COALESCE(NULLIF(?, ''), 区县),
                       备注 = COALESCE(NULLIF(?, ''), 备注),
                       标记来源 = ?,
                       标记人 = ?,
                       导入时间 = ?,
                       撤销时间 = NULL,
                       撤销人 = NULL,
                       撤销原因 = NULL
                 WHERE 客户编码 = ?
            """, (客户名称, 城市, 区县, 备注, 标记来源, 标记人, now, str(客户编码)))
            action = 'revived' if cur[0] else 'updated'
        conn.commit()
        return {'action': action, '客户编码': str(客户编码)}
    finally:
        conn.close()


def revoke_closed_provider(
    客户编码: str, *, 撤销人: str = '', 撤销原因: str = '',
) -> bool:
    """软撤销「明确无采购意向」标签"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        cur = conn.execute("""
            UPDATE closed_provider
               SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = ?
             WHERE 客户编码 = ? AND 撤销时间 IS NULL
        """, (now, 撤销人, 撤销原因, str(客户编码)))
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def get_closed_state(客户编码: str) -> dict:
    """查询某客户的「明确无采购意向」状态。

    Returns:
      {状态: 'active' / 'revoked' / 'none',
       标记人, 标记时间, 标记来源, 备注,
       撤销人, 撤销时间, 撤销原因}
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT 客户名称, 城市, 区县, 标记类型, 备注,
                   导入时间, 导入文件, 标记来源, 标记人,
                   撤销时间, 撤销人, 撤销原因
              FROM closed_provider WHERE 客户编码 = ?
        """, (str(客户编码),)).fetchone()
        if not row:
            return {'状态': 'none'}
        return {
            '状态': 'revoked' if row[9] else 'active',
            '客户名称': row[0], '城市': row[1], '区县': row[2],
            '标记类型': row[3], '备注': row[4],
            '标记时间': row[5], '导入文件': row[6],
            '标记来源': row[7], '标记人': row[8],
            '撤销时间': row[9], '撤销人': row[10], '撤销原因': row[11],
        }
    finally:
        conn.close()


def closed_provider_codes(city: str = None) -> set:
    """覆盖更新：只返回 active 状态（未撤销）的 — 给派单 / 任务 / 评估过滤用"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT 客户编码 FROM closed_provider WHERE 撤销时间 IS NULL"
        params = []
        if city:
            sql += " AND 城市 = ?"
            params.append(city)
        df = pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return set()
    return set(df['客户编码'].astype(str))


# ══════════════════════════════════════════════
# ⚔️ 竞品 Top 服务商（重点开拓目标）
# ══════════════════════════════════════════════

def import_competitor_top(records: list[dict], source_file: str = '') -> dict:
    """批量导入竞品 Top 服务商。同客户编码已存在则覆盖。

    records 中 在售大华 字段会自动从 客户经营品牌 派生（含"大华" → 1）

    Returns: {'inserted': N, 'updated': N, 'total': N}
    """
    _ensure_table()
    if not records:
        return {'inserted': 0, 'updated': 0, 'total': 0}
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat(timespec='seconds')
        n_ins = n_upd = 0
        for r in records:
            code = r.get('客户编码')
            if not code:
                continue
            brand = r.get('客户经营品牌') or ''
            sells_dahua = 1 if ('大华' in str(brand)) else 0
            existed = cur.execute(
                "SELECT 1 FROM competitor_top_provider WHERE 客户编码 = ?", (str(code),)
            ).fetchone()
            cur.execute("""
                INSERT OR REPLACE INTO competitor_top_provider
                  (客户编码, 客户名称, 省份, 城市, 区县,
                   责任人角色, 责任人姓名, 老板姓名, 老板手机号,
                   客户经营品牌, 在售大华, 竞品体量_万,
                   拜访内容, 转化策略, 备注,
                   导入时间, 导入文件)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(code), r.get('客户名称'),
                r.get('省份'), r.get('城市'), r.get('区县'),
                r.get('责任人角色'), r.get('责任人姓名'),
                r.get('老板姓名'), r.get('老板手机号'),
                brand, sells_dahua, r.get('竞品体量_万'),
                r.get('拜访内容'), r.get('转化策略'), r.get('备注'),
                now, source_file,
            ))
            if existed:
                n_upd += 1
            else:
                n_ins += 1
        conn.commit()
        return {'inserted': n_ins, 'updated': n_upd, 'total': n_ins + n_upd}
    finally:
        conn.close()


def list_competitor_top(*, city: str = None,
                        responsible: str = None,
                        sells_dahua: int = None,
                        limit: int = 10000) -> pd.DataFrame:
    """查询竞品 Top 服务商列表"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM competitor_top_provider WHERE 1=1"
        params = []
        if city:
            sql += " AND 城市 = ?"; params.append(city)
        if responsible:
            sql += " AND 责任人姓名 = ?"; params.append(responsible)
        if sells_dahua is not None:
            sql += " AND 在售大华 = ?"; params.append(int(sells_dahua))
        sql += " ORDER BY 竞品体量_万 DESC NULLS LAST, 城市, 区县 LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def competitor_top_codes(city: str = None) -> set:
    """返回所有「竞品 Top 服务商」客户编码集合 — 给重点开拓 / 跑动优先级 用"""
    df = list_competitor_top(city=city, limit=100000)
    if df.empty:
        return set()
    return set(df['客户编码'].astype(str))


def clear_competitor_top(city: str = None) -> int:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        if city:
            cur = conn.execute("DELETE FROM competitor_top_provider WHERE 城市 = ?", (city,))
        else:
            cur = conn.execute("DELETE FROM competitor_top_provider")
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 业务员月度跑动任务 CRUD (page 07)
# ══════════════════════════════════════════════

def bulk_insert_tasks(records: list[dict]) -> int:
    """批量插入任务。同（任务年月+业务员+客户编码）已存在则跳过"""
    _ensure_table()
    if not records:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat(timespec='seconds')
        n = 0
        for r in records:
            try:
                cur.execute("""
                    INSERT INTO salesperson_task
                      (任务年月, 地市, 业务员, 客户编码, 客户名称, 区县, 所属一级客户,
                       任务类型, 任务说明, 优先级, 分配时间, 备注)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r['任务年月'], r['地市'], r['业务员'], r['客户编码'],
                    r.get('客户名称'), r.get('区县'), r.get('所属一级客户'),
                    r['任务类型'], r.get('任务说明'), r.get('优先级'),
                    now, r.get('备注'),
                ))
                n += 1
            except sqlite3.IntegrityError:
                # 已存在（UNIQUE 冲突）→ 跳过
                continue
        conn.commit()
        return n
    finally:
        conn.close()


def list_tasks(*, 任务年月: str = None, 地市: str = None,
              业务员: str = None, 确认状态: str = None,
              完成状态: str = None, limit: int = 5000) -> pd.DataFrame:
    """查任务清单"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM salesperson_task WHERE 1=1"
        params = []
        if 任务年月:
            sql += " AND 任务年月 = ?"; params.append(任务年月)
        if 地市:
            sql += " AND 地市 = ?"; params.append(地市)
        if 业务员:
            sql += " AND 业务员 = ?"; params.append(业务员)
        if 确认状态:
            sql += " AND 确认状态 = ?"; params.append(确认状态)
        if 完成状态:
            sql += " AND 完成状态 = ?"; params.append(完成状态)
        sql += " ORDER BY 任务年月 DESC, 业务员, 优先级, id LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def update_task_status(task_id: int, *, 确认状态: str = None, 剔除原因: str = None,
                       完成状态: str = None, 实际拜访日: str = None,
                       实际激活日: str = None, 备注: str = None):
    """更新任务状态"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sets = []
        params = []
        if 确认状态 is not None:
            sets.append("确认状态 = ?"); params.append(确认状态)
            sets.append("确认时间 = ?"); params.append(datetime.now().isoformat(timespec='seconds'))
        if 剔除原因 is not None:
            sets.append("剔除原因 = ?"); params.append(剔除原因)
        if 完成状态 is not None:
            sets.append("完成状态 = ?"); params.append(完成状态)
        if 实际拜访日 is not None:
            sets.append("实际拜访日 = ?"); params.append(实际拜访日)
        if 实际激活日 is not None:
            sets.append("实际激活日 = ?"); params.append(实际激活日)
        if 备注 is not None:
            sets.append("备注 = ?"); params.append(备注)
        if not sets:
            return 0
        params.append(task_id)
        conn.execute(f"UPDATE salesperson_task SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id: int):
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM salesperson_task WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


def delete_tasks_by_month(任务年月: str, 地市: str = None, 业务员: str = None) -> int:
    """整月清空（管理员重新分配前用）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "DELETE FROM salesperson_task WHERE 任务年月 = ?"
        params = [任务年月]
        if 地市:
            sql += " AND 地市 = ?"; params.append(地市)
        if 业务员:
            sql += " AND 业务员 = ?"; params.append(业务员)
        cur = conn.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 应用用户 CRUD (page 00 用户管理 — Phase 2)
# ══════════════════════════════════════════════

def _hash_password(pwd: str) -> str:
    """简单哈希（生产建议用 bcrypt；此处用 sha256+salt 简化）"""
    import hashlib
    salt = 'so-data-analytics-2026'
    return hashlib.sha256((salt + pwd).encode('utf-8')).hexdigest()


def verify_password(username: str, password: str) -> bool:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        r = conn.execute(
            "SELECT password_hash, enabled FROM app_user WHERE username = ?",
            (username,),
        ).fetchone()
        if not r:
            return False
        if not r[1]:
            return False
        return _hash_password(password) == r[0]
    finally:
        conn.close()


def upsert_user(*, username: str, password: str = None, role: str = 'salesperson',
                full_name: str = None, enabled: bool = True, 备注: str = None,
                bound_salesperson: str = None):
    """新增或更新用户。password=None 时不改密码（仅改其他字段）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        exists = cur.execute("SELECT 1 FROM app_user WHERE username = ?", (username,)).fetchone()
        now = datetime.now().isoformat(timespec='seconds')
        if exists:
            sets = ['role = ?', 'full_name = ?', 'enabled = ?']
            params = [role, full_name, 1 if enabled else 0]
            if password:
                sets.append('password_hash = ?'); params.append(_hash_password(password))
            if 备注 is not None:
                sets.append('备注 = ?'); params.append(备注)
            # bound_salesperson 显式传 '' 也算更新（用于"解除绑定"）
            if bound_salesperson is not None:
                sets.append('bound_salesperson = ?'); params.append(bound_salesperson or None)
            params.append(username)
            cur.execute(f"UPDATE app_user SET {', '.join(sets)} WHERE username = ?", params)
        else:
            if not password:
                raise ValueError('新用户必须提供初始密码')
            cur.execute("""
                INSERT INTO app_user (username, password_hash, role, full_name,
                                       bound_salesperson, enabled, created_at, 备注)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (username, _hash_password(password), role, full_name,
                  bound_salesperson or None,
                  1 if enabled else 0, now, 备注))
        conn.commit()
    finally:
        conn.close()


def set_bound_salesperson(username: str, salesperson_name: str | None) -> bool:
    """显式绑定/解绑一个用户到业务员名"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE app_user SET bound_salesperson = ? WHERE username = ?",
            (salesperson_name or None, username),
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def get_bound_salesperson(username: str) -> str | None:
    """返回该用户绑定的业务员名（无绑定返回 None）"""
    _ensure_table()
    if not username:
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        r = conn.execute(
            "SELECT bound_salesperson FROM app_user WHERE username = ? AND enabled = 1",
            (username,),
        ).fetchone()
        if not r:
            return None
        return r[0] if r[0] else None
    finally:
        conn.close()


def list_data_salespeople() -> list[str]:
    """返回业务数据里的所有业务员名（从 salesperson_scope 表）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            rows = conn.execute(
                "SELECT DISTINCT 业务员 FROM salesperson_scope "
                "WHERE 业务员 IS NOT NULL AND 业务员 != '' ORDER BY 业务员"
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


def list_unbound_salesperson_users() -> pd.DataFrame:
    """列出 role=salesperson 但未绑定业务员的用户（admin 待处理列表）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql("""
            SELECT username, full_name, enabled, created_at, last_login
              FROM app_user
             WHERE role = 'salesperson'
               AND enabled = 1
               AND (bound_salesperson IS NULL OR bound_salesperson = '')
             ORDER BY username
        """, conn)
    finally:
        conn.close()


def update_last_login(username: str):
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE app_user SET last_login = ? WHERE username = ?",
                     (datetime.now().isoformat(timespec='seconds'), username))
        conn.commit()
    finally:
        conn.close()


def delete_user(username: str):
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM app_user_scope WHERE username = ?", (username,))
        conn.execute("DELETE FROM app_user WHERE username = ?", (username,))
        conn.commit()
    finally:
        conn.close()


def list_users() -> pd.DataFrame:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql(
            "SELECT username, role, full_name, bound_salesperson, "
            "enabled, created_at, last_login, 备注 "
            "FROM app_user ORDER BY username", conn,
        )
    finally:
        conn.close()


def set_user_scope(username: str, scopes: list[tuple]) -> int:
    """覆盖写：先删后插。scopes = [(scope_type, scope_value), ...]"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM app_user_scope WHERE username = ?", (username,))
        n = 0
        for t, v in scopes:
            if not v:
                continue
            try:
                cur.execute(
                    "INSERT INTO app_user_scope (username, scope_type, scope_value) VALUES (?, ?, ?)",
                    (username, t, v),
                )
                n += 1
            except sqlite3.IntegrityError:
                continue
        conn.commit()
        return n
    finally:
        conn.close()


def get_user_scope(username: str) -> dict[str, list[str]]:
    """返回该用户的可见范围 {scope_type: [scope_values]}"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            "SELECT scope_type, scope_value FROM app_user_scope WHERE username = ?",
            conn, params=(username,),
        )
        if df.empty:
            return {}
        return df.groupby('scope_type')['scope_value'].apply(list).to_dict()
    finally:
        conn.close()


def get_user_role(username: str) -> str:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        r = conn.execute(
            "SELECT role FROM app_user WHERE username = ? AND enabled = 1",
            (username,),
        ).fetchone()
        return r[0] if r else ''
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 智能搜索日志（page 00 搜索）
# ══════════════════════════════════════════════

def log_search(*, user: str, role: str, query: str, results: list, clicked_url: str = None) -> int:
    """记录一条搜索查询"""
    _ensure_table()
    import json as _json
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("""
            INSERT INTO ai_search_log (时间, 用户, 用户角色, 查询, 推荐结果, 点击的url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(timespec='seconds'),
            user, role, query,
            _json.dumps(results, ensure_ascii=False) if results else None,
            clicked_url,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_search_click(log_id: int, clicked_url: str):
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE ai_search_log SET 点击的url = ? WHERE id = ?",
                     (clicked_url, log_id))
        conn.commit()
    finally:
        conn.close()


def list_search_logs(limit: int = 200) -> pd.DataFrame:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql(
            "SELECT * FROM ai_search_log ORDER BY 时间 DESC LIMIT ?",
            conn, params=(limit,),
        )
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 🏷️ 服务商标签字典 CRUD (admin only)
# ══════════════════════════════════════════════
#
# 设计约束：
#   - 标签可以 rename / 启用-禁用 / 改图标-颜色-描述
#   - 不允许 DELETE（保留历史 assignment 引用）
#   - tag_key 锁定（PK） — admin 改名只改 tag_name


def list_tag_defs(*, only_enabled: bool = False) -> pd.DataFrame:
    """列出所有标签定义。返回 DataFrame：tag_key/tag_name/tag_icon/tag_color/tag_desc/source_type/enabled/sort_order/created_at/updated_at"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT * FROM provider_tag_def"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY sort_order, tag_key"
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


def get_tag_def(tag_key: str) -> dict | None:
    """取单个标签定义"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT tag_key, tag_name, tag_icon, tag_color, tag_desc,
                   source_type, enabled, sort_order, created_at, created_by,
                   updated_at, updated_by
              FROM provider_tag_def WHERE tag_key = ?
        """, (tag_key,)).fetchone()
        if not row:
            return None
        keys = ['tag_key', 'tag_name', 'tag_icon', 'tag_color', 'tag_desc',
                'source_type', 'enabled', 'sort_order', 'created_at', 'created_by',
                'updated_at', 'updated_by']
        return dict(zip(keys, row))
    finally:
        conn.close()


def tag_def_map() -> dict[str, dict]:
    """tag_key → {tag_name, tag_icon, tag_color, ...} 字典（给 UI 展示用）"""
    df = list_tag_defs(only_enabled=False)
    out = {}
    for _, r in df.iterrows():
        out[r['tag_key']] = {
            'tag_name': r['tag_name'],
            'tag_icon': r['tag_icon'] or '',
            'tag_color': r['tag_color'] or 'gray',
            'tag_desc': r['tag_desc'] or '',
            'source_type': r['source_type'],
            'enabled': bool(r['enabled']),
        }
    return out


def create_tag(*, tag_key: str, tag_name: str, tag_icon: str = '',
               tag_color: str = 'gray', tag_desc: str = '',
               source_type: str = 'manual', sort_order: int = 100,
               created_by: str = '') -> dict:
    """admin 创建新标签。tag_key 必须 ASCII 小写下划线，全局唯一。"""
    _ensure_table()
    import re
    if not re.match(r'^[a-z][a-z0-9_]*$', tag_key or ''):
        raise ValueError(f"tag_key 必须 ASCII 小写字母+数字+下划线: {tag_key!r}")
    if not tag_name:
        raise ValueError("tag_name 不能为空")
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        try:
            conn.execute("""
                INSERT INTO provider_tag_def
                  (tag_key, tag_name, tag_icon, tag_color, tag_desc,
                   source_type, enabled, sort_order, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (tag_key, tag_name, tag_icon, tag_color, tag_desc,
                  source_type, sort_order, now, created_by))
            conn.commit()
            return {'action': 'created', 'tag_key': tag_key}
        except sqlite3.IntegrityError as e:
            raise ValueError(f"tag_key/tag_name 已存在: {e}") from e
    finally:
        conn.close()


def rename_tag(*, tag_key: str, new_tag_name: str, updated_by: str = '') -> bool:
    """admin 改标签的中文名（tag_key 不动）"""
    _ensure_table()
    if not new_tag_name:
        raise ValueError("new_tag_name 不能为空")
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        try:
            cur = conn.execute("""
                UPDATE provider_tag_def
                   SET tag_name = ?, updated_at = ?, updated_by = ?
                 WHERE tag_key = ?
            """, (new_tag_name, now, updated_by, tag_key))
            ok = cur.rowcount > 0
            conn.commit()
            return ok
        except sqlite3.IntegrityError as e:
            raise ValueError(f"tag_name 已被占用: {e}") from e
    finally:
        conn.close()


def update_tag_def(*, tag_key: str,
                   tag_icon: str = None, tag_color: str = None,
                   tag_desc: str = None, sort_order: int = None,
                   enabled: int = None, updated_by: str = '') -> bool:
    """admin 改标签的外观字段（图标/颜色/描述/排序/启停）"""
    _ensure_table()
    sets, params = [], []
    if tag_icon is not None:
        sets.append("tag_icon = ?"); params.append(tag_icon)
    if tag_color is not None:
        sets.append("tag_color = ?"); params.append(tag_color)
    if tag_desc is not None:
        sets.append("tag_desc = ?"); params.append(tag_desc)
    if sort_order is not None:
        sets.append("sort_order = ?"); params.append(int(sort_order))
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if not sets:
        return False
    sets.append("updated_at = ?"); params.append(datetime.now().isoformat(timespec='seconds'))
    sets.append("updated_by = ?"); params.append(updated_by)
    params.append(tag_key)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            f"UPDATE provider_tag_def SET {', '.join(sets)} WHERE tag_key = ?",
            params,
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


# ══════════════════════════════════════════════
# 🏷️ 服务商×标签 分配 CRUD
# ══════════════════════════════════════════════

def assign_tag(
    *, 客户编码: str, tag_key: str,
    客户名称: str = '', 城市: str = '', 区县: str = '',
    标记来源: str = '', 标记人: str = '', 备注: str = '',
) -> dict:
    """给服务商挂标签。
    幂等：同 (客户编码, tag_key) 已 active → 更新；已撤销 → 重新激活
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        # 看是否已有任何分配（active or revoked）
        row = conn.execute("""
            SELECT id, 撤销时间 FROM provider_tag_assignment
             WHERE 客户编码 = ? AND tag_key = ?
             ORDER BY id DESC LIMIT 1
        """, (str(客户编码), tag_key)).fetchone()
        if row is None:
            # 新增
            conn.execute("""
                INSERT INTO provider_tag_assignment
                  (客户编码, tag_key, 客户名称, 城市, 区县,
                   标记来源, 标记人, 标记时间, 备注)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(客户编码), tag_key, 客户名称, 城市, 区县,
                  标记来源, 标记人, now, 备注))
            action = 'inserted'
        else:
            # 已存在 — 复活/更新
            conn.execute("""
                UPDATE provider_tag_assignment
                   SET 客户名称 = COALESCE(NULLIF(?, ''), 客户名称),
                       城市 = COALESCE(NULLIF(?, ''), 城市),
                       区县 = COALESCE(NULLIF(?, ''), 区县),
                       备注 = COALESCE(NULLIF(?, ''), 备注),
                       标记来源 = ?,
                       标记人 = ?,
                       标记时间 = ?,
                       撤销时间 = NULL,
                       撤销人 = NULL,
                       撤销原因 = NULL
                 WHERE id = ?
            """, (客户名称, 城市, 区县, 备注,
                  标记来源, 标记人, now, row[0]))
            action = 'revived' if row[1] else 'updated'
        # 打标事件流水（所有标签留全历史，永不覆盖）
        conn.execute("""
            INSERT INTO provider_tag_event
              (客户编码, tag_key, 事件类型, 客户名称, 城市, 区县,
               标记来源, 操作人, 事件时间, 事件年月, 备注)
            VALUES (?, ?, 'assign', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(客户编码), tag_key, 客户名称, 城市, 区县,
              标记来源, 标记人, now, now[:7], 备注))
        conn.commit()
        return {'action': action, '客户编码': str(客户编码), 'tag_key': tag_key}
    finally:
        conn.close()


def revoke_assignment(*, 客户编码: str, tag_key: str,
                     撤销人: str = '', 撤销原因: str = '') -> bool:
    """撤销服务商的某个标签（软删）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        cur = conn.execute("""
            UPDATE provider_tag_assignment
               SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = ?
             WHERE 客户编码 = ? AND tag_key = ? AND 撤销时间 IS NULL
        """, (now, 撤销人, 撤销原因, str(客户编码), tag_key))
        ok = cur.rowcount > 0
        if ok:
            # 撤销事件也进流水
            conn.execute("""
                INSERT INTO provider_tag_event
                  (客户编码, tag_key, 事件类型, 操作人, 事件时间, 事件年月, 备注)
                VALUES (?, ?, 'revoke', ?, ?, ?, ?)
            """, (str(客户编码), tag_key, 撤销人, now, now[:7], 撤销原因))
        conn.commit()
        return ok
    finally:
        conn.close()


def get_tag_events(客户编码: str = None, *, tag_key: str = None,
                   事件年月: str = None, 事件类型: str = None,
                   limit: int = 2000) -> list[dict]:
    """查打标事件流水（完整历史 —— 每一次 assign/revoke 的时间都在）。

    可按 客户编码 / tag_key / 事件年月(YYYY-MM) / 事件类型(assign|revoke) 过滤。
    按事件时间倒序返回。
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = ("SELECT 客户编码, tag_key, 事件类型, 客户名称, 城市, 区县, "
               "标记来源, 操作人, 事件时间, 事件年月, 备注 "
               "FROM provider_tag_event WHERE 1=1")
        params = []
        if 客户编码:
            sql += " AND 客户编码 = ?"; params.append(str(客户编码))
        if tag_key:
            sql += " AND tag_key = ?"; params.append(tag_key)
        if 事件年月:
            sql += " AND 事件年月 = ?"; params.append(事件年月)
        if 事件类型:
            sql += " AND 事件类型 = ?"; params.append(事件类型)
        sql += " ORDER BY 事件时间 DESC LIMIT ?"; params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        cols = ['客户编码', 'tag_key', '事件类型', '客户名称', '城市', '区县',
                '标记来源', '操作人', '事件时间', '事件年月', '备注']
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_provider_tags(客户编码: str) -> list[dict]:
    """查询某服务商当前的所有 active 标签（来自 provider_tags_v 视图）

    Returns: [{tag_key, tag_name, tag_icon, tag_color, tag_desc, source_type,
               标记来源, 标记人, 标记时间, 备注, origin}, ...]
    其中 origin ∈ {imported, heuristic, hybrid, manual}：
      - manual: 来自 provider_tag_assignment → 可单户撤销
      - heuristic: 实时算 → 只能改规则，不能单户撤销
      - imported: 来自外部表 → 改外部表才能去掉（点撤销会提示）
      - hybrid: closed_provider，可单户撤销
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT v.tag_key, v.标记来源, v.标记人, v.标记时间, v.备注, v.origin,
                   d.tag_name, d.tag_icon, d.tag_color, d.tag_desc, d.source_type
              FROM provider_tags_v v
              LEFT JOIN provider_tag_def d ON d.tag_key = v.tag_key
             WHERE v.客户编码 = ?
        """, (str(客户编码),)).fetchall()
        out = []
        for r in rows:
            out.append({
                'tag_key':     r[0],
                '标记来源':    r[1],
                '标记人':      r[2],
                '标记时间':    r[3],
                '备注':        r[4],
                'origin':      r[5],
                'tag_name':    r[6] or r[0],
                'tag_icon':    r[7] or '',
                'tag_color':   r[8] or 'gray',
                'tag_desc':    r[9] or '',
                'source_type': r[10] or 'manual',
            })
        # 按 sort_order 排序（用 tag_def_map）
        td = tag_def_map()
        out.sort(key=lambda x: (td.get(x['tag_key'], {}).get('sort_order', 999), x['tag_key']))
        return out
    finally:
        conn.close()


def get_provider_tags_batch(客户编码列表: list[str]) -> dict[str, list[dict]]:
    """批量取多个服务商的标签（同 get_provider_tags，但一次性查所有）

    Returns: {客户编码: [{tag_key, tag_name, ...}, ...], ...}
    """
    _ensure_table()
    if not 客户编码列表:
        return {}
    codes = [str(c) for c in 客户编码列表 if c]
    if not codes:
        return {}
    conn = sqlite3.connect(DB_PATH)
    try:
        # 分批 IN 查询（sqlite IN 列表理论上限 999）
        out: dict[str, list[dict]] = {c: [] for c in codes}
        td = tag_def_map()
        chunk = 500
        for i in range(0, len(codes), chunk):
            sub = codes[i:i + chunk]
            placeholders = ','.join('?' * len(sub))
            rows = conn.execute(f"""
                SELECT v.客户编码, v.tag_key, v.标记来源, v.标记人, v.标记时间, v.备注, v.origin,
                       d.tag_name, d.tag_icon, d.tag_color, d.source_type
                  FROM provider_tags_v v
                  LEFT JOIN provider_tag_def d ON d.tag_key = v.tag_key
                 WHERE v.客户编码 IN ({placeholders})
            """, sub).fetchall()
            for r in rows:
                out.setdefault(r[0], []).append({
                    'tag_key':     r[1],
                    '标记来源':    r[2],
                    '标记人':      r[3],
                    '标记时间':    r[4],
                    '备注':        r[5],
                    'origin':      r[6],
                    'tag_name':    r[7] or r[1],
                    'tag_icon':    r[8] or '',
                    'tag_color':   r[9] or 'gray',
                    'source_type': r[10] or 'manual',
                })
        # 排序
        for c, lst in out.items():
            lst.sort(key=lambda x: (td.get(x['tag_key'], {}).get('sort_order', 999), x['tag_key']))
        return out
    finally:
        conn.close()


def provider_tag_summary(*, 客户编码列表: list[str] = None,
                          city: str = None) -> pd.DataFrame:
    """各标签下的服务商数量统计 — 给「标签字典」管理页 / 看板用

    Returns: DataFrame[tag_key, tag_name, tag_icon, source_type, n_providers]
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        # 检查视图是否存在
        v_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='provider_tags_v'"
        ).fetchone()
        if not v_exists:
            _ensure_tag_view()
        sql = """
            SELECT d.tag_key, d.tag_name, d.tag_icon, d.tag_color,
                   d.source_type, COUNT(DISTINCT v.客户编码) AS n_providers
              FROM provider_tag_def d
              LEFT JOIN provider_tags_v v ON v.tag_key = d.tag_key
        """
        wheres, params = [], []
        if 客户编码列表:
            codes = [str(c) for c in 客户编码列表 if c]
            if codes:
                placeholders = ','.join('?' * len(codes))
                wheres.append(f"v.客户编码 IN ({placeholders})")
                params.extend(codes)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " GROUP BY d.tag_key, d.tag_name, d.tag_icon, d.tag_color, d.source_type"
        sql += " ORDER BY d.sort_order, d.tag_key"
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def providers_by_tag(tag_key: str, *, city: str = None, limit: int = 10000) -> pd.DataFrame:
    """列出某标签下的所有 active 服务商

    Returns: DataFrame[客户编码, 城市, 区县, 标记来源, 标记人, 标记时间, 备注, origin]
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        # 视图里没有 城市/区县，要 join provider_profile 拿
        sql = """
            SELECT v.客户编码,
                   COALESCE(pp.公司名称, pp.外部客户名称) AS 客户名称,
                   pp.地市 AS 城市, pp.区县 AS 区县,
                   v.标记来源, v.标记人, v.标记时间, v.备注, v.origin
              FROM provider_tags_v v
              LEFT JOIN provider_profile pp ON pp.客户编码 = v.客户编码
             WHERE v.tag_key = ?
        """
        params = [tag_key]
        if city:
            sql += " AND pp.地市 = ?"
            params.append(city)
        sql += " ORDER BY pp.地市, pp.区县, v.客户编码 LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def codes_by_tag(tag_key: str, *, city: str = None) -> set:
    """返回某标签下所有 active 服务商的客户编码集合（给过滤逻辑用）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = "SELECT DISTINCT 客户编码 FROM provider_tags_v WHERE tag_key = ?"
        params = [tag_key]
        if city:
            # 需要 join provider_profile 拿城市
            sql = """
                SELECT DISTINCT v.客户编码
                  FROM provider_tags_v v
                  LEFT JOIN provider_profile pp ON pp.客户编码 = v.客户编码
                 WHERE v.tag_key = ? AND pp.地市 = ?
            """
            params.append(city)
        df = pd.read_sql(sql, conn, params=params)
        return set(df['客户编码'].astype(str)) if not df.empty else set()
    finally:
        conn.close()


def rebuild_provider_tags_view() -> bool:
    """显式重建视图 — 上传完数据后调用，确保依赖表都齐"""
    _ensure_table()
    return _ensure_tag_view()


# ══════════════════════════════════════════════
# ☂️ 伞形组 CRUD
# ══════════════════════════════════════════════
#
# 一个客户同时只能属于一个 active 组（uq_umb_mem_active 唯一索引强制）
# 解散组：g.解散时间 = now ；同时把所有成员的 撤销时间 = now
# 移除单成员：m.撤销时间 = now ；组保留
#
# 与 provider_tag_assignment 关系：
#   - 加成员 → 不需要再写 provider_tag_assignment，视图 5a 自动把成员暴露为 umbrella 标签
#   - 移除成员 → 视图 5a 不再返回该成员，标签自动消失（如该客户还匹配 5b 启发式则还会有 umbrella）


def create_umbrella_group(
    *, 主账号_客户编码: str, 主账号_客户名称: str = '',
    城市: str = '', 区县: str = '',
    group_name: str = '', 备注: str = '', 创建人: str = '',
    成员_客户编码列表: list[str] | None = None,
    成员明细: list[dict] | None = None,
) -> dict:
    """创建一个新伞形组 + 一次性加入成员（含主账号自身）

    Args:
        主账号_客户编码: 必填，组主账号
        成员_客户编码列表: 其他成员（不含主账号）
        成员明细: 可选，如果给了带 客户名称/备注 的字典列表，优先用这个

    Returns: {'group_id': N, 'added_codes': [...], 'skipped_codes': [...]}
    """
    _ensure_table()
    if not 主账号_客户编码:
        raise ValueError("主账号_客户编码 必填")

    # 构建成员明细：主账号 + 其他成员
    members_to_add = [{
        '客户编码': str(主账号_客户编码),
        '客户名称': 主账号_客户名称 or '',
    }]
    if 成员明细:
        for m in 成员明细:
            code = str(m.get('客户编码', '') or '').strip()
            if not code or code == str(主账号_客户编码):
                continue
            members_to_add.append({
                '客户编码': code,
                '客户名称': m.get('客户名称', '') or '',
                '备注': m.get('备注', '') or '',
            })
    elif 成员_客户编码列表:
        for code in 成员_客户编码列表:
            code = str(code or '').strip()
            if not code or code == str(主账号_客户编码):
                continue
            members_to_add.append({'客户编码': code, '客户名称': ''})

    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        # 1. 建组
        cur = conn.execute("""
            INSERT INTO provider_umbrella_group
              (group_name, 主账号_客户编码, 主账号_客户名称, 城市, 区县,
               创建时间, 创建人, 备注)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_name or 主账号_客户名称 or 主账号_客户编码,
            str(主账号_客户编码), 主账号_客户名称, 城市, 区县,
            now, 创建人, 备注,
        ))
        group_id = cur.lastrowid

        # 2. 加成员（每个客户只能 active 在一个组里，如果已在他组要先移出）
        added, skipped = [], []
        for m in members_to_add:
            code = m['客户编码']
            # 该客户是否已在某 active 组里？
            existing = conn.execute("""
                SELECT m.id, m.group_id FROM provider_umbrella_member m
                  JOIN provider_umbrella_group g ON g.group_id = m.group_id
                 WHERE m.客户编码 = ? AND m.撤销时间 IS NULL AND g.解散时间 IS NULL
            """, (code,)).fetchone()
            if existing and existing[1] != group_id:
                # 先把旧成员关系软撤销
                conn.execute("""
                    UPDATE provider_umbrella_member
                       SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = '加入新组'
                     WHERE id = ?
                """, (now, 创建人, existing[0]))
            # 加入新组（如果之前没在任何组，或者已撤销）
            try:
                conn.execute("""
                    INSERT INTO provider_umbrella_member
                      (group_id, 客户编码, 客户名称, 加入时间, 加入人, 备注)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (group_id, code, m.get('客户名称', ''),
                      now, 创建人, m.get('备注', '')))
                added.append(code)
            except sqlite3.IntegrityError:
                # 唯一索引冲突 — 已经 active 在此组里
                skipped.append(code)
        conn.commit()
        return {'group_id': group_id, 'added_codes': added, 'skipped_codes': skipped}
    finally:
        conn.close()


def add_umbrella_members(
    *, group_id: int, codes: list[dict] | list[str],
    加入人: str = '',
) -> dict:
    """给已有组追加成员。codes 可以是 [{'客户编码','客户名称'}, ...] 或纯客户编码列表"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        # 检查组是否存在 + 未解散
        g = conn.execute("""
            SELECT 解散时间 FROM provider_umbrella_group WHERE group_id = ?
        """, (group_id,)).fetchone()
        if not g:
            raise ValueError(f"组不存在: group_id={group_id}")
        if g[0]:
            raise ValueError(f"组已解散: group_id={group_id}")

        added, skipped = [], []
        for c in codes:
            if isinstance(c, dict):
                code = str(c.get('客户编码', '') or '').strip()
                name = c.get('客户名称', '') or ''
                remark = c.get('备注', '') or ''
            else:
                code = str(c or '').strip()
                name = ''
                remark = ''
            if not code:
                continue
            # 是否已在他组 active？先撤销老的
            existing = conn.execute("""
                SELECT m.id, m.group_id FROM provider_umbrella_member m
                  JOIN provider_umbrella_group g ON g.group_id = m.group_id
                 WHERE m.客户编码 = ? AND m.撤销时间 IS NULL AND g.解散时间 IS NULL
            """, (code,)).fetchone()
            if existing and existing[1] != group_id:
                conn.execute("""
                    UPDATE provider_umbrella_member
                       SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = '加入新组'
                     WHERE id = ?
                """, (now, 加入人, existing[0]))
            elif existing and existing[1] == group_id:
                skipped.append(code)
                continue
            try:
                conn.execute("""
                    INSERT INTO provider_umbrella_member
                      (group_id, 客户编码, 客户名称, 加入时间, 加入人, 备注)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (group_id, code, name, now, 加入人, remark))
                added.append(code)
            except sqlite3.IntegrityError:
                skipped.append(code)
        conn.commit()
        return {'added_codes': added, 'skipped_codes': skipped}
    finally:
        conn.close()


def remove_umbrella_member(*, group_id: int, 客户编码: str,
                            撤销人: str = '', 撤销原因: str = '') -> bool:
    """从组里移除某个成员（软撤销）。组保留。"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        cur = conn.execute("""
            UPDATE provider_umbrella_member
               SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = ?
             WHERE group_id = ? AND 客户编码 = ? AND 撤销时间 IS NULL
        """, (now, 撤销人, 撤销原因, group_id, str(客户编码)))
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def dissolve_umbrella_group(*, group_id: int,
                              解散人: str = '', 解散原因: str = '') -> bool:
    """解散整个组：组 + 所有 active 成员一起软撤销"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now().isoformat(timespec='seconds')
        cur = conn.execute("""
            UPDATE provider_umbrella_group
               SET 解散时间 = ?, 解散人 = ?, 解散原因 = ?
             WHERE group_id = ? AND 解散时间 IS NULL
        """, (now, 解散人, 解散原因, group_id))
        ok = cur.rowcount > 0
        if ok:
            conn.execute("""
                UPDATE provider_umbrella_member
                   SET 撤销时间 = ?, 撤销人 = ?, 撤销原因 = '组解散'
                 WHERE group_id = ? AND 撤销时间 IS NULL
            """, (now, 解散人, group_id))
        conn.commit()
        return ok
    finally:
        conn.close()


def list_umbrella_groups(*, city: str = None, active_only: bool = True,
                           limit: int = 1000) -> pd.DataFrame:
    """列出所有伞形组 + 每组成员数 + 累计货值合计"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = """
            WITH mem_active AS (
                SELECT group_id, COUNT(*) AS n_members,
                       GROUP_CONCAT(客户编码, ',') AS member_codes
                  FROM provider_umbrella_member
                 WHERE 撤销时间 IS NULL
                 GROUP BY group_id
            ),
            amt AS (
                SELECT m.group_id, COALESCE(SUM(ir.产品现有分销价), 0) / 10000.0 AS 累计货值_万
                  FROM provider_umbrella_member m
                  LEFT JOIN install_redpack ir ON ir.上线客户编码 = m.客户编码
                 WHERE m.撤销时间 IS NULL
                 GROUP BY m.group_id
            )
            SELECT g.group_id, g.group_name,
                   g.主账号_客户编码, g.主账号_客户名称,
                   g.城市, g.区县,
                   COALESCE(ma.n_members, 0) AS 成员数,
                   COALESCE(a.累计货值_万, 0) AS 累计货值_万,
                   g.创建时间, g.创建人, g.备注,
                   g.解散时间, g.解散人,
                   ma.member_codes
              FROM provider_umbrella_group g
              LEFT JOIN mem_active ma ON ma.group_id = g.group_id
              LEFT JOIN amt a ON a.group_id = g.group_id
             WHERE 1=1
        """
        params = []
        if active_only:
            sql += " AND g.解散时间 IS NULL"
        if city:
            sql += " AND g.城市 = ?"
            params.append(city)
        sql += " ORDER BY 累计货值_万 DESC, 成员数 DESC, g.group_id DESC LIMIT ?"
        params.append(limit)
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def list_umbrella_group_members(group_id: int, *,
                                  include_revoked: bool = False) -> pd.DataFrame:
    """某组的成员明细（含客户名称 / 货值 / 红包数）"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = """
            SELECT m.id, m.group_id, m.客户编码,
                   COALESCE(m.客户名称, pp.公司名称, pp.外部客户名称) AS 客户名称,
                   pp.地市 AS 城市, pp.区县 AS 区县,
                   pp.老板姓名, pp.老板电话,
                   m.加入时间, m.加入人, m.备注,
                   m.撤销时间, m.撤销人, m.撤销原因,
                   COALESCE(rp.n_red, 0) AS 红包数,
                   COALESCE(rp.amt, 0) / 10000.0 AS 累计货值_万,
                   CASE WHEN m.客户编码 = g.主账号_客户编码 THEN 1 ELSE 0 END AS 是否主账号
              FROM provider_umbrella_member m
              JOIN provider_umbrella_group g ON g.group_id = m.group_id
              LEFT JOIN provider_profile pp ON pp.客户编码 = m.客户编码
              LEFT JOIN (
                  SELECT 上线客户编码 AS code, COUNT(*) AS n_red,
                         SUM(产品现有分销价) AS amt
                    FROM install_redpack
                   GROUP BY 上线客户编码
              ) rp ON rp.code = m.客户编码
             WHERE m.group_id = ?
        """
        params = [group_id]
        if not include_revoked:
            sql += " AND m.撤销时间 IS NULL"
        sql += " ORDER BY 是否主账号 DESC, 累计货值_万 DESC, m.客户编码"
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def get_umbrella_group_of(客户编码: str) -> dict | None:
    """查某客户当前属于哪个 active 组。返回组信息 + 成员列表，或 None"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT g.group_id, g.group_name,
                   g.主账号_客户编码, g.主账号_客户名称,
                   g.城市, g.区县, g.创建时间, g.创建人, g.备注
              FROM provider_umbrella_member m
              JOIN provider_umbrella_group g ON g.group_id = m.group_id
             WHERE m.客户编码 = ?
               AND m.撤销时间 IS NULL
               AND g.解散时间 IS NULL
             LIMIT 1
        """, (str(客户编码),)).fetchone()
        if not row:
            return None
        return {
            'group_id': row[0], 'group_name': row[1],
            '主账号_客户编码': row[2], '主账号_客户名称': row[3],
            '城市': row[4], '区县': row[5],
            '创建时间': row[6], '创建人': row[7], '备注': row[8],
        }
    finally:
        conn.close()


def suggest_umbrella_candidates(客户编码: str, *, limit: int = 30) -> pd.DataFrame:
    """基于「同老板姓名 + 同老板电话」推荐当前客户可能的伞形组同伴

    返回 DataFrame[客户编码, 客户名称, 城市, 区县, 老板姓名, 老板电话, 累计货值_万, 红包数,
                  is_in_other_group(已被其他组占)]
    """
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        # 先查这个客户的老板
        boss = conn.execute("""
            SELECT 老板姓名, 老板电话 FROM provider_profile WHERE 客户编码 = ?
        """, (str(客户编码),)).fetchone()
        if not boss or not boss[0] or not boss[1]:
            return pd.DataFrame()
        boss_name, boss_phone = boss
        sql = """
            SELECT pp.客户编码,
                   COALESCE(pp.公司名称, pp.外部客户名称) AS 客户名称,
                   pp.地市 AS 城市, pp.区县 AS 区县,
                   pp.老板姓名, pp.老板电话,
                   COALESCE(rp.n_red, 0) AS 红包数,
                   COALESCE(rp.amt, 0) / 10000.0 AS 累计货值_万,
                   (SELECT m.group_id FROM provider_umbrella_member m
                      JOIN provider_umbrella_group g ON g.group_id = m.group_id
                     WHERE m.客户编码 = pp.客户编码
                       AND m.撤销时间 IS NULL AND g.解散时间 IS NULL
                     LIMIT 1) AS in_group_id
              FROM provider_profile pp
              LEFT JOIN (
                  SELECT 上线客户编码 AS code, COUNT(*) AS n_red,
                         SUM(产品现有分销价) AS amt
                    FROM install_redpack
                   GROUP BY 上线客户编码
              ) rp ON rp.code = pp.客户编码
             WHERE pp.老板姓名 = ? AND pp.老板电话 = ?
               AND pp.客户编码 != ?
             ORDER BY 累计货值_万 DESC, pp.客户编码
             LIMIT ?
        """
        return pd.read_sql(sql, conn, params=(boss_name, boss_phone, str(客户编码), limit))
    finally:
        conn.close()


def bulk_create_umbrella_groups_from_heuristic(
    candidates: list[dict],
    *, 创建人: str = '', 备注_前缀: str = '启发式建组',
) -> dict:
    """批量从启发式候选一键建组。

    每个候选 dict 形如：
      {'老板姓名': str, '老板电话': str, '客户编码列表': 'code1,code2,...'}

    每组的主账号 = 累计货值最大的那个客户。所有客户加入新组并自动获得伞形标签。

    Returns: {
        'n_groups_created': N,
        'n_members_added': N,
        'group_ids': [...],
        'errors': [(老板姓名, error_msg), ...],
        'skipped_already_in_group': N,   # 候选客户里已经在其他活跃组里的总数
    }
    """
    _ensure_table()
    if not candidates:
        return {
            'n_groups_created': 0, 'n_members_added': 0,
            'group_ids': [], 'errors': [], 'skipped_already_in_group': 0,
        }

    conn = sqlite3.connect(DB_PATH)
    try:
        n_groups = 0
        n_members = 0
        n_skipped_in_group = 0
        gids: list[int] = []
        errors: list[tuple] = []

        for cand in candidates:
            boss_name = (cand.get('老板姓名') or '').strip()
            boss_phone = (cand.get('老板电话') or '').strip()
            codes_str = cand.get('客户编码列表') or ''
            codes = [c.strip() for c in codes_str.split(',') if c.strip()]
            if not codes:
                continue

            # 排除已在其他 active 组里的客户
            placeholders = ','.join('?' * len(codes))
            in_group_rows = conn.execute(f"""
                SELECT m.客户编码 FROM provider_umbrella_member m
                  JOIN provider_umbrella_group g ON g.group_id = m.group_id
                 WHERE m.撤销时间 IS NULL
                   AND g.解散时间 IS NULL
                   AND m.客户编码 IN ({placeholders})
            """, codes).fetchall()
            in_group_set = {r[0] for r in in_group_rows}
            n_skipped_in_group += len(in_group_set)
            usable_codes = [c for c in codes if c not in in_group_set]

            if len(usable_codes) < 2:
                # 不够 2 个 → 不建组（伞形语义最少 2 户）
                errors.append((boss_name, f'有效成员不足 2 个（剩 {len(usable_codes)} 个，其他都在他组里）'))
                continue

            # 查每个客户的累计货值 + 公司名 + 城市/区县 → 选主账号
            placeholders2 = ','.join('?' * len(usable_codes))
            amt_rows = conn.execute(f"""
                SELECT pp.客户编码,
                       COALESCE(pp.公司名称, pp.外部客户名称) AS 客户名称,
                       pp.地市, pp.区县,
                       COALESCE(rp.amt, 0) AS amt
                  FROM provider_profile pp
                  LEFT JOIN (
                      SELECT 上线客户编码 AS code, SUM(产品现有分销价) AS amt
                        FROM install_redpack
                       GROUP BY 上线客户编码
                  ) rp ON rp.code = pp.客户编码
                 WHERE pp.客户编码 IN ({placeholders2})
            """, usable_codes).fetchall()

            if not amt_rows:
                errors.append((boss_name, '在 provider_profile 找不到这些客户编码'))
                continue

            # 主账号 = 累计货值最大
            amt_rows.sort(key=lambda r: float(r[4] or 0), reverse=True)
            leader = amt_rows[0]
            leader_code = str(leader[0])
            leader_name = leader[1] or leader_code
            leader_city = leader[2] or ''
            leader_dist = leader[3] or ''

            # 构造成员明细
            members_payload = []
            for r in amt_rows[1:]:
                members_payload.append({
                    '客户编码': str(r[0]),
                    '客户名称': r[1] or '',
                })

            try:
                res = create_umbrella_group(
                    主账号_客户编码=leader_code,
                    主账号_客户名称=leader_name,
                    城市=leader_city, 区县=leader_dist,
                    group_name=f"{boss_name}（{leader_name}）",
                    创建人=创建人,
                    备注=(f"{备注_前缀} · 同老板={boss_name} / 电话={boss_phone}"
                           if (boss_name or boss_phone) else 备注_前缀),
                    成员明细=members_payload,
                )
                gids.append(res['group_id'])
                n_groups += 1
                n_members += len(res['added_codes'])
            except Exception as e:
                errors.append((boss_name, str(e)))
                continue

        return {
            'n_groups_created': n_groups,
            'n_members_added': n_members,
            'group_ids': gids,
            'errors': errors,
            'skipped_already_in_group': n_skipped_in_group,
        }
    finally:
        conn.close()


def umbrella_group_quick_stats() -> dict:
    """伞形组总览：活跃组数 / 总成员数 / 累计货值"""
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM provider_umbrella_group WHERE 解散时间 IS NULL
        """).fetchone()
        n_groups = int(row[0]) if row else 0
        row = conn.execute("""
            SELECT COUNT(*) FROM provider_umbrella_member m
              JOIN provider_umbrella_group g ON g.group_id = m.group_id
             WHERE m.撤销时间 IS NULL AND g.解散时间 IS NULL
        """).fetchone()
        n_members = int(row[0]) if row else 0
        row = conn.execute("""
            SELECT COALESCE(SUM(ir.产品现有分销价), 0) / 10000.0
              FROM provider_umbrella_member m
              JOIN provider_umbrella_group g ON g.group_id = m.group_id
              LEFT JOIN install_redpack ir ON ir.上线客户编码 = m.客户编码
             WHERE m.撤销时间 IS NULL AND g.解散时间 IS NULL
        """).fetchone()
        total_amt_w = float(row[0]) if row else 0.0
        return {
            'n_groups': n_groups,
            'n_members': n_members,
            '累计货值_万': round(total_amt_w, 2),
        }
    finally:
        conn.close()
