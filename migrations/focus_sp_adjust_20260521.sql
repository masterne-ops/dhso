-- ════════════════════════════════════════════════════════════════
-- focus_target_salesperson 调整(2026-05-21):
-- 1. 平泉伟离职 → 删 3 行;陈琪能 3 个专项升级 100%
-- 2. 徐建来跨 3 地市补登:丽水/衢州 总责行 6 行新增
-- 3. 祝嘉蔚暂不入库
-- ════════════════════════════════════════════════════════════════

-- ─── 1. 删除平泉伟 ─────────
DELETE FROM focus_target_salesperson
 WHERE 业务员 = '平泉伟' AND 年度 = 2026;

-- ─── 2. 陈琪能接管 100%(目标 = 原两人加总) ─────────
UPDATE focus_target_salesperson
   SET 占比_pct = 100, 目标台数 = 3600,
       备注 = '原平泉伟+陈琪能合并(2026-05-21 平泉伟离职)',
       更新人 = 'merge_pq', 更新时间 = '2026-05-21'
 WHERE 业务员 = '陈琪能' AND 专项 = '夜视王' AND 年度 = 2026;

UPDATE focus_target_salesperson
   SET 占比_pct = 100, 目标台数 = 2300,
       备注 = '原平泉伟+陈琪能合并(2026-05-21 平泉伟离职)',
       更新人 = 'merge_pq', 更新时间 = '2026-05-21'
 WHERE 业务员 = '陈琪能' AND 专项 = '场景化' AND 年度 = 2026;

UPDATE focus_target_salesperson
   SET 占比_pct = 100, 目标台数 = 5400,
       备注 = '原平泉伟+陈琪能合并(2026-05-21 平泉伟离职)',
       更新人 = 'merge_pq', 更新时间 = '2026-05-21'
 WHERE 业务员 = '陈琪能' AND 专项 = '无线' AND 年度 = 2026;

-- ─── 3. 徐建来跨地市补登(丽水 + 衢州 总责) ─────────
INSERT INTO focus_target_salesperson
  (专项, 地市, 业务员, 年度, 是否地市总负责, 占比_pct, 目标台数, 备注, 更新人, 更新时间) VALUES
('夜视王', '丽水市', '徐建来', 2026, 'Y', NULL, 2400, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21'),
('场景化', '丽水市', '徐建来', 2026, 'Y', NULL, 1000, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21'),
('无线',   '丽水市', '徐建来', 2026, 'Y', NULL, 4800, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21'),
('夜视王', '衢州市', '徐建来', 2026, 'Y', NULL, 2400, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21'),
('场景化', '衢州市', '徐建来', 2026, 'Y', NULL, 1000, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21'),
('无线',   '衢州市', '徐建来', 2026, 'Y', NULL, 4800, '跨地市主管(金华/丽水/衢州)', 'init', '2026-05-21');

-- ═════════════════════ 校验 ═════════════════════
SELECT '─── 总行数(原 75 - 3 + 6 = 78) ───' AS '';
SELECT COUNT(*) FROM focus_target_salesperson WHERE 年度 = 2026;

SELECT '─── 绍兴市(应只剩陈琪能 3 行 100%) ───' AS '';
SELECT 专项, 业务员, 是否地市总负责, 占比_pct, 目标台数
  FROM focus_target_salesperson
 WHERE 地市 = '绍兴市' AND 年度 = 2026
 ORDER BY 专项, 业务员;

SELECT '─── 徐建来跨地市(应 9 行 = 金华 3 + 丽水 3 + 衢州 3) ───' AS '';
SELECT 专项, 地市, 是否地市总负责, 目标台数, 备注
  FROM focus_target_salesperson
 WHERE 业务员 = '徐建来' AND 年度 = 2026
 ORDER BY 专项, 地市;

SELECT '─── 跨表校验:focus_target 地市目标 vs sp 业务员加总/总责 ───' AS '';
WITH sp AS (
  SELECT 专项, 地市,
         SUM(CASE WHEN 是否地市总负责='Y' THEN 目标台数 ELSE 0 END) AS sp总责,
         SUM(CASE WHEN 是否地市总负责='N' THEN 目标台数 ELSE 0 END) AS sp业务员加总
    FROM focus_target_salesperson WHERE 年度 = 2026
   GROUP BY 1, 2
)
SELECT t.专项, t.地市, t.目标台数, sp.sp总责, sp.sp业务员加总,
       CASE
         WHEN sp.sp总责 > 0 AND t.目标台数 = sp.sp总责 THEN '✓ 总责'
         WHEN t.目标台数 = sp.sp业务员加总 THEN '✓ 加总'
         ELSE '⚠️'
       END AS 校验
  FROM focus_target t
  LEFT JOIN sp ON sp.专项 = t.专项 AND sp.地市 = t.地市
 WHERE t.年度 = 2026 AND t.地市 != '浙江合计'
 ORDER BY t.专项, t.地市;
