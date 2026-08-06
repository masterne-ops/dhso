"""代理商阵地沙盘(dealer_battlefield)— 只存战略字段,基础信息复用现有表

设计原则:
  - 服务商基础信息(名称 / 地市 / 区县 / 所属代理商)从 `provider_profile` +
    `install_redpack_v` 实时 JOIN 获取,**不重复存储**
  - `dealer_battlefield` 表只存**战略层独有字段**:归属/价值/趋势/竞品情报/争取目标
  - 主键 = 服务商编码(`install_redpack.上线客户编码` = `provider_profile.客户编码`)

数据来源链:
  ┌─ provider_profile  ──→ 名称 / 地市 / 区县 / 老板信息
  ┼─ install_redpack_v ──→ 所属代理商 / 上线货值 / 客户类型
  └─ dealer_battlefield──→ 战略标签(独立)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


TABLE = 'dealer_battlefield'

# ✨ 精简 schema:只存战略字段(基础信息 runtime JOIN)
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    服务商编码      TEXT PRIMARY KEY,

    -- 战略分类
    类型            TEXT,    -- 渠道(中性) / 阵地(战略价值高)
    归属            TEXT,    -- 我方 / 敌方 / 摇摆
    价值            TEXT,    -- A / B / C
    趋势            TEXT,    -- 增长 / 持平 / 萎缩

    -- 竞品情报
    主力采购品牌    TEXT,    -- 海康 / 宇视 / 天地伟业 / 大华 / 其他
    次要品牌        TEXT,
    全年销量_万     REAL,

    -- 攻坚目标
    大华占比_pct    REAL,    -- 钱包份额(0-100)
    争取目标_万     REAL,
    争取策略        TEXT,
    流失风险        TEXT,    -- 高 / 中 / 低

    -- 备注 + 审计
    备注            TEXT,
    更新人          TEXT,
    更新时间        TEXT
)
"""

INDEXES = [
    f'CREATE INDEX IF NOT EXISTS idx_bf_type ON {TABLE}(类型)',
    f'CREATE INDEX IF NOT EXISTS idx_bf_ownership ON {TABLE}(归属)',
    f'CREATE INDEX IF NOT EXISTS idx_bf_grade ON {TABLE}(价值)',
]

# 战略字段(不含基础冗余字段)
STRATEGY_COLS = [
    '服务商编码',
    '类型', '归属', '价值', '趋势',
    '主力采购品牌', '次要品牌', '全年销量_万',
    '大华占比_pct', '争取目标_万', '争取策略', '流失风险',
    '备注', '更新人', '更新时间',
]


def init_table():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(DDL)
    for ix in INDEXES:
        cur.execute(ix)
    conn.commit()
    conn.close()


def import_battlefield_excel(uploaded_file, mode: str = 'merge',
                              operator: str = None) -> dict:
    """导入阵地沙盘 Excel — 只取战略字段入库,基础信息忽略。

    Excel 可包含基础字段(服务商名称/地市/区县/所属代理商)供录入时认人,
    但**入库时这些字段会被自动 drop**。
    """
    df = pd.read_excel(uploaded_file)

    df.columns = (df.columns.astype(str).str.strip()
                   .str.replace(r'[\r\n\t]+', ' ', regex=True)
                   .str.replace(r'\s+', ' ', regex=True))

    if '服务商编码' not in df.columns:
        raise ValueError(f'Excel 缺主键列 [服务商编码]。实际列: {list(df.columns)}')

    # 主键清洗
    df['服务商编码'] = df['服务商编码'].astype(str).str.strip()
    df = df[(df['服务商编码'].notna()) & (df['服务商编码'] != '') & (df['服务商编码'] != 'nan')]

    dup = len(df) - df['服务商编码'].nunique()
    if dup > 0:
        df = df.drop_duplicates(subset='服务商编码', keep='last').reset_index(drop=True)

    # 数值列
    for col in ['全年销量_万', '大华占比_pct', '争取目标_万']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # ✨ 关键:只取战略字段,丢弃基础冗余字段(名称/地市/区县/所属代理商等)
    for c in STRATEGY_COLS:
        if c not in df.columns:
            df[c] = None
    df_strategic = df[STRATEGY_COLS].copy()
    df_strategic = df_strategic.where(pd.notnull(df_strategic), None)

    # 审计
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    df_strategic['更新人'] = df_strategic['更新人'].fillna(operator or 'system')
    df_strategic['更新时间'] = df_strategic['更新时间'].fillna(now)

    # 写库
    init_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if mode == 'replace':
        cur.execute(f'DELETE FROM {TABLE}')

    existing = set(
        r[0] for r in cur.execute(f'SELECT 服务商编码 FROM {TABLE}').fetchall()
    )

    placeholders = ', '.join('?' * len(STRATEGY_COLS))
    sql = (f'INSERT OR REPLACE INTO {TABLE} ({", ".join(f"`{c}`" for c in STRATEGY_COLS)}) '
           f'VALUES ({placeholders})')

    inserted = updated = 0
    for row in df_strategic[STRATEGY_COLS].values.tolist():
        cur.execute(sql, row)
        if str(row[0]) in existing:
            updated += 1
        else:
            inserted += 1
            existing.add(str(row[0]))

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]
    conn.close()

    return {
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'duplicates_in_file': dup,
        'ignored_basic_fields': [c for c in df.columns if c not in STRATEGY_COLS
                                 and c != '_id'],
    }


def batch_upsert(records: list[dict], operator: str = None) -> dict:
    """批量新增 / 更新(供 page 上 data_editor 保存用)。

    每条 record 必须含 '服务商编码',其他战略字段可选。
    返回:{updated, inserted}
    """
    if not records:
        return {'updated': 0, 'inserted': 0}

    init_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    existing = set(
        r[0] for r in cur.execute(f'SELECT 服务商编码 FROM {TABLE}').fetchall()
    )

    placeholders = ', '.join('?' * len(STRATEGY_COLS))
    sql = (f'INSERT OR REPLACE INTO {TABLE} ({", ".join(f"`{c}`" for c in STRATEGY_COLS)}) '
           f'VALUES ({placeholders})')

    inserted = updated = 0
    for rec in records:
        code = str(rec.get('服务商编码', '')).strip()
        if not code or code == 'nan':
            continue
        rec_clean = {k: v for k, v in rec.items() if k in STRATEGY_COLS}
        rec_clean['服务商编码'] = code
        rec_clean.setdefault('更新人', operator or 'manual')
        rec_clean.setdefault('更新时间', now)
        # 空字符串当 None
        for k, v in list(rec_clean.items()):
            if isinstance(v, str) and v.strip() == '':
                rec_clean[k] = None
        values = [rec_clean.get(c) for c in STRATEGY_COLS]
        cur.execute(sql, values)
        if code in existing:
            updated += 1
        else:
            inserted += 1
            existing.add(code)

    conn.commit()
    conn.close()
    return {'updated': updated, 'inserted': inserted}


def upsert_one(record: dict, operator: str = None) -> None:
    """单条新增 / 更新"""
    init_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    record.setdefault('更新人', operator or 'manual')
    record.setdefault('更新时间', now)
    values = [record.get(c) for c in STRATEGY_COLS]
    placeholders = ', '.join('?' * len(STRATEGY_COLS))
    cur.execute(
        f'INSERT OR REPLACE INTO {TABLE} ({", ".join(f"`{c}`" for c in STRATEGY_COLS)}) '
        f'VALUES ({placeholders})',
        values,
    )
    conn.commit()
    conn.close()


@st.cache_data(ttl=300, show_spinner='加载阵地沙盘…')
def load_battlefield_joined(dealer: str = None, city: str = None) -> pd.DataFrame:
    """读阵地沙盘 + JOIN 基础信息(provider_profile + install_redpack 最新所属代理商)。

    返回的 DataFrame 含:
      - 战略字段(归属/价值/...): 来自 dealer_battlefield
      - 基础字段(名称/地市/区县/所属代理商): runtime JOIN
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        )
        if cur.fetchone() is None:
            return pd.DataFrame()

        # JOIN provider_profile + install_redpack 最新所属代理商
        sql = f"""
            WITH latest_dealer AS (
                SELECT 上线客户编码 AS code,
                       所属一级客户 AS 所属代理商,
                       上线客户地市 AS 地市_rp,
                       上线客户区县 AS 区县_rp,
                       上线客户名称 AS 名称_rp,
                       上线客户渠道客户类型 AS 客户类型,
                       ROW_NUMBER() OVER (PARTITION BY 上线客户编码 ORDER BY 上线时间 DESC) AS rn
                  FROM install_redpack_v
            ),
            agg_so AS (
                SELECT 上线客户编码 AS code,
                       COUNT(*) AS 历史台数,
                       ROUND(SUM(产品现有分销价)/10000, 2) AS 历史货值_万
                  FROM install_redpack_v
                 GROUP BY 上线客户编码
            )
            SELECT bf.*,
                   COALESCE(pp.公司名称, ld.名称_rp) AS 服务商名称,
                   COALESCE(pp.地市, ld.地市_rp) AS 地市,
                   COALESCE(pp.区县, ld.区县_rp) AS 区县,
                   ld.所属代理商,
                   ld.客户类型,
                   ag.历史台数,
                   ag.历史货值_万
              FROM {TABLE} bf
              LEFT JOIN provider_profile pp ON pp.客户编码 = bf.服务商编码
              LEFT JOIN latest_dealer ld ON ld.code = bf.服务商编码 AND ld.rn = 1
              LEFT JOIN agg_so ag ON ag.code = bf.服务商编码
             WHERE 1=1
        """
        params: list = []
        if dealer:
            sql += ' AND ld.所属代理商 = ?'
            params.append(dealer)
        if city:
            sql += ' AND COALESCE(pp.地市, ld.地市_rp) = ?'
            params.append(city)

        df = pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()
    return df


# 兼容别名
load_battlefield = load_battlefield_joined


def generate_template_excel() -> bytes:
    """生成 Excel 录入模板。
    模板包含基础字段(名称/地市/区县/所属代理商)供用户认人,
    但 import 时这些字段不入库(只取战略字段)。
    """
    import io
    template = pd.DataFrame([{
        '服务商编码': '1@xxxxxxxxxxxxx(必填,关联现有 provider_profile)',
        '服务商名称(仅供认人,不入库)': '示例服务商有限公司',
        '所属代理商(仅供认人,不入库)': '杭州大载科技有限公司',
        '地市(仅供认人,不入库)': '杭州市',
        '区县(仅供认人,不入库)': '西湖区',
        '类型': '阵地',
        '归属': '摇摆',
        '价值': 'A',
        '趋势': '增长',
        '主力采购品牌': '海康',
        '次要品牌': '大华',
        '全年销量_万': 500,
        '大华占比_pct': 30,
        '争取目标_万': 150,
        '争取策略': '门头建设补贴+夜视王培训',
        '流失风险': '中',
        '备注': '',
    }])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        template.to_excel(w, sheet_name='阵地沙盘模板', index=False)
    return buf.getvalue()


# ──────────────────────────────────────────
# 候选服务商列表(供 page 选服务商时下拉用)
# ──────────────────────────────────────────


@st.cache_data(ttl=300)
def list_dealer_providers(city: str, dealer: str,
                          period_start: str = None,
                          period_end: str = None) -> pd.DataFrame:
    """列出某代理商旗下的服务商(用于 Page 在录入时下拉选服务商)"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        sql = """
            SELECT DISTINCT 上线客户编码 AS 服务商编码,
                   上线客户名称 AS 服务商名称,
                   上线客户地市 AS 地市,
                   上线客户区县 AS 区县
              FROM install_redpack_v
             WHERE 上线客户地市 = ? AND 所属一级客户 = ?
        """
        params = [city, dealer]
        if period_start and period_end:
            sql += ' AND 上线年月 BETWEEN ? AND ?'
            params += [period_start, period_end]
        df = pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()
    return df
