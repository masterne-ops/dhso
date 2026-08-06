"""城市区县基础数据 + 营销活动目标 导入

数据源:`【4.30】终端推广目标达成推进表-浙江.xlsx`(97 行 × 16 列,单行表头)

拆成 2 张表:

1. `district_base` — 城市区县基础画像(供全系统其他表关联使用)
   主键: 城市区县
   列: 省份/城市/区县/区县说明/GDP_亿元/人口_万人/安防体量/服务商体量/存量门头/存量专卖店/区县定义

2. `marketing_target` — 营销活动目标(目标 vs 实际)
   主键: (城市区县, 活动类型)
   列: 年度目标 / 已投入 / 数据截止日期
   现在有 2 类:'门头建设' / '圈子会议'
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


TABLE_BASE = 'district_base'
TABLE_TARGET = 'marketing_target'


DDL_BASE = f"""
CREATE TABLE IF NOT EXISTS {TABLE_BASE} (
    城市区县       TEXT PRIMARY KEY,
    省份           TEXT,
    城市           TEXT,
    区县           TEXT,
    区县说明       TEXT,
    GDP_亿元       REAL,
    人口_万人      INTEGER,
    安防体量       INTEGER,
    服务商体量     INTEGER,
    存量门头       INTEGER,
    存量专卖店     INTEGER,
    区县定义       TEXT
)
"""

DDL_TARGET = f"""
CREATE TABLE IF NOT EXISTS {TABLE_TARGET} (
    城市区县         TEXT,
    活动类型         TEXT,
    年度目标         INTEGER,
    已投入           INTEGER,
    数据截止日期     TEXT,
    PRIMARY KEY (城市区县, 活动类型),
    FOREIGN KEY (城市区县) REFERENCES district_base(城市区县)
)
"""

INDEXES = [
    f'CREATE INDEX IF NOT EXISTS idx_db_city ON {TABLE_BASE}(城市)',
    f'CREATE INDEX IF NOT EXISTS idx_db_district ON {TABLE_BASE}(区县)',
    f'CREATE INDEX IF NOT EXISTS idx_mt_city ON {TABLE_TARGET}(城市区县)',
    f'CREATE INDEX IF NOT EXISTS idx_mt_type ON {TABLE_TARGET}(活动类型)',
]


def import_district_target_excel(uploaded_file, mode: str = 'merge',
                                  cutoff_date: str = None) -> dict:
    """读 Excel → 拆成 district_base + marketing_target 两张表落库.

    Args:
        uploaded_file: file_uploader 对象 或 路径
        mode: 'merge' / 'replace'
        cutoff_date: 数据截止日(YYYY-MM-DD),默认推断为 Excel 文件名里的日期 或 今天

    Returns:
        {base_inserted, target_inserted, total_base, total_target}
    """
    # Excel 双 column 名(c14 / c16 重复叫"已投入数量")— pandas 自动加 .1
    df = pd.read_excel(uploaded_file)

    # 规范列名(去 end / 改重复列名 / 去括号)
    rename_map = {
        '门头目标end': '门头目标',
        '圈子会议目标end': '圈子会议目标',
        'GDP(亿元)': 'GDP_亿元',
        'GDP(亿元)': 'GDP_亿元',  # 全角括号 fallback
        '人口(万人)': '人口_万人',
        '人口(万人)': '人口_万人',
    }
    df = df.rename(columns=rename_map)

    # pandas 自动把 c14 / c16 都叫"已投入数量","已投入数量.1"
    # c14 是「门头已投入」(在「门头目标」后面)
    # c16 是「圈子会议已投入」(在「圈子会议目标」后面)
    cols = list(df.columns)
    if '已投入数量' in cols and '已投入数量.1' in cols:
        df = df.rename(columns={
            '已投入数量': '门头已投入',
            '已投入数量.1': '圈子会议已投入',
        })

    # 默认 cutoff
    if cutoff_date is None:
        # 试从文件名推断
        cutoff_date = '2026-04-30'  # 文件名【4.30】

    # 必备列检查
    required = ['城市区县', '省份', '城市', '区县']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'缺少必备列: {missing}\n实际列: {list(df.columns)}')

    # 主键清洗
    df['城市区县'] = df['城市区县'].astype(str).str.strip()
    df = df[df['城市区县'].notna() & (df['城市区县'] != '') & (df['城市区县'] != 'nan')]

    # 数值列 → numeric
    for col in ['GDP_亿元', '人口_万人', '安防体量', '服务商体量', '存量门头', '存量专卖店',
                '门头目标', '门头已投入', '圈子会议目标', '圈子会议已投入']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.where(pd.notnull(df), None)

    # ── 落库 ──
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(DDL_BASE)
    cur.execute(DDL_TARGET)
    for ix in INDEXES:
        cur.execute(ix)

    if mode == 'replace':
        cur.execute(f'DELETE FROM {TABLE_BASE}')
        cur.execute(f'DELETE FROM {TABLE_TARGET}')

    # 1. district_base
    base_cols = ['城市区县', '省份', '城市', '区县', '区县说明',
                 'GDP_亿元', '人口_万人', '安防体量', '服务商体量',
                 '存量门头', '存量专卖店', '区县定义']
    for c in base_cols:
        if c not in df.columns:
            df[c] = None

    placeholders = ', '.join('?' * len(base_cols))
    sql_base = f'INSERT OR REPLACE INTO {TABLE_BASE} ({", ".join(f"`{c}`" for c in base_cols)}) VALUES ({placeholders})'

    base_inserted = 0
    for row in df[base_cols].values.tolist():
        cur.execute(sql_base, row)
        base_inserted += 1

    # 2. marketing_target — 把 门头 + 圈子会议 各拆一行
    target_inserted = 0
    sql_target = f'''INSERT OR REPLACE INTO {TABLE_TARGET}
                     (城市区县, 活动类型, 年度目标, 已投入, 数据截止日期)
                     VALUES (?, ?, ?, ?, ?)'''

    for _, row in df.iterrows():
        city_district = row['城市区县']
        # 门头建设
        target_v = row.get('门头目标')
        actual_v = row.get('门头已投入')
        if target_v is not None or actual_v is not None:
            cur.execute(sql_target, (
                city_district, '门头建设',
                int(target_v) if target_v is not None and not pd.isna(target_v) else None,
                int(actual_v) if actual_v is not None and not pd.isna(actual_v) else 0,
                cutoff_date,
            ))
            target_inserted += 1
        # 圈子会议
        target_v = row.get('圈子会议目标')
        actual_v = row.get('圈子会议已投入')
        if target_v is not None or actual_v is not None:
            cur.execute(sql_target, (
                city_district, '圈子会议',
                int(target_v) if target_v is not None and not pd.isna(target_v) else None,
                int(actual_v) if actual_v is not None and not pd.isna(actual_v) else 0,
                cutoff_date,
            ))
            target_inserted += 1

    conn.commit()
    total_base = cur.execute(f'SELECT COUNT(*) FROM {TABLE_BASE}').fetchone()[0]
    total_target = cur.execute(f'SELECT COUNT(*) FROM {TABLE_TARGET}').fetchone()[0]
    conn.close()

    return {
        'base_inserted': base_inserted,
        'target_inserted': target_inserted,
        'total_base': total_base,
        'total_target': total_target,
        'cutoff_date': cutoff_date,
    }


# ──────────────────────────────────────────
# 共享 loader(供 page 用)
# ──────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner='加载城市区县基础数据…')
def load_district_base() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE_BASE,))
        if cur.fetchone() is None:
            return pd.DataFrame()
        df = pd.read_sql(f'SELECT * FROM {TABLE_BASE}', conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=600, show_spinner='加载营销活动目标数据…')
def load_marketing_target() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE_TARGET,))
        if cur.fetchone() is None:
            return pd.DataFrame()
        df = pd.read_sql(f'SELECT * FROM {TABLE_TARGET}', conn)
    finally:
        conn.close()
    return df
