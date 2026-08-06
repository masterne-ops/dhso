-- ════════════════════════════════════════════════════════════════
-- Admin 待办工作流 — 数据结构(Phase 1 MVP)
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS admin_issue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    标题        TEXT NOT NULL,
    状态        TEXT NOT NULL DEFAULT '待调研',
                -- 待调研 / 调研中 / 思路就绪 / 任务定稿 / 执行中 / 待验证 / 已关闭 / 已归档
    优先级      TEXT NOT NULL DEFAULT 'P1',
                -- P0 / P1 / P2
    标签        TEXT,
                -- 口径修正 / 数据异常 / 新功能 / 优化 / 数据缺失 / 算法升级 / 其他(逗号分隔)
    背景描述    TEXT,    -- Admin 输入(markdown)
    数据调研    TEXT,    -- AI 输出或手动填(markdown)
    思路        TEXT,    -- Admin 输入或 AI 提案(markdown)
    关联上下文  TEXT,    -- 关联的 page / 段落(如 "page 32 段 20")
    创建人      TEXT NOT NULL,
    创建时间    TEXT NOT NULL,
    更新人      TEXT,
    更新时间    TEXT,
    关闭时间    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_status ON admin_issue(状态);
CREATE INDEX IF NOT EXISTS idx_ai_priority ON admin_issue(优先级);
CREATE INDEX IF NOT EXISTS idx_ai_created ON admin_issue(创建时间);

CREATE TABLE IF NOT EXISTS admin_issue_task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL,
    序号        INTEGER NOT NULL DEFAULT 0,
    描述        TEXT NOT NULL,
    责任方      TEXT NOT NULL DEFAULT 'AI',  -- AI / Admin / AI+Admin
    状态        TEXT NOT NULL DEFAULT '待办',
                -- 待办 / 进行中 / 完成 / 取消
    输出备注    TEXT,
    创建时间    TEXT NOT NULL,
    完成时间    TEXT,
    FOREIGN KEY (issue_id) REFERENCES admin_issue(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ait_issue ON admin_issue_task(issue_id);
CREATE INDEX IF NOT EXISTS idx_ait_status ON admin_issue_task(状态);

CREATE TABLE IF NOT EXISTS admin_issue_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL,
    时间        TEXT NOT NULL,
    类型        TEXT NOT NULL,    -- 创建 / 调研 / 思路 / 任务 / 执行 / 验证 / 状态变更 / 评论
    操作人      TEXT,             -- Admin / AI
    内容        TEXT NOT NULL,
    FOREIGN KEY (issue_id) REFERENCES admin_issue(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ail_issue ON admin_issue_log(issue_id);

-- 校验
SELECT '✓ admin_issue 表创建' AS '';
SELECT '✓ admin_issue_task 表创建' AS '';
SELECT '✓ admin_issue_log 表创建' AS '';
SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'admin_issue%' ORDER BY name;
