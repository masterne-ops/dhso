INSERT OR IGNORE INTO campaign (代号, 名称, 定位, 描述, 状态, 开始日期, 截止日期, 创建人, 创建时间, 更新时间) VALUES
('hikvision-share', '海康份额抢夺',
 '把约 280 家海康强势 / 竞品 TOP 服务商的大华份额抢回来',
 '## 战役定位

海康是公司最大竞争对手,且服务商签约海康比例高。本战役聚焦把海康强势客户的大华份额抢回来。

## 目标池(Phase 1 自动识别)

- competitor_top_provider 71 家(客户经营品牌含「海康」)
- provider_contract 是否竞品TOP=Y 210 家(安防体量≥20W)
- 去重后约 280 家

## 自动打标

| 标签 | 算法 | 当前 |
|---|---|---|
| 海康相关 | 在目标池内 | ✅ |
| 海康服务商 | 客户分类_规范 ∈ (安装商,夫妻门店,批发门店) | ✅ |
| 海康工程商 | 客户分类_规范 = 中小工程商 | ✅ |
| 海康一级 | 待提供名单 | 🚧 |
| 海康金牌 | 待提供名单 | 🚧 |
| 海康占比>50% | 大华占比 = 累计上线/年采购,大华<50% | ✅ 粗估 |

## KR

- KR1:目标池大华占比从 X% 提升到 Y%
- KR2:海康强势(大华<50%)客户拉回 M 家(大华≥30%)
- KR3:目标池年度大华上线 X → Y 万',
 '进行中', '2026-05-25', '2026-12-31', 'init', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO campaign_target
  (campaign_id, 客户编码, 客户名称, 城市, 区县, 客户所有者, 上级代理商, 状态, 备注, 更新时间)
SELECT
  (SELECT id FROM campaign WHERE 代号 = 'hikvision-share'),
  pc.客户编码, pc.客户名称, pc.客户城市, pc.客户区县,
  pc.客户所有者, pc.上级客户名称, '待执行',
  COALESCE('品牌:' || ct.客户经营品牌, '') ||
  CASE WHEN pc."是否竞品TOP服务商（安防体量≥20W）" = 'Y' THEN ' | 体量≥20W' ELSE '' END,
  datetime('now')
  FROM provider_contract pc
  LEFT JOIN competitor_top_provider ct ON ct.客户编码 = pc.客户编码
 WHERE pc.客户编码 IN (
   SELECT DISTINCT 客户编码 FROM competitor_top_provider
   UNION
   SELECT 客户编码 FROM provider_contract WHERE "是否竞品TOP服务商（安防体量≥20W）" = 'Y'
 );

SELECT '✓ 战役行数:' AS '', COUNT(*) FROM campaign WHERE 代号 = 'hikvision-share';
SELECT '✓ 目标客户:' AS '', COUNT(*) FROM campaign_target
 WHERE campaign_id = (SELECT id FROM campaign WHERE 代号 = 'hikvision-share');
SELECT '── 分类分布 ──' AS '';
SELECT pc.客户分类_规范, COUNT(*) FROM campaign_target ct
  JOIN provider_contract_v pc ON pc.客户编码 = ct.客户编码
 WHERE ct.campaign_id = (SELECT id FROM campaign WHERE 代号 = 'hikvision-share')
 GROUP BY 1 ORDER BY 2 DESC;
SELECT '── 城市分布 ──' AS '';
SELECT pc.客户城市, COUNT(*) FROM campaign_target ct
  JOIN provider_contract pc ON pc.客户编码 = ct.客户编码
 WHERE ct.campaign_id = (SELECT id FROM campaign WHERE 代号 = 'hikvision-share')
 GROUP BY 1 ORDER BY 2 DESC;
