"""DB 视图层 — V2/V3 共享口径的单一真相源

为什么有这个文件：
  V2 在 _loaders.py 里用 pandas 派生了一堆字段（KPI金额/签约状态/_打卡异常无效/服务商等级/...），
  V3 沙箱里 AI 直接读 SQLite，看不到这些 Python 派生。
  把派生逻辑提到 SQL 视图层，V2/V3 都从视图读 → 单一真相源 + 口径不分叉。

使用方式：
  - app.py:import_excel_to_db 末尾调 `ensure_views(conn)` 重建视图
  - _loaders.py 的 SELECT 从原表换成对应视图（_v 后缀）
  - V3 沙箱里 agent-claude.md 教 AI 优先用 _v 视图

视图清单：
  - product_flow_v       主表 + 派生（KPI金额/产品系列_有效/上线区县_全/流通天数/上线年月）
  - install_redpack_v    红包表 + 派生（是否签约/签约状态/安装区县_全/上线年月）
  - visit_record_v       拜访表 + 派生（_打卡异常无效/_真异常打卡/_打卡方/拜访年月/拜访时间_修正）
  - provider_tier_v      服务商等级（聚合：客户编码 → 上线累计 / 红包累计 / 服务商等级）
"""

from __future__ import annotations

import sqlite3


# 共享：去空白 helper（SQLite 没内置 trim 全角空格，要嵌套 REPLACE）
# 用于「是否签约」判定时归一化客户名称
_NORM = (
    "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE({col},''), "
    "' ',''), '　',''), CHAR(10),''), CHAR(13),''), CHAR(9),'')"
)
_NORM_OUT  = _NORM.format(col='出货客户名称')
_NORM_SIGN = _NORM.format(col='所属一级客户')


# 打卡异常分类 — 从 _loaders.py:load_visit_shared 复制（必须保持一致）
_INVALID_ANOMALY_TYPES = (
    '系统客户地址信息维护错误，后续更正',
    '系统客户地址信息维护错误,后续更正',
    '客户多地址办公',
    '系统客户地址信息维护错误',
)


# ══════════════════════════════════════════════
# 视图定义（每个视图一个 SQL 字符串）
# ══════════════════════════════════════════════

VIEW_PRODUCT_FLOW = """
CREATE VIEW product_flow_v AS
SELECT
    pf.*,
    -- 时间派生
    strftime('%Y-%m', pf."上线时间") AS "上线年月",
    date(pf."上线时间")               AS "上线日期",
    CAST(strftime('%Y', pf."上线时间") AS INTEGER) AS "上线年份",
    CAST(strftime('%m', pf."上线时间") AS INTEGER) AS "上线月份",
    -- 流通天数（>0 且 ≤730 才算合理；其他记 NULL）
    CASE
        WHEN pf."上线时间" IS NOT NULL AND pf."出库时间" IS NOT NULL
         AND CAST(julianday(pf."上线时间") - julianday(pf."出库时间") AS INTEGER) BETWEEN 0 AND 730
        THEN CAST(julianday(pf."上线时间") - julianday(pf."出库时间") AS INTEGER)
    END AS "流通天数",
    -- 产品系列三层 fallback：子系列-新 → 子系列 → 系列 → '无型号'
    -- 三层全空多为 NVR 下挂、取不到序列号的 IPC(序列号带$)，归"无型号"，台数/金额照算
    COALESCE(
        NULLIF(pf."产品子系列-新", ''),
        NULLIF(pf."产品子系列", ''),
        NULLIF(pf."产品系列", ''),
        '无型号'
    ) AS "产品系列_有效",
    -- 区县全名（带城市）
    COALESCE(NULLIF(pf."上线城市",''), '未知城市')
        || ' / ' ||
    COALESCE(NULLIF(pf."上线区县",''), '未知区县') AS "上线区县_全",
    -- KPI 金额（公司口径：优先红包表的产品现有分销价，缺失再 fallback 主表最新分销价）
    COALESCE(rp."产品现有分销价", pf."最新分销价") AS "KPI金额"
FROM product_flow pf
LEFT JOIN (
    SELECT "产品序列号",
           MAX("产品现有分销价") AS "产品现有分销价"
      FROM install_redpack
     WHERE "产品序列号" IS NOT NULL
     GROUP BY "产品序列号"
) rp ON rp."产品序列号" = pf."产品序列号";
"""


VIEW_INSTALL_REDPACK = f"""
CREATE VIEW install_redpack_v AS
SELECT
    rp.*,
    -- 时间派生
    strftime('%Y-%m', rp."上线时间") AS "上线年月",
    date(rp."上线时间")               AS "上线日期",
    CAST(strftime('%Y', rp."上线时间") AS INTEGER) AS "上线年份",
    CAST(strftime('%m', rp."上线时间") AS INTEGER) AS "上线月份",
    -- 区县全名（用安装侧）
    COALESCE(NULLIF(rp."安装城市",''), '未知城市')
        || ' / ' ||
    COALESCE(NULLIF(rp."安装区县",''), '未知区县') AS "安装区县_全",
    -- 是否签约（出货代理商 == 所属一级，且都非空）
    CASE
        WHEN {_NORM_SIGN} != ''
         AND {_NORM_OUT} = {_NORM_SIGN}
        THEN 1 ELSE 0
    END AS "是否签约",
    -- 签约状态三分类
    CASE
        WHEN {_NORM_SIGN} = '' THEN '无签约'
        WHEN {_NORM_OUT} = {_NORM_SIGN} THEN '签约采购'
        ELSE '跨渠道采购'
    END AS "签约状态"
FROM install_redpack rp;
"""


VIEW_VISIT_RECORD = f"""
CREATE VIEW visit_record_v AS
SELECT
    vr.*,
    -- 拜访时间修正：「活动创建时间」精度是分钟，比「拜访时间」（只到日 08:00:00 占位）准
    COALESCE(vr."活动创建时间", vr."拜访时间") AS "拜访时间_修正",
    -- 派生年月/年份/日期（基于修正后时间）
    strftime('%Y-%m', COALESCE(vr."活动创建时间", vr."拜访时间")) AS "拜访年月",
    CAST(strftime('%Y', COALESCE(vr."活动创建时间", vr."拜访时间")) AS INTEGER) AS "拜访年份",
    date(COALESCE(vr."活动创建时间", vr."拜访时间")) AS "拜访日期",
    -- 大华内部还是代理商业务员（公司字段为空 = 大华）
    CASE WHEN COALESCE(vr."打卡人所属公司",'') = '' THEN 1 ELSE 0 END AS "_是否大华",
    CASE WHEN COALESCE(vr."打卡人所属公司",'') = '' THEN '🏢 大华' ELSE '🏪 代理商' END AS "_打卡方",
    -- 打卡异常无效（已知数据问题，非业务员造假）
    CASE WHEN vr."打卡异常类型" IN {_INVALID_ANOMALY_TYPES}
         THEN 1 ELSE 0
    END AS "_打卡异常无效",
    -- 真异常打卡（距离 >1000m 且 不属于已知数据问题）
    CASE WHEN vr."距离偏离_米" > 1000
          AND COALESCE(vr."打卡异常类型",'') NOT IN {_INVALID_ANOMALY_TYPES}
         THEN 1 ELSE 0
    END AS "_真异常打卡"
FROM visit_record vr;
"""


# 服务商等级聚合视图（一行 = 一个客户编码）
# 注意：跟 _loaders.py:_kpi_tier_from_so 的阈值必须保持一致
VIEW_PROVIDER_TIER = """
CREATE VIEW provider_tier_v AS
SELECT
    "上线客户编码" AS "客户编码",
    MAX("上线客户名称") AS "上线客户名称",
    SUM(COALESCE("产品现有分销价", 0)) AS "上线累计",
    SUM(CASE WHEN COALESCE("中奖金额",0) > 0 THEN "中奖金额" ELSE 0 END) AS "红包累计",
    CASE
        WHEN SUM(COALESCE("产品现有分销价", 0)) >= 30000 THEN 'v4服务商'
        WHEN SUM(COALESCE("产品现有分销价", 0)) >= 10000 THEN 'v3服务商'
        WHEN SUM(COALESCE("产品现有分销价", 0)) >= 1000  THEN 'v2服务商'
        WHEN SUM(COALESCE("产品现有分销价", 0)) > 0      THEN '已激活'
        ELSE 'v0未激活'
    END AS "服务商等级"
FROM install_redpack
WHERE "上线客户编码" IS NOT NULL
  AND "上线客户编码" != ''
GROUP BY "上线客户编码";
"""


# 视图依赖顺序（先建被依赖的）
# product_flow_v 依赖 install_redpack 原表 → 必须先在 install_redpack 表建好后再建
# 其他视图无依赖
VIEW_REGISTRY = [
    # (name, depends_on_table_or_view, sql)
    ('install_redpack_v', 'install_redpack', VIEW_INSTALL_REDPACK),
    ('visit_record_v',    'visit_record',    VIEW_VISIT_RECORD),
    ('provider_tier_v',   'install_redpack', VIEW_PROVIDER_TIER),
    ('product_flow_v',    'product_flow',    VIEW_PRODUCT_FLOW),  # 依赖 install_redpack
]


def _table_exists(cur, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    )
    return cur.fetchone() is not None


def ensure_views(conn: sqlite3.Connection) -> dict:
    """重建所有视图（DROP + CREATE，幂等）。

    依赖的原表不存在就跳过对应视图（首次只导了部分数据时不报错）。

    Returns:
        {view_name: 'created' | 'skipped (xxx not exists)' | 'failed: ...'}
    """
    cur = conn.cursor()
    out = {}
    for view_name, dep, sql in VIEW_REGISTRY:
        if not _table_exists(cur, dep):
            out[view_name] = f'skipped ({dep} not exists)'
            continue
        try:
            cur.execute(f'DROP VIEW IF EXISTS {view_name}')
            cur.executescript(sql)
            out[view_name] = 'created'
        except sqlite3.Error as e:
            out[view_name] = f'failed: {e}'
    conn.commit()
    return out


def view_status(conn: sqlite3.Connection) -> dict:
    """查每个视图的存在状态 + 行数（debug 用）"""
    cur = conn.cursor()
    out = {}
    for view_name, _, _ in VIEW_REGISTRY:
        if _table_exists(cur, view_name):
            try:
                cur.execute(f'SELECT COUNT(*) FROM {view_name}')
                out[view_name] = {'exists': True, 'rows': cur.fetchone()[0]}
            except sqlite3.Error as e:
                out[view_name] = {'exists': True, 'rows': None, 'error': str(e)}
        else:
            out[view_name] = {'exists': False, 'rows': 0}
    return out


if __name__ == '__main__':
    # 命令行直跑：python _views.py [db_path]
    import sys
    from pathlib import Path

    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = str(Path(__file__).parent.parent / 'db' / 'product_flow.db')

    print(f'DB: {db_path}')
    conn = sqlite3.connect(db_path)
    try:
        result = ensure_views(conn)
        for name, status in result.items():
            print(f'  {name}: {status}')
        print()
        print('视图状态：')
        for name, info in view_status(conn).items():
            if info['exists']:
                print(f"  {name}: {info['rows']:,} 行")
            else:
                print(f"  {name}: ❌ 不存在")
    finally:
        conn.close()
