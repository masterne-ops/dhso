-- 3 家流失 SMB 代理商修正(用户 2026-05-29 确认):25 年在做、26 年停
UPDATE dealer_purchase SET 下单客户类型 = '一级代理商'
 WHERE 下单客户 LIKE '%名硕建设工程%'
    OR 下单客户 LIKE '%歌呈智能%'
    OR (下单客户 LIKE '%微空间网络科技%' AND 下单客户 LIKE '%湖州%');

-- 校验:被改的 3 家
SELECT '── 改后这 3 家 ──' AS '';
SELECT 下单客户, 下单客户类型,
       ROUND(SUM(CASE WHEN 数据年份=2025 THEN 实销万 ELSE 0 END),1) AS y2025,
       ROUND(SUM(CASE WHEN 数据年份=2026 THEN 实销万 ELSE 0 END),1) AS y2026
  FROM dealer_purchase
 WHERE 下单客户 LIKE '%名硕建设工程%' OR 下单客户 LIKE '%歌呈智能%'
    OR (下单客户 LIKE '%微空间网络科技%' AND 下单客户 LIKE '%湖州%')
 GROUP BY 下单客户;

-- 最终 SMB 进货口径
SELECT '── 最终 SMB 一级代理商进货口径 ──' AS '';
SELECT 数据年份,
       COUNT(*) AS 行数,
       ROUND(SUM(实销万),1) AS 实销万,
       COUNT(DISTINCT 下单客户) AS 代理商数
  FROM dealer_purchase WHERE 下单客户类型 = '一级代理商'
 GROUP BY 数据年份;
