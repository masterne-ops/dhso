-- ════════════════════════════════════════════════════════════════
-- 代理商业务备注表 — 存业务规则/上下文,影响后续分析
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dealer_business_note (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    代理商客户编码 TEXT NOT NULL,
    代理商名称   TEXT NOT NULL,
    备注类型     TEXT NOT NULL,    -- SI口径异常 / 挂牌客户 / 业务员归属 / 战略调整 / 其他
    备注内容     TEXT NOT NULL,
    影响         TEXT,             -- 这条备注影响哪些分析
    生效日期     TEXT,
    创建人       TEXT,
    创建时间     TEXT,
    UNIQUE(代理商客户编码, 备注类型)
);
CREATE INDEX IF NOT EXISTS idx_dbn_code ON dealer_business_note(代理商客户编码);
CREATE INDEX IF NOT EXISTS idx_dbn_type ON dealer_business_note(备注类型);

-- 入库 3 条业务规则
INSERT OR REPLACE INTO dealer_business_note
  (代理商客户编码, 代理商名称, 备注类型, 备注内容, 影响, 生效日期, 创建人, 创建时间) VALUES

-- 鼎博:SI 双线组成,业务员双归属
('1-145PBP', '杭州鼎博安防设备有限公司', '业务员归属',
 '线下业务员=章旱雨 / 电商业务员=姜雨薇',
 'page 18/19 全景 + 业务员维度统计 不能简单用单一业务员归属',
 '2026-05-26', 'jin', datetime('now')),
('1-145PBP', '杭州鼎博安防设备有限公司', 'SI口径异常',
 'SI 由电商 + 线下两部分组成,无法从总数判断单部分完成度',
 '鼎博 SI 完成率 145% 不能作为线下业绩参考;6 月分析 + 攻坚方案不用鼎博 SI 作为依据',
 '2026-05-26', 'jin', datetime('now')),

-- 图创:挂牌客户
('1-2RX04Z', '杭州图创科技有限公司', '挂牌客户',
 '签约但不开展业务,签约金额 100 万 / 累计任务 36 万 均为挂牌虚账',
 '6 月分析忽略;不要算到「零达成预警」;不要算到杭州亏空',
 '2026-05-26', 'jin', datetime('now')),

-- 睿业:挂牌客户
('1@144780449', '杭州睿业电子科技有限公司', '挂牌客户',
 '签约但不开展业务,签约金额 100 万 / 累计任务 36 万 均为挂牌虚账',
 '6 月分析忽略;不要算到「零达成预警」;不要算到杭州亏空',
 '2026-05-26', 'jin', datetime('now'));

-- 校验
SELECT '✓ 入库完成,行数:' AS '', COUNT(*) FROM dealer_business_note;
SELECT 代理商名称, 备注类型, substr(备注内容, 1, 50) AS 备注摘要 FROM dealer_business_note ORDER BY 代理商名称, 备注类型;
