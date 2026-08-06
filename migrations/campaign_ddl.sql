-- ════════════════════════════════════════════════════════════════
-- 战役管理 通用 schema(Phase 1)
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS campaign (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    代号        TEXT NOT NULL UNIQUE,    -- 短代号 如 'wholesale100' / 'churn-recall'
    名称        TEXT NOT NULL,
    定位        TEXT,                    -- 一句话定位
    描述        TEXT,                    -- markdown 长描述
    状态        TEXT NOT NULL DEFAULT '进行中',  -- 进行中/已完成/暂停/取消
    Owner       TEXT,
    开始日期    TEXT,
    截止日期    TEXT,
    创建人      TEXT,
    创建时间    TEXT,
    更新时间    TEXT
);

CREATE TABLE IF NOT EXISTS campaign_target (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL,
    客户编码        TEXT NOT NULL,
    客户名称        TEXT,
    城市            TEXT,
    区县            TEXT,
    客户所有者      TEXT,                -- 大华业务员
    上级代理商      TEXT,                -- 上级一级代理商
    责任人          TEXT,                -- 战役内指定责任人(可以是大华业务员或代理商业务员)
    优先级          TEXT,                -- P0/P1/P2
    计划日期        TEXT,
    计划备注        TEXT,
    状态            TEXT DEFAULT '待执行', -- 待执行 / 进行中 / 已完成 / 暂缓 / 放弃
    实际完成日期    TEXT,
    备注            TEXT,
    更新人          TEXT,
    更新时间        TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaign(id) ON DELETE CASCADE,
    UNIQUE (campaign_id, 客户编码)
);
CREATE INDEX IF NOT EXISTS idx_ct_campaign ON campaign_target(campaign_id);
CREATE INDEX IF NOT EXISTS idx_ct_city ON campaign_target(城市);
CREATE INDEX IF NOT EXISTS idx_ct_status ON campaign_target(状态);

-- ── 创建第一个战役 ──
INSERT OR IGNORE INTO campaign (代号, 名称, 定位, 描述, 状态, 开始日期, 截止日期, 创建人, 创建时间, 更新时间) VALUES
('wholesale100', '批发类客户实现 100% 铺货',
 '让浙江 902 家批发类服务商全部完成铺货(当前 13.7% → 100%)',
 '## 战役定义

**目标**:1 个月内,浙江全省 902 家批发类服务商(批发门店 877 + 批发商 16 + 批发+工程 9)全部完成至少一次铺货。

**现状(战役开始时)**:
- 902 家批发类服务商
- 已铺货 124 家 (13.7%)
- 未铺货 778 家 (86.3%)
- 已铺货后上线率 93.5%(116/124)

**KR**:
- KR1:1 月内未铺货 778 家全部首次铺货(当前 0/778)
- KR2:铺货后上线率维持 ≥ 90%
- KR3:平均铺货 ≥ 5 台/家

**SOP**:
- "已铺货" = distribution_info.客户名称_下级 出现该客户
- "已上线" = 铺货后 install_redpack 出现该客户

**资源**:暂未投入(等 Owner 给)',
 '进行中', '2026-05-25', '2026-06-24', 'init', datetime('now'), datetime('now'));

-- ── 把 902 家批发类服务商作为战役目标入库 ──
INSERT OR IGNORE INTO campaign_target
  (campaign_id, 客户编码, 客户名称, 城市, 区县, 客户所有者, 上级代理商, 状态, 更新时间)
SELECT
  c.id,
  pc.客户编码,
  pc.客户名称,
  pc.客户城市,
  pc.客户区县,
  pc.客户所有者,
  pc.上级客户名称,
  CASE WHEN EXISTS (SELECT 1 FROM distribution_info di WHERE di.客户名称_下级 = pc.客户名称)
       THEN '已完成' ELSE '待执行' END,
  datetime('now')
  FROM provider_contract pc
  JOIN campaign c ON c.代号 = 'wholesale100'
 WHERE pc.客户分类 IN ('批发门店', '批发商', '批发+工程');

-- 校验
SELECT '✓ campaign 表行数:', COUNT(*) FROM campaign;
SELECT '✓ wholesale100 战役目标行数(应 902):', COUNT(*) FROM campaign_target WHERE campaign_id = (SELECT id FROM campaign WHERE 代号 = 'wholesale100');
SELECT '── 状态分布 ──';
SELECT 状态, COUNT(*) FROM campaign_target
 WHERE campaign_id = (SELECT id FROM campaign WHERE 代号 = 'wholesale100') GROUP BY 1;
