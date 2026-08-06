"""产品专项映射(product_focus)— M13 三大专项攻坚的产品基础数据

主键:物料号(install_redpack_v.物料号 / product_flow_v.物料号 都有此字段,100% 填充可 join)
分类:夜视王 / 无线 / 场景化

数据源:2026-5月 SMB 专项清单(用户给的 Excel,3 sheets)
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


TABLE = 'product_focus'
TABLE_TARGET = 'focus_target'

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    物料号         TEXT PRIMARY KEY,
    规格型号       TEXT,
    专项           TEXT NOT NULL,   -- 夜视王 / 无线 / 场景化
    产品类别       TEXT,             -- IPC / NVR / ...
    产品状态       TEXT,             -- 正常销售 / 停售 / 即将停售 / 未上架新品
    备注           TEXT,
    更新人         TEXT,
    更新时间       TEXT
)
"""

INDEXES = [
    f'CREATE INDEX IF NOT EXISTS idx_pf_focus ON {TABLE}(专项)',
    f'CREATE INDEX IF NOT EXISTS idx_pf_status ON {TABLE}(产品状态)',
    f'CREATE INDEX IF NOT EXISTS idx_pf_model ON {TABLE}(规格型号)',
]

# ─── 专项目标表(地市级 × 专项 × 年度)─────
DDL_TARGET = f"""
CREATE TABLE IF NOT EXISTS {TABLE_TARGET} (
    专项           TEXT NOT NULL,    -- 夜视王 / 无线 / 场景化
    年度           INTEGER NOT NULL,
    地市           TEXT NOT NULL,    -- '浙江合计' / 11 地市
    目标台数       INTEGER,
    目标货值_万    REAL,
    备注           TEXT,
    更新人         TEXT,
    更新时间       TEXT,
    PRIMARY KEY (专项, 年度, 地市)
)
"""


def init_table():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute(DDL_TARGET)
    for ix in INDEXES:
        cur.execute(ix)
    conn.commit()
    conn.close()


def import_focus_excel(uploaded_file, mode: str = 'replace',
                        operator: str = None) -> dict:
    """导入专项清单 Excel(3 sheets:夜视王专项 / 无线专项 / 场景化专项)

    Args:
        uploaded_file: file_uploader 或 路径
        mode: 'replace'(默认,全表替换 — 推荐)/ 'merge'(物料号合并)
        operator: 操作人

    Returns:
        {inserted, updated, total, by_focus}
    """
    # 读 3 sheets
    sheets = pd.read_excel(uploaded_file, sheet_name=None)

    all_rows = []
    for sn, df in sheets.items():
        # 兼容 "夜视王专项" / "夜视王" 两种命名
        focus = sn.replace('专项', '').strip()
        if focus not in ('夜视王', '无线', '场景化'):
            continue
        df = df.copy()
        df.columns = (df.columns.astype(str).str.strip()
                       .str.replace(r'[\r\n\t]+', ' ', regex=True)
                       .str.replace(r'\s+', ' ', regex=True))
        df['专项'] = focus
        all_rows.append(df)

    if not all_rows:
        raise ValueError('Excel 里没找到「夜视王专项 / 无线专项 / 场景化专项」3 个 sheet 之一')

    focus_df = pd.concat(all_rows, ignore_index=True)

    # 必备列
    must_have = ['物料号', '专项']
    missing = [c for c in must_have if c not in focus_df.columns]
    if missing:
        raise ValueError(f'缺必备列: {missing}')

    focus_df['物料号'] = focus_df['物料号'].astype(str).str.strip()
    focus_df = focus_df[(focus_df['物料号'] != '') & (focus_df['物料号'] != 'nan')]

    # 文件内主键去重
    dup = len(focus_df) - focus_df['物料号'].nunique()
    if dup > 0:
        focus_df = focus_df.drop_duplicates(subset='物料号', keep='last').reset_index(drop=True)

    # 补齐字段
    for c in ['规格型号', '产品类别', '产品状态', '备注']:
        if c not in focus_df.columns:
            focus_df[c] = None
    focus_df = focus_df.where(pd.notnull(focus_df), None)

    # 审计
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    focus_df['更新人'] = operator or 'system'
    focus_df['更新时间'] = now

    cols = ['物料号', '规格型号', '专项', '产品类别', '产品状态', '备注', '更新人', '更新时间']

    init_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if mode == 'replace':
        cur.execute(f'DELETE FROM {TABLE}')

    existing = set(r[0] for r in cur.execute(f'SELECT 物料号 FROM {TABLE}').fetchall())

    placeholders = ', '.join('?' * len(cols))
    sql = (f'INSERT OR REPLACE INTO {TABLE} ({", ".join(f"`{c}`" for c in cols)}) '
           f'VALUES ({placeholders})')

    inserted = updated = 0
    for row in focus_df[cols].values.tolist():
        cur.execute(sql, row)
        if str(row[0]) in existing:
            updated += 1
        else:
            inserted += 1
            existing.add(str(row[0]))

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]

    # 按专项分布
    by_focus = pd.read_sql(
        f'SELECT 专项, COUNT(*) AS n FROM {TABLE} GROUP BY 1 ORDER BY 1',
        conn,
    ).set_index('专项')['n'].to_dict()

    conn.close()
    return {
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'duplicates_in_file': dup,
        'by_focus': by_focus,
    }


# ──────────────────────────────────────────
# 共享 loader
# ──────────────────────────────────────────


@st.cache_data(ttl=600, show_spinner='加载专项产品清单…')
def load_focus(focus: str = None, only_active: bool = False) -> pd.DataFrame:
    """读 product_focus

    Args:
        focus: '夜视王' / '无线' / '场景化',不传 = 全部
        only_active: True = 仅"正常销售"
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

        sql = f'SELECT * FROM {TABLE} WHERE 1=1'
        params: list = []
        if focus:
            sql += ' AND 专项 = ?'
            params.append(focus)
        if only_active:
            sql += " AND 产品状态 = '正常销售'"
        df = pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()
    return df


def import_focus_target_excel(uploaded_file, mode: str = 'replace',
                              operator: str = None) -> dict:
    """导入专项目标 Excel(地市级 × 专项 × 年度)

    Excel 格式(列):专项 / 年度 / 地市 / 目标台数 / 目标货值_万 / 备注
    支持长格式(一行一记录)或宽格式(地市为行,专项为列)
    本函数按长格式处理,如要宽格式让 sheet 区分专项

    mode: 'replace'(默认)/ 'merge'
    """
    df = pd.read_excel(uploaded_file)
    df.columns = (df.columns.astype(str).str.strip()
                   .str.replace(r'[\r\n\t]+', ' ', regex=True)
                   .str.replace(r'\s+', ' ', regex=True))

    required = ['专项', '年度', '地市']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'缺必备列: {missing}\n实际列: {list(df.columns)}')

    df['专项'] = df['专项'].astype(str).str.strip()
    df['地市'] = df['地市'].astype(str).str.strip()
    df = df[df['专项'].isin(['夜视王', '无线', '场景化'])]

    # 数值列
    for c in ['目标台数', '目标货值_万']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    if '备注' not in df.columns:
        df['备注'] = None

    df = df.where(pd.notnull(df), None)

    init_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if mode == 'replace':
        # 只删本次涉及年度的数据(避免清空别的年度)
        years = df['年度'].unique().tolist()
        for y in years:
            cur.execute(f'DELETE FROM {TABLE_TARGET} WHERE 年度 = ?', (int(y),))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cols = ['专项', '年度', '地市', '目标台数', '目标货值_万', '备注', '更新人', '更新时间']
    placeholders = ', '.join('?' * len(cols))
    sql = (f'INSERT OR REPLACE INTO {TABLE_TARGET} ({", ".join(f"`{c}`" for c in cols)}) '
           f'VALUES ({placeholders})')

    inserted = 0
    for _, row in df.iterrows():
        cur.execute(sql, (
            row['专项'], int(row['年度']), row['地市'],
            int(row['目标台数']) if pd.notna(row.get('目标台数')) else None,
            float(row['目标货值_万']) if pd.notna(row.get('目标货值_万')) else None,
            row.get('备注'),
            operator or 'system',
            now,
        ))
        inserted += 1

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM {TABLE_TARGET}').fetchone()[0]
    conn.close()
    return {'inserted': inserted, 'total': total}


@st.cache_data(ttl=600)
def load_focus_target(focus: str = None, year: int = None) -> pd.DataFrame:
    """读 focus_target,按专项/年度筛选"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_TARGET,),
        )
        if cur.fetchone() is None:
            return pd.DataFrame()
        sql = f'SELECT * FROM {TABLE_TARGET} WHERE 1=1'
        params = []
        if focus:
            sql += ' AND 专项 = ?'
            params.append(focus)
        if year:
            sql += ' AND 年度 = ?'
            params.append(year)
        df = pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()
    return df


def generate_target_template_excel() -> bytes:
    """生成专项目标录入模板(供用户下载填写后上传)"""
    import io
    cities = ['浙江合计', '杭州市', '宁波市', '温州市', '绍兴市', '台州市',
              '嘉兴市', '金华市', '湖州市', '衢州市', '丽水市', '舟山市']
    rows = []
    for focus in ['夜视王', '无线', '场景化']:
        for city in cities:
            rows.append({
                '专项': focus,
                '年度': 2026,
                '地市': city,
                '目标台数': '',
                '目标货值_万': '',
                '备注': '',
            })
    template = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        template.to_excel(w, sheet_name='专项目标录入', index=False)
        # 加 1 个填写说明 sheet
        guide = pd.DataFrame([
            {'字段': '专项', '说明': '夜视王 / 无线 / 场景化(必填)'},
            {'字段': '年度', '说明': '如 2026(必填)'},
            {'字段': '地市', '说明': '浙江合计 / 杭州市 / 宁波市 ...(必填)'},
            {'字段': '目标台数', '说明': '年度目标台数(选填,跟"目标货值"二选一或都填)'},
            {'字段': '目标货值_万', '说明': '年度目标货值(万),选填'},
            {'字段': '备注', '说明': '选填'},
        ])
        guide.to_excel(w, sheet_name='填写说明', index=False)
    return buf.getvalue()


@st.cache_data(ttl=600)
def get_focus_summary() -> pd.DataFrame:
    """专项概览:SKU 数 / 正常销售 SKU 数 / 历史 SO(install_redpack)"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(f"""
            WITH so AS (
                SELECT 物料号,
                       COUNT(*) AS 历史台数,
                       SUM(产品现有分销价) AS 历史货值
                  FROM install_redpack_v
                 WHERE 物料号 IS NOT NULL
                 GROUP BY 物料号
            )
            SELECT pf.专项,
                   COUNT(*) AS SKU数,
                   SUM(CASE WHEN pf.产品状态 = '正常销售' THEN 1 ELSE 0 END) AS 在售SKU数,
                   SUM(CASE WHEN so.物料号 IS NOT NULL THEN 1 ELSE 0 END) AS 有上线SKU数,
                   COALESCE(SUM(so.历史台数), 0) AS 历史总台数,
                   ROUND(COALESCE(SUM(so.历史货值), 0)/10000, 2) AS 历史总货值_万
              FROM {TABLE} pf
              LEFT JOIN so ON so.物料号 = pf.物料号
             GROUP BY pf.专项
             ORDER BY pf.专项
        """, conn)
    finally:
        conn.close()
    return df
