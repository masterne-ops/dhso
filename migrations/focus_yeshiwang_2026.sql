-- ════════════════════════════════════════════════════════════════
-- 夜视王 2026 SO 目标:地市级年度 + 全省月度节奏
-- 数据源:用户提供的图片(2026-05-20)
-- ════════════════════════════════════════════════════════════════

-- ───────────────── 1. focus_rhythm 表(月度节奏) ─────────────────
CREATE TABLE IF NOT EXISTS focus_rhythm (
    专项     TEXT NOT NULL,
    年度     INTEGER NOT NULL,
    月份     INTEGER NOT NULL,
    占比_pct REAL NOT NULL,
    全省目标 INTEGER,
    备注     TEXT,
    更新人   TEXT,
    更新时间 TEXT,
    PRIMARY KEY (专项, 年度, 月份)
);
CREATE INDEX IF NOT EXISTS idx_fr_focus_year ON focus_rhythm(专项, 年度);

-- ───────────────── 2. focus_target 入库(夜视王 2026 地市级) ─────────────────
DELETE FROM focus_target WHERE 专项 = '夜视王' AND 年度 = 2026;

INSERT INTO focus_target (专项, 年度, 地市, 目标台数, 备注, 更新人, 更新时间) VALUES
('夜视王', 2026, '浙江合计', 48000, '全省合计',         'init', '2026-05-20'),
('夜视王', 2026, '杭州市',    9600, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '湖州市',    2400, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '嘉兴市',    2400, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '金华市',    6600, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '丽水市',    2400, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '宁波市',    8400, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '衢州市',    2400, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '绍兴市',    3600, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '台州市',    4200, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '温州市',    4800, NULL,               'init', '2026-05-20'),
('夜视王', 2026, '舟山市',    1200, NULL,               'init', '2026-05-20');

-- ───────────────── 3. focus_rhythm 入库(夜视王 2026 月度节奏) ─────────────────
DELETE FROM focus_rhythm WHERE 专项 = '夜视王' AND 年度 = 2026;

INSERT INTO focus_rhythm (专项, 年度, 月份, 占比_pct, 全省目标, 更新人, 更新时间) VALUES
('夜视王', 2026,  1,  7.5,  3600, 'init', '2026-05-20'),
('夜视王', 2026,  2,  3.0,  1440, 'init', '2026-05-20'),
('夜视王', 2026,  3,  7.5,  3600, 'init', '2026-05-20'),
('夜视王', 2026,  4,  7.5,  3600, 'init', '2026-05-20'),
('夜视王', 2026,  5,  8.5,  4080, 'init', '2026-05-20'),
('夜视王', 2026,  6,  9.5,  4560, 'init', '2026-05-20'),
('夜视王', 2026,  7,  8.5,  4080, 'init', '2026-05-20'),
('夜视王', 2026,  8,  9.0,  4320, 'init', '2026-05-20'),
('夜视王', 2026,  9, 10.0,  4800, 'init', '2026-05-20'),
('夜视王', 2026, 10,  8.0,  3840, 'init', '2026-05-20'),
('夜视王', 2026, 11, 10.0,  4800, 'init', '2026-05-20'),
('夜视王', 2026, 12, 11.0,  5280, 'init', '2026-05-20');

-- ═════════════════════ 校验 ═════════════════════
SELECT '─── focus_target 行数 ───' AS '';
SELECT COUNT(*) FROM focus_target WHERE 专项 = '夜视王' AND 年度 = 2026;

SELECT '─── 地市加和 = 浙江合计 校验 ───' AS '';
WITH s AS (
  SELECT SUM(CASE WHEN 地市 != '浙江合计' THEN 目标台数 ELSE 0 END) AS 地市加和,
         SUM(CASE WHEN 地市  = '浙江合计' THEN 目标台数 ELSE 0 END) AS 全省合计
    FROM focus_target
   WHERE 专项 = '夜视王' AND 年度 = 2026
)
SELECT 地市加和, 全省合计,
       CASE WHEN 地市加和 = 全省合计 THEN '✓' ELSE '⚠️' END AS 校验 FROM s;

SELECT '─── focus_rhythm 节奏校验 ───' AS '';
SELECT ROUND(SUM(占比_pct), 2) AS 占比合计,
       SUM(全省目标) AS 全省目标合计,
       CASE WHEN ABS(SUM(占比_pct) - 100) < 0.01 THEN '✓' ELSE '⚠️' END AS 占比校验,
       CASE WHEN SUM(全省目标) = 48000 THEN '✓' ELSE '⚠️' END AS 数额校验
  FROM focus_rhythm WHERE 专项 = '夜视王' AND 年度 = 2026;

SELECT '─── 跟 focus_target_salesperson 交叉校验(地市目标 vs 业务员加总) ───' AS '';
WITH sp AS (
  SELECT 地市,
         SUM(CASE WHEN 是否地市总负责='Y' THEN 目标台数 ELSE 0 END) AS sp总责,
         SUM(CASE WHEN 是否地市总负责='N' THEN 目标台数 ELSE 0 END) AS sp业务员加总
    FROM focus_target_salesperson
   WHERE 专项 = '夜视王' AND 年度 = 2026
   GROUP BY 1
),
city AS (
  SELECT 地市, 目标台数 AS 地市目标 FROM focus_target
   WHERE 专项 = '夜视王' AND 年度 = 2026 AND 地市 != '浙江合计'
)
SELECT c.地市, c.地市目标, COALESCE(sp.sp总责, 0) AS sp总责, COALESCE(sp.sp业务员加总, 0) AS sp业务员加总,
       CASE
         WHEN sp.sp总责 > 0 AND c.地市目标 = sp.sp总责 THEN '✓ 总责'
         WHEN c.地市目标 = sp.sp业务员加总 THEN '✓ 业务员加总'
         ELSE '⚠️'
       END AS 校验
  FROM city c LEFT JOIN sp ON sp.地市 = c.地市
 ORDER BY c.地市;
