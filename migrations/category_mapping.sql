-- ════════════════════════════════════════════════════════════════
-- 客户分类规范化(8 类 → 4 类 MECE)
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS category_mapping (
    原分类     TEXT PRIMARY KEY,
    规范分类   TEXT NOT NULL,
    备注       TEXT,
    更新时间   TEXT
);

DELETE FROM category_mapping;
INSERT INTO category_mapping (原分类, 规范分类, 备注, 更新时间) VALUES
  ('安装商',     '安装商',     '主类',                   '2026-05-25'),
  ('个体安装商', '安装商',     '合并到安装商',           '2026-05-25'),
  ('中小工程商', '中小工程商', '主类',                   '2026-05-25'),
  ('工程商',     '中小工程商', '合并到中小工程商',       '2026-05-25'),
  ('夫妻门店',   '夫妻门店',   '主类',                   '2026-05-25'),
  ('批发门店',   '批发门店',   '主类',                   '2026-05-25'),
  ('批发商',     '批发门店',   '合并到批发门店',         '2026-05-25'),
  ('批发+工程',  '批发门店',   '以批发为主,合并到批发门店', '2026-05-25');

-- View:provider_contract 加规范分类字段
DROP VIEW IF EXISTS provider_contract_v;
CREATE VIEW provider_contract_v AS
SELECT pc.*,
       COALESCE(cm.规范分类,
                CASE WHEN pc.客户分类 IS NULL OR pc.客户分类 = '' THEN '未分类'
                     ELSE pc.客户分类 END) AS 客户分类_规范
  FROM provider_contract pc
  LEFT JOIN category_mapping cm ON cm.原分类 = pc.客户分类;

-- 校验
SELECT '── 映射表行数 ──' AS '';
SELECT COUNT(*) FROM category_mapping;

SELECT '── 规范分类分布(应:安装商 2659 / 中小工程商 2602 / 夫妻门店 2348 / 批发门店 902 / 未分类 828)──' AS '';
SELECT 客户分类_规范, COUNT(*) FROM provider_contract_v GROUP BY 1 ORDER BY 2 DESC;

SELECT '── 合计校验(应 9339)──' AS '';
SELECT COUNT(*) FROM provider_contract_v;

SELECT '── 11 地市 × 4 类 网格 ──' AS '';
SELECT 客户城市,
  SUM(CASE WHEN 客户分类_规范 = '安装商' THEN 1 ELSE 0 END) AS 安装商,
  SUM(CASE WHEN 客户分类_规范 = '中小工程商' THEN 1 ELSE 0 END) AS 中小工程商,
  SUM(CASE WHEN 客户分类_规范 = '夫妻门店' THEN 1 ELSE 0 END) AS 夫妻门店,
  SUM(CASE WHEN 客户分类_规范 = '批发门店' THEN 1 ELSE 0 END) AS 批发门店,
  SUM(CASE WHEN 客户分类_规范 = '未分类' THEN 1 ELSE 0 END) AS 未分类,
  COUNT(*) AS 合计
  FROM provider_contract_v
 WHERE 客户城市 IS NOT NULL AND 客户城市 != ''
 GROUP BY 1 ORDER BY 合计 DESC;
