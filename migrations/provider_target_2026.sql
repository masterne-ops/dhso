-- ════════════════════════════════════════════════════════════════
-- 服务商签约目标 2026(地市级年度)— M2 P0 阻塞数据第 3 件
-- 数据源:用户提供的图片(2026-05-21),省区级目标分解
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS provider_target (
    年度                   INTEGER NOT NULL,
    地市                   TEXT    NOT NULL,
    服务商预测总家数        INTEGER,          -- 池子规模(潜在客户)
    服务商签约数_含个人     INTEGER,          -- 总签约目标(含个人户)
    安装红包V2_家数         INTEGER,          -- 红包成交额 1000元-1万
    安装红包V3_家数         INTEGER,          -- 红包成交额 1万-3万
    安装红包V4及以上_家数   INTEGER,          -- 红包成交额 3万及以上
    新签目标               INTEGER,          -- 签约目标里的新签部分
    备注                   TEXT,
    更新人                 TEXT,
    更新时间               TEXT,
    PRIMARY KEY (年度, 地市)
);
CREATE INDEX IF NOT EXISTS idx_pt_city ON provider_target(地市);

DELETE FROM provider_target WHERE 年度 = 2026;

INSERT INTO provider_target
  (年度, 地市, 服务商预测总家数, 服务商签约数_含个人, 安装红包V2_家数, 安装红包V3_家数, 安装红包V4及以上_家数, 新签目标, 备注, 更新人, 更新时间) VALUES
(2026, '浙江合计', 18771, 12000, 4670, 1680, 660, 3694, '全省合计', 'init', '2026-05-21'),
(2026, '杭州市',    3661,  2720, 1059,  381, 150,  837, NULL,       'init', '2026-05-21'),
(2026, '湖州市',     921,   950,  370,  133,  52,  292, NULL,       'init', '2026-05-21'),
(2026, '嘉兴市',    1401,  1050,  409,  147,  58,  323, NULL,       'init', '2026-05-21'),
(2026, '金华市',    1564,  1350,  525,  189,  74,  416, NULL,       'init', '2026-05-21'),
(2026, '丽水市',     812,   480,  187,   67,  26,  148, NULL,       'init', '2026-05-21'),
(2026, '宁波市',    3008,  1600,  623,  224,  88,  493, NULL,       'init', '2026-05-21'),
(2026, '衢州市',     778,   750,  292,  105,  41,  231, NULL,       'init', '2026-05-21'),
(2026, '绍兴市',    1687,   950,  370,  133,  52,  292, NULL,       'init', '2026-05-21'),
(2026, '台州市',    1918,   520,  202,   73,  29,  160, NULL,       'init', '2026-05-21'),
(2026, '温州市',    2643,  1440,  560,  202,  79,  443, NULL,       'init', '2026-05-21'),
(2026, '舟山市',     378,   190,   74,   27,  10,   58, NULL,       'init', '2026-05-21');

-- ═════════════════════ 校验 ═════════════════════
SELECT '─── 行数(应 12) ───' AS '';
SELECT COUNT(*) FROM provider_target WHERE 年度 = 2026;

SELECT '─── 11 地市加和 vs 浙江合计 ───' AS '';
WITH s AS (
  SELECT
    SUM(CASE WHEN 地市 != '浙江合计' THEN 服务商预测总家数      ELSE 0 END) AS 预测加和,
    SUM(CASE WHEN 地市 != '浙江合计' THEN 服务商签约数_含个人   ELSE 0 END) AS 签约加和,
    SUM(CASE WHEN 地市 != '浙江合计' THEN 安装红包V2_家数       ELSE 0 END) AS V2加和,
    SUM(CASE WHEN 地市 != '浙江合计' THEN 安装红包V3_家数       ELSE 0 END) AS V3加和,
    SUM(CASE WHEN 地市 != '浙江合计' THEN 安装红包V4及以上_家数 ELSE 0 END) AS V4加和,
    SUM(CASE WHEN 地市 != '浙江合计' THEN 新签目标             ELSE 0 END) AS 新签加和,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 服务商预测总家数      END) AS 预测全省,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 服务商签约数_含个人   END) AS 签约全省,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 安装红包V2_家数       END) AS V2全省,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 安装红包V3_家数       END) AS V3全省,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 安装红包V4及以上_家数 END) AS V4全省,
    MAX(CASE WHEN 地市  = '浙江合计' THEN 新签目标             END) AS 新签全省
  FROM provider_target WHERE 年度 = 2026
)
SELECT '预测总家数' AS 项, 预测加和 AS 加和, 预测全省 AS 全省, ABS(预测加和-预测全省) AS 差, CASE WHEN ABS(预测加和-预测全省)<=5 THEN '✓' ELSE '⚠️' END AS 校验 FROM s
UNION ALL SELECT '签约数', 签约加和, 签约全省, ABS(签约加和-签约全省), CASE WHEN ABS(签约加和-签约全省)<=5 THEN '✓' ELSE '⚠️' END FROM s
UNION ALL SELECT 'V2', V2加和, V2全省, ABS(V2加和-V2全省), CASE WHEN ABS(V2加和-V2全省)<=5 THEN '✓ (四舍五入)' ELSE '⚠️' END FROM s
UNION ALL SELECT 'V3', V3加和, V3全省, ABS(V3加和-V3全省), CASE WHEN ABS(V3加和-V3全省)<=5 THEN '✓ (四舍五入)' ELSE '⚠️' END FROM s
UNION ALL SELECT 'V4+', V4加和, V4全省, ABS(V4加和-V4全省), CASE WHEN ABS(V4加和-V4全省)<=5 THEN '✓ (四舍五入)' ELSE '⚠️' END FROM s
UNION ALL SELECT '新签目标', 新签加和, 新签全省, ABS(新签加和-新签全省), CASE WHEN ABS(新签加和-新签全省)<=5 THEN '✓ (四舍五入)' ELSE '⚠️' END FROM s;
