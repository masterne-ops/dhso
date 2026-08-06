#!/usr/bin/env python3
"""
SO 环比分析 - 按上线城市导出 Excel 报告
每个城市一个 .xlsx，多 sheet 覆盖系统里所有维度。

用法:
    python src/export_city_report.py
    python src/export_city_report.py --base 2026-03 --curr 2026-04
    python src/export_city_report.py --keep-ecomm        # 保留电商/4G/电商-4G
    python src/export_city_report.py --keep-abnormal     # 保留客户行为异常
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db" / "product_flow.db"
OUT_BASE = BASE_DIR / "reports"
TABLE = "product_flow"

NEEDED_COLS = [
    'ID', '产品序列号', '内部型号',
    '产品系列', '产品子系列', '产品子系列-新',
    '出库客户名称',
    '上线城市', '上线区县',
    '上线时间', '出库时间',
    '最新分销价',
    '上线自云商账号', '上线自客户名称',
    '数据剔除', '客户行为异常',
    '是否异省', '是否异城', '是否异县',
]


# ──────────────────────────────────────────
# 数据加载（与 app.py 一致）
# ──────────────────────────────────────────

def load_df() -> pd.DataFrame:
    if not DB_PATH.exists():
        sys.exit(f"❌ 数据库不存在：{DB_PATH}\n   请先运行 python src/load_data.py")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({TABLE})")
    db_cols = {r[1] for r in cur.fetchall()}
    cols = [c for c in NEEDED_COLS if c in db_cols]
    select_cols = ', '.join('"' + c + '"' for c in cols)
    df = pd.read_sql(f'SELECT {select_cols} FROM {TABLE}', conn)
    conn.close()

    for c in NEEDED_COLS:
        if c not in df.columns:
            df[c] = None

    df['上线时间'] = pd.to_datetime(df['上线时间'], errors='coerce')
    df['上线日期'] = df['上线时间'].dt.date
    df['上线年月'] = df['上线时间'].dt.to_period('M').astype(str)
    df['最新分销价'] = pd.to_numeric(df['最新分销价'], errors='coerce').fillna(0)

    df['产品系列_有效'] = (
        df['产品子系列-新']
        .fillna(df['产品子系列'])
        .fillna(df['产品系列'])
    )
    city = df['上线城市'].fillna('未知城市').astype(str)
    dist = df['上线区县'].fillna('未知区县').astype(str)
    df['上线区县_全'] = city + ' / ' + dist
    df['是否服务商上线'] = df['上线自云商账号'].notna() & (
        df['上线自云商账号'].astype(str).str.strip() != ''
    )
    return df


def filter_clean(df, exclude_tags, drop_abnormal):
    if exclude_tags:
        df = df[~df['数据剔除'].fillna('').astype(str).str.strip().isin(exclude_tags)]
    if drop_abnormal:
        df = df[df['客户行为异常'].fillna('').astype(str).str.strip().str.upper() != 'Y']
    return df


# ──────────────────────────────────────────
# 维度环比
# ──────────────────────────────────────────

def agg_by(df, dim_col):
    return df.groupby(dim_col, dropna=False).agg(
        台数=('ID', 'count'),
        金额=('最新分销价', 'sum'),
    ).reset_index()


def mom_compare(df_base, df_curr, dim_col):
    a = agg_by(df_base, dim_col).rename(columns={'台数': '基准台数', '金额': '基准金额'})
    b = agg_by(df_curr, dim_col).rename(columns={'台数': '当月台数', '金额': '当月金额'})
    m = pd.merge(a, b, on=dim_col, how='outer').fillna(0)
    m['台数变化'] = m['当月台数'] - m['基准台数']
    m['金额变化'] = m['当月金额'] - m['基准金额']
    m['台数变化率'] = np.where(m['基准台数'] > 0, m['台数变化'] / m['基准台数'], np.nan)
    m['金额变化率'] = np.where(m['基准金额'] > 0, m['金额变化'] / m['基准金额'], np.nan)
    return m


def fmt_pct(v):
    if pd.isna(v):
        return '—'
    return f'{v * 100:+.1f}%'


def beautify(m: pd.DataFrame) -> pd.DataFrame:
    out = m.copy()
    if '台数变化率' in out.columns:
        out['台数变化率'] = out['台数变化率'].map(fmt_pct)
    if '金额变化率' in out.columns:
        out['金额变化率'] = out['金额变化率'].map(fmt_pct)
    # 金额保留 0 位小数
    for c in ('基准金额', '当月金额', '金额变化'):
        if c in out.columns:
            out[c] = out[c].round(0).astype('Int64', errors='ignore')
    return out


# ──────────────────────────────────────────
# 单城市导出
# ──────────────────────────────────────────

def export_one_city(df_base_city, df_curr_city, df_anom_city,
                     city, base_m, curr_m, out_path):
    """生成一个城市的 Excel，多 sheet"""

    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:

        # ── Sheet 1: 概况 ──────────────────────────────
        b_qty, c_qty = len(df_base_city), len(df_curr_city)
        b_amt, c_amt = df_base_city['最新分销价'].sum(), df_curr_city['最新分销价'].sum()
        summary = pd.DataFrame({
            '指标': ['总台数', '总金额（元）'],
            f'基准月 {base_m}': [b_qty, round(b_amt)],
            f'对比月 {curr_m}': [c_qty, round(c_amt)],
            '变化': [c_qty - b_qty, round(c_amt - b_amt)],
            '变化率': [
                f'{(c_qty - b_qty) / b_qty * 100:+.1f}%' if b_qty else '—',
                f'{(c_amt - b_amt) / b_amt * 100:+.1f}%' if b_amt else '—',
            ],
        })
        summary.to_excel(writer, sheet_name='概况', index=False)

        # ── Sheet 2-5: 区县 / 产品系列 / 代理商 / 服务商 ──
        dims = [
            ('区县', '上线区县_全', df_base_city, df_curr_city),
            ('产品系列', '产品系列_有效', df_base_city, df_curr_city),
            ('代理商', '出库客户名称', df_base_city, df_curr_city),
            ('服务商', '上线自客户名称',
                df_base_city[df_base_city['是否服务商上线']],
                df_curr_city[df_curr_city['是否服务商上线']]),
        ]
        for sheet_name, col, sb, sc in dims:
            m = mom_compare(sb, sc, col).sort_values('台数变化')
            beautify(m).to_excel(writer, sheet_name=sheet_name, index=False)

        # ── Sheet 6: 日期分布 ──────────────────────────
        daily = pd.concat([
            df_base_city.assign(月=base_m),
            df_curr_city.assign(月=curr_m),
        ])
        if len(daily) > 0:
            daily_g = daily.groupby(['月', '上线日期']).agg(
                台数=('ID', 'count'),
                金额=('最新分销价', 'sum'),
            ).reset_index().sort_values(['月', '上线日期'])
            daily_g['金额'] = daily_g['金额'].round(0).astype('Int64', errors='ignore')
            daily_g.to_excel(writer, sheet_name='日期分布', index=False)
        else:
            pd.DataFrame({'提示': ['当月无数据']}).to_excel(
                writer, sheet_name='日期分布', index=False
            )

        # ── Sheet 7: 异常明细（仅当存在）──────────────
        if len(df_anom_city) > 0:
            cols_anom = [c for c in [
                '上线年月', 'ID', '出库客户名称', '上线城市', '上线区县',
                '上线自客户名称', '内部型号', '产品系列_有效',
                '是否异省', '是否异城', '是否异县',
                '数据剔除', '上线时间', '最新分销价',
            ] if c in df_anom_city.columns]
            df_anom_city[cols_anom].to_excel(writer, sheet_name='异常明细', index=False)


# ──────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', help='基准月 YYYY-MM（默认数据库倒数第二月）')
    parser.add_argument('--curr', help='对比月 YYYY-MM（默认数据库最新月）')
    parser.add_argument('--keep-ecomm', action='store_true',
                        help='保留电商/4G/电商-4G（默认剔除）')
    parser.add_argument('--keep-abnormal', action='store_true',
                        help='保留客户行为异常 Y（默认剔除）')
    args = parser.parse_args()

    df = load_df()

    months = sorted(df['上线年月'].dropna().unique())
    if len(months) < 2:
        sys.exit('❌ 数据库中只有 < 2 个月数据，无法做环比')
    base_m = args.base or months[-2]
    curr_m = args.curr or months[-1]
    if base_m not in months or curr_m not in months:
        sys.exit(f'❌ 月份不在数据库内，可选：{months}')

    # 异常明细单独留一份（剔除前）
    df_anom = df[
        (df['客户行为异常'].fillna('').astype(str).str.strip().str.upper() == 'Y')
        & (df['上线年月'].isin([base_m, curr_m]))
    ]

    # 应用与看板一致的默认过滤
    exclude_tags = [] if args.keep_ecomm else ['4G', '电商', '电商-4G']
    drop_abn = not args.keep_abnormal
    df_clean = filter_clean(df, exclude_tags, drop_abn)

    df_base = df_clean[df_clean['上线年月'] == base_m]
    df_curr = df_clean[df_clean['上线年月'] == curr_m]

    cities = sorted(
        c for c in (set(df_base['上线城市'].dropna()) | set(df_curr['上线城市'].dropna()))
        if str(c).strip() and str(c) != 'nan'
    )

    out_dir = OUT_BASE / f'{base_m}_to_{curr_m}'
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'月份: {base_m} → {curr_m}')
    print(f'剔除标签: {exclude_tags or "（保留全部）"} | 异常剔除: {drop_abn}')
    print(f'城市数: {len(cities)}')
    print(f'输出目录: {out_dir}')
    print()

    # ── 全省汇总文件（前缀 0_ 让它排最前）─────────────
    print('  生成 0_全省汇总.xlsx ...')
    with pd.ExcelWriter(out_dir / '0_全省汇总.xlsx', engine='openpyxl') as w:
        # Sheet 1: 总览
        b_qty, c_qty = len(df_base), len(df_curr)
        b_amt, c_amt = df_base['最新分销价'].sum(), df_curr['最新分销价'].sum()
        pd.DataFrame({
            '指标': ['总台数', '总金额（元）'],
            f'基准月 {base_m}': [b_qty, round(b_amt)],
            f'对比月 {curr_m}': [c_qty, round(c_amt)],
            '变化': [c_qty - b_qty, round(c_amt - b_amt)],
            '变化率': [
                f'{(c_qty - b_qty) / b_qty * 100:+.1f}%' if b_qty else '—',
                f'{(c_amt - b_amt) / b_amt * 100:+.1f}%' if b_amt else '—',
            ],
        }).to_excel(w, sheet_name='总览', index=False)

        # Sheet 2-6: 5 个维度全省环比
        all_dims = [
            ('城市', '上线城市', df_base, df_curr),
            ('区县', '上线区县_全', df_base, df_curr),
            ('产品系列', '产品系列_有效', df_base, df_curr),
            ('代理商', '出库客户名称', df_base, df_curr),
            ('服务商', '上线自客户名称',
                df_base[df_base['是否服务商上线']],
                df_curr[df_curr['是否服务商上线']]),
        ]
        for sheet_name, col, sb, sc in all_dims:
            m = mom_compare(sb, sc, col).sort_values('台数变化')
            beautify(m).to_excel(w, sheet_name=sheet_name, index=False)

    # ── 每个城市一个文件 ─────────────────────────────
    for city in cities:
        df_b = df_base[df_base['上线城市'] == city]
        df_c = df_curr[df_curr['上线城市'] == city]
        df_a = df_anom[df_anom['上线城市'] == city]
        # 文件名安全化
        safe = str(city).replace('/', '_').replace(' ', '').replace('\\', '_')
        out = out_dir / f'{safe}.xlsx'
        export_one_city(df_b, df_c, df_a, city, base_m, curr_m, out)
        print(f'  ✅ {city}: 基准 {len(df_b):,} → 对比 {len(df_c):,} '
              f'（差 {len(df_c) - len(df_b):+,}） → {out.name}')

    print(f'\n📂 全部输出到：{out_dir}')


if __name__ == '__main__':
    main()
