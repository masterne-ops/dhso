"""门头建设(市场推广费用 服务商维度)数据导入 + 共享 loader

源表:Excel `市场推广费用_服务商维度_YYYY.xlsx`(单行表头,10 列)
目标表:provider_storefront_invest

主键:(投入客户编码, 投入年份)

含义:某服务商在某年度收到的「门头建设」补贴,以及该年度通过其安装红包带来的回款。
ROI = 当年上线金额 / 投入金额
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


TABLE = 'provider_storefront_invest'

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    投入客户编码      TEXT,
    投入年份          INTEGER,
    军团              TEXT,
    省份              TEXT,
    地市              TEXT,
    投入客户名称      TEXT,
    所属一级名称      TEXT,
    投入金额_元       REAL,
    是否激活          TEXT,
    当年上线金额_元   REAL,
    PRIMARY KEY (投入客户编码, 投入年份)
)
"""

INDEXES = [
    f'CREATE INDEX IF NOT EXISTS idx_psi_city ON {TABLE}(地市)',
    f'CREATE INDEX IF NOT EXISTS idx_psi_year ON {TABLE}(投入年份)',
    f'CREATE INDEX IF NOT EXISTS idx_psi_agent ON {TABLE}(所属一级名称)',
    f'CREATE INDEX IF NOT EXISTS idx_psi_active ON {TABLE}(是否激活)',
]


def _infer_year_from_name(name: str) -> int | None:
    """从文件名里推断年份,如 `市场推广费用_服务商维度_2025.xlsx` → 2025"""
    if not name:
        return None
    m = re.search(r'(20\d{2})', str(name))
    return int(m.group(1)) if m else None


def import_storefront_excel(uploaded_file, mode: str = 'merge',
                            invest_year: int | None = None) -> dict:
    """导入门头建设 Excel 到 provider_storefront_invest 表.

    Args:
        uploaded_file: streamlit file_uploader 对象 或 路径(.xlsx)
        mode: 'merge'(默认,PK 重合则覆盖) / 'replace'(先清表再导)
        invest_year: 投入年份,默认从文件名里推断,推不出来则报错

    Returns:
        {inserted, updated, total, year}
    """
    # 推断年份
    if invest_year is None:
        fname = getattr(uploaded_file, 'name', None) or str(uploaded_file)
        invest_year = _infer_year_from_name(fname)
    if not invest_year:
        raise ValueError('无法从文件名推断 投入年份,请显式传 invest_year=YYYY')

    df = pd.read_excel(uploaded_file)

    # 列名规范化(半角/全角括号兼容)
    rename_map = {
        '投入金额(元)':           '投入金额_元',
        '投入金额(元)':           '投入金额_元',
        '当年安装红包上线金额(元)': '当年上线金额_元',
        '当年安装红包上线金额(元)': '当年上线金额_元',
    }
    df = df.rename(columns=rename_map)

    # 丢掉 'Unnamed: 0' 类无名列
    drop_cols = [c for c in df.columns if str(c).startswith('Unnamed:')]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    # 必备列检查
    required = ['投入客户编码', '投入金额_元']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'缺少必备列: {missing}\n实际列: {list(df.columns)}')

    # 主键清洗
    df['投入客户编码'] = df['投入客户编码'].astype(str).str.strip()
    df = df[(df['投入客户编码'].notna()) & (df['投入客户编码'] != '') & (df['投入客户编码'] != 'nan')]

    # 数值列 → numeric
    for col in ['投入金额_元', '当年上线金额_元']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.where(pd.notnull(df), None)

    # 文件内主键去重(同 客户编码 多行 → 金额汇总)
    if df['投入客户编码'].duplicated().any():
        # 聚合: 投入金额求和,上线金额求和,其他字段取第一条
        agg_dict = {}
        for col in df.columns:
            if col == '投入客户编码':
                continue
            if col in ('投入金额_元', '当年上线金额_元'):
                agg_dict[col] = 'sum'
            else:
                agg_dict[col] = 'first'
        df = df.groupby('投入客户编码', as_index=False).agg(agg_dict)
        df = df.where(pd.notnull(df), None)

    # 加 投入年份
    df['投入年份'] = invest_year

    # ── 写库 ──
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(DDL)
    for ix in INDEXES:
        cur.execute(ix)

    if mode == 'replace':
        cur.execute(f'DELETE FROM {TABLE}')

    # 拿现有 PK (当前年份)
    existing = set(
        r[0]
        for r in cur.execute(
            f'SELECT 投入客户编码 FROM {TABLE} WHERE 投入年份 = ?', (invest_year,)
        ).fetchall()
    )

    cols_in_table = [
        '投入客户编码', '投入年份', '军团', '省份', '地市',
        '投入客户名称', '所属一级名称',
        '投入金额_元', '是否激活', '当年上线金额_元',
    ]
    for c in cols_in_table:
        if c not in df.columns:
            df[c] = None

    placeholders = ', '.join('?' * len(cols_in_table))
    sql = f'INSERT OR REPLACE INTO {TABLE} ({", ".join(f"`{c}`" for c in cols_in_table)}) VALUES ({placeholders})'

    inserted = updated = 0
    for row in df[cols_in_table].values.tolist():
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
        'year': invest_year,
    }


# ──────────────────────────────────────────
# 共享 loader(供 page 用)
# ──────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner='加载门头建设投入数据…')
def load_storefront_invest() -> pd.DataFrame:
    """读 provider_storefront_invest,加 ROI 派生列"""
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
        df = pd.read_sql(f'SELECT * FROM {TABLE}', conn)
    finally:
        conn.close()

    if df.empty:
        return df

    df['投入金额_元'] = pd.to_numeric(df['投入金额_元'], errors='coerce').fillna(0)
    df['当年上线金额_元'] = pd.to_numeric(df['当年上线金额_元'], errors='coerce').fillna(0)
    # ROI = 上线 / 投入(投入为 0 时 None)
    df['ROI'] = df.apply(
        lambda r: (r['当年上线金额_元'] / r['投入金额_元']) if r['投入金额_元'] > 0 else None,
        axis=1,
    )
    # 是否激活: 'Y' → True
    df['已激活'] = df['是否激活'].astype(str).str.upper().eq('Y')
    return df
