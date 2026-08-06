"""推广会(会议沙龙)数据导入 + 共享 loader

源表:Excel `会议沙龙参会客户明细表_*.xlsx`(双行表头 + 合并单元格)
目标表:promotion_meeting

主键:(ADSPID, 参会客户编码) 联合
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


TABLE = 'promotion_meeting'

# Excel 列名 → DB 列名映射
# Excel 是双行表头(row1 主名 + row2 子名),用合并单元格
# pandas read_excel(header=[0,1]) 会出 MultiIndex,我们手动展平
COLUMN_MAP = {
    # (row1, row2) → db_col
    ('ADSPID', None):                            'ADSPID',
    ('云商会议ID', None):                          '云商会议ID',
    ('活动名称', None):                            '活动名称',
    ('活动开始时间', None):                        '活动开始时间',
    ('主办方(认证名称)', None):                    '主办方代理商',
    ('主办方(认证名称)', 'None'):                  '主办方代理商',
    ('签到时填写的公司名称', None):                '签到填写公司名',
    ('参会客户编码(实时)', None):                  '参会客户编码',
    ('参会客户名称(实时)', None):                  '参会客户名称',
    ('参与人姓名', None):                          '参与人姓名',
    ('账号', None):                                '账号',
    ('用户类型', None):                            '用户类型',
    ('渠道客户类型(固化)', None):                  '渠道客户类型_固化',
    ('星级(固化)', None):                          '星级_固化',
    ('渠道客户类型(实时)', None):                  '渠道客户类型_实时',
    ('是否激活(实时)', None):                      '是否激活_实时',
    ('报名时间', None):                            '报名时间',
    ('签到时间', None):                            '签到时间',
    ('评价时间', None):                            '评价时间',
    ('评价内容', None):                            '评价内容',
    # 双行表头的卡券字段
    ('50或100元签到券', '卡券编码'):                '券50_编码',
    ('50或100元签到券', '卡券发放面额'):            '券50_面额',
    ('50或100元签到券', '卡券使用状态'):            '券50_状态',
    ('S2产品5元签到券', '卡券编码'):                '券5_编码',
    ('S2产品5元签到券', '卡券发放面额'):            '券5_面额',
    ('S2产品5元签到券', '卡券使用状态'):            '券5_状态',
    ('参会后安装红包金额', None):                '参会后安装红包金额',
    ('夜视王产品上线台数', None):                '夜视王产品上线台数',
}


DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    ADSPID            TEXT,
    参会客户编码      TEXT,
    云商会议ID         TEXT,
    活动名称           TEXT,
    活动开始时间       TEXT,
    主办方代理商       TEXT,
    签到填写公司名     TEXT,
    参会客户名称       TEXT,
    参与人姓名         TEXT,
    账号              TEXT,
    用户类型           TEXT,
    渠道客户类型_固化   TEXT,
    星级_固化           TEXT,
    渠道客户类型_实时   TEXT,
    是否激活_实时       TEXT,
    报名时间           TEXT,
    签到时间           TEXT,
    评价时间           TEXT,
    评价内容           TEXT,
    券50_编码          TEXT,
    券50_面额          REAL,
    券50_状态          TEXT,
    券5_编码           TEXT,
    券5_面额           REAL,
    券5_状态           TEXT,
    参会后安装红包金额  REAL,
    夜视王产品上线台数  REAL,
    PRIMARY KEY (ADSPID, 参会客户编码)
)
"""

INDEXES = [
    f'CREATE INDEX IF NOT EXISTS idx_pm_客户 ON {TABLE}(参会客户编码)',
    f'CREATE INDEX IF NOT EXISTS idx_pm_活动 ON {TABLE}(活动名称)',
    f'CREATE INDEX IF NOT EXISTS idx_pm_时间 ON {TABLE}(活动开始时间)',
    f'CREATE INDEX IF NOT EXISTS idx_pm_主办方 ON {TABLE}(主办方代理商)',
]


def _normalize_header_chars(s):
    """Excel 里中文括号可能是全角或半角 — 都统一成半角"""
    if s is None:
        return None
    s = str(s).strip()
    # 全角括号 → 半角
    s = s.replace('（', '(').replace('）', ')')
    return s


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把 MultiIndex columns (row1, row2) 通过 COLUMN_MAP 映射到 DB 列名,
    不在 map 里的列直接 drop。"""
    keep_indices = []
    new_names = []
    for i, col in enumerate(df.columns):
        if isinstance(col, tuple):
            r1 = _normalize_header_chars(col[0])
            r2 = _normalize_header_chars(col[1]) if len(col) > 1 else None
            if r1 and r1.startswith('Unnamed'):
                r1 = None
            if r2 and (r2.startswith('Unnamed') or r2 == 'nan' or r2 == 'None'):
                r2 = None
        else:
            r1 = _normalize_header_chars(col)
            r2 = None

        key1 = (r1, r2)
        key2 = (r1, None)
        if key1 in COLUMN_MAP:
            keep_indices.append(i)
            new_names.append(COLUMN_MAP[key1])
        elif key2 in COLUMN_MAP:
            keep_indices.append(i)
            new_names.append(COLUMN_MAP[key2])
        # 否则 drop

    df = df.iloc[:, keep_indices].copy()
    df.columns = new_names
    return df


def import_promotion_excel(uploaded_file, mode: str = 'merge') -> dict:
    """导入推广会 Excel 到 promotion_meeting 表.

    Args:
        uploaded_file: streamlit file_uploader 对象 或 文件路径
        mode: 'merge'(默认,PK 重合则覆盖)/ 'append'(已有跳过)/ 'replace'(先清表再导)

    Returns:
        {inserted, updated, total, duplicates_in_file}
    """
    # 双行表头读 Excel
    df = pd.read_excel(uploaded_file, header=[0, 1])
    df = _flatten_columns(df)

    # 检查必备列
    must_have = ['ADSPID', '参会客户编码', '活动名称']
    missing = [c for c in must_have if c not in df.columns]
    if missing:
        raise ValueError(f'缺少必备列: {missing}\n实际列: {list(df.columns)}')

    # 强制主键非空字符串
    df['ADSPID'] = df['ADSPID'].astype(str).str.strip()
    df['参会客户编码'] = df['参会客户编码'].astype(str).str.strip()
    # 丢掉主键空 / 'nan' 的
    df = df[(df['ADSPID'] != '') & (df['ADSPID'] != 'nan')]
    df = df[(df['参会客户编码'] != '') & (df['参会客户编码'] != 'nan')]

    # 文件内去重(留最后)
    dup = len(df) - df.duplicated(subset=['ADSPID', '参会客户编码']).sum() - df['ADSPID'].count()
    dup_count = int(df.duplicated(subset=['ADSPID', '参会客户编码']).sum())
    if dup_count > 0:
        df = df.drop_duplicates(subset=['ADSPID', '参会客户编码'], keep='last').reset_index(drop=True)

    # 时间字段格式化为字符串(SQLite 不存 datetime)
    for col in ['活动开始时间', '报名时间', '签到时间', '评价时间']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            df[col] = df[col].where(df[col].notna(), None)

    # 面额转 numeric(可能是字符串)
    for col in ['券50_面额', '券5_面额']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 全表 NaN → None(SQLite 兼容)
    df = df.where(pd.notnull(df), None)

    # ── 写库 ──
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(DDL)
    for ix in INDEXES:
        cur.execute(ix)

    if mode == 'replace':
        cur.execute(f'DELETE FROM {TABLE}')

    # 拿现有 PK
    existing = set(
        (r[0], r[1])
        for r in cur.execute(f'SELECT ADSPID, 参会客户编码 FROM {TABLE}').fetchall()
    )

    inserted = updated = 0
    cols_in_table = [
        'ADSPID', '参会客户编码', '云商会议ID', '活动名称', '活动开始时间',
        '主办方代理商', '签到填写公司名', '参会客户名称', '参与人姓名', '账号',
        '用户类型', '渠道客户类型_固化', '星级_固化', '渠道客户类型_实时',
        '是否激活_实时', '报名时间', '签到时间', '评价时间', '评价内容',
        '券50_编码', '券50_面额', '券50_状态',
        '券5_编码', '券5_面额', '券5_状态',
    ]
    # 补缺失列
    for c in cols_in_table:
        if c not in df.columns:
            df[c] = None

    verb = 'INSERT OR REPLACE' if mode == 'merge' else 'INSERT OR IGNORE'
    placeholders = ', '.join('?' * len(cols_in_table))
    sql = f'{verb} INTO {TABLE} ({", ".join(f"`{c}`" for c in cols_in_table)}) VALUES ({placeholders})'

    rows_to_insert = df[cols_in_table].values.tolist()
    for row in rows_to_insert:
        pk = (str(row[0]), str(row[1]))
        cur.execute(sql, row)
        if pk in existing:
            if mode == 'merge':
                updated += 1
        else:
            inserted += 1
            existing.add(pk)

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]
    conn.close()

    return {
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'duplicates_in_file': dup_count,
    }


# ──────────────────────────────────────────
# 共享 loader(供 page 用)
# ──────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner="正在加载推广会数据…")
def load_promotion_shared() -> pd.DataFrame:
    """读 promotion_meeting 表,加几个派生字段"""
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

    # 时间字段
    for col in ['活动开始时间', '报名时间', '签到时间', '评价时间']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # 派生:活动年月 / 活动日期 / 是否签到
    df['活动年月'] = df['活动开始时间'].dt.to_period('M').astype(str)
    df['活动日期'] = df['活动开始时间'].dt.date
    df['是否签到'] = df['签到时间'].notna()

    return df
