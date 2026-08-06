#!/usr/bin/env python3
"""代理商进货指导数据层 — 代理商×内部型号 进/销/存矩阵 + 滞销/补货双预警

三源(金先生 2026-07 定的 V1 口径):
  - 动销 = product_flow(出库客户名称=代理商)的上线 —— 主轴。该表按月全量更新为主,
    30/90 天窗口锚定数据水位(MAX上线日)而非今天,防断档期动销被系统性低估
  - 估算现存 = inventory_snapshot 最新季度在库序列号 − 盘库后已上线序列号(序列号级净额)
    ⚠️ 未含盘库日之后的新进货(进货表断档),现存可能低估 → 补货预警偏保守,滞销侧不受影响
  - 进货参考 = dealer_purchase(月粒度,2026 年度替换更新见 load_dealer_purchase_2026.py)取最近 6 个可用月

判定(阈值页面可调):
  - 滞销 = 近90天动销≤2台 且 (现存≥5台 或 现存货值≥5000元);严重度=货值×库龄
  - 补货 = 近30天动销≥3台 且 可售天数<15;建议补货量=(30天−可售天数)×日均动销,停售品拦截
代理商口径 = product_flow 出库客户名称(出货口径)。
"""
from __future__ import annotations
import math
import sqlite3
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


def _conn():
    return sqlite3.connect(str(DB_PATH))


def latest_quarter(conn=None) -> str | None:
    own = conn or _conn()
    try:
        r = own.execute(
            "SELECT MAX(盘库季度) FROM inventory_snapshot WHERE 盘库季度 LIKE '____Q_'"
        ).fetchone()
        return r[0] if r else None
    finally:
        if conn is None:
            own.close()


def get_matrix() -> pd.DataFrame:
    """核心引擎:代理商×内部型号 的 动销30/90 + 估算现存 + 库龄 + 进货 + 状态/专项/呆滞标记。

    行 = (有Q2在库 ∪ 近90天有动销) 的 代理商×内部型号。
    """
    conn = _conn()
    try:
        q = latest_quarter(conn)

        # ── 存:最新季度在库序列号 − 已上线序列号(净额) ──
        # 单价≥90万 = 999999 占位符(价格未知),货值按 0 计,台数照算
        inv = pd.read_sql("""
            SELECT i.盘库客户名称 AS 代理商, i.内部型号, i.外部型号,
                   i.产品序列号, i.库龄天数, date(i.盘库时间) AS 盘库日,
                   CASE WHEN COALESCE(i.产品现有分销价, i.产品分销价, 0) >= 900000
                        THEN 0 ELSE COALESCE(i.产品现有分销价, i.产品分销价, 0)
                   END AS 货值
              FROM inventory_snapshot i
             WHERE i.盘库季度 = ? AND i.是否在库 = 1
               AND i.内部型号 IS NOT NULL AND i.内部型号 != ''
        """, conn, params=[q]) if q else pd.DataFrame()
        # 盘库参与名单(整季度,含已全部动销的):只有参与者的"现存/可售天数"可信
        part = set()
        if q:
            part = set(r[0] for r in conn.execute(
                "SELECT DISTINCT 盘库客户名称 FROM inventory_snapshot WHERE 盘库季度=?",
                (q,)) if r[0])
        if not inv.empty:
            sold = pd.read_sql(
                "SELECT DISTINCT 产品序列号 FROM product_flow "
                "WHERE 上线时间 IS NOT NULL AND 产品序列号 != '***'",
                conn)
            sold_set = set(sold['产品序列号'].astype(str))
            inv['已动销'] = inv['产品序列号'].astype(str).isin(sold_set)
            left = inv[~inv['已动销']]
            # 当前库龄 = 盘库时点库龄 + 盘库以来天数
            since = (pd.Timestamp.now().normalize()
                     - pd.to_datetime(left['盘库日'])).dt.days
            left = left.assign(当前库龄=left['库龄天数'].fillna(0) + since)
            stock = (left.groupby(['代理商', '内部型号'])
                     .agg(现存台数=('产品序列号', 'nunique'),
                          现存货值=('货值', 'sum'),
                          平均库龄天=('当前库龄', 'mean'),
                          外部型号=('外部型号', 'first'))
                     .reset_index())
            stock['现存货值_万'] = (stock['现存货值'] / 10000).round(2)
            stock['平均库龄天'] = stock['平均库龄天'].round(0)
        else:
            stock = pd.DataFrame(columns=['代理商', '内部型号', '现存台数',
                                          '现存货值', '现存货值_万', '平均库龄天', '外部型号'])

        # ── 销:近90天上线(30/90窗口) ──
        # 窗口锚定"数据水位"(MAX上线日)而非今天:product_flow 按月全量更新,
        # 用 date('now') 会在断档期把日均动销系统性低估 → 补货漏报/滞销误报。
        # '***' = 外省代理商占位序列号,COUNT(DISTINCT)会把几百行压成1台,剔除。
        asof = conn.execute(
            "SELECT MAX(date(上线时间)) FROM product_flow WHERE 上线时间 IS NOT NULL"
        ).fetchone()[0]
        sales = pd.read_sql("""
            SELECT 出库客户名称 AS 代理商, 内部型号,
                   MAX(外部型号) AS 外部型号_s,
                   COUNT(DISTINCT CASE WHEN 上线日期 >= date(?, '-30 day')
                                       THEN 产品序列号 END) AS 动销30,
                   COUNT(DISTINCT 产品序列号) AS 动销90,
                   MAX(上线日期) AS 最近上线日
              FROM product_flow_v
             WHERE 上线日期 >= date(?, '-90 day')
               AND 内部型号 IS NOT NULL AND 内部型号 != ''
               AND 出库客户名称 IS NOT NULL AND 出库客户名称 != ''
               AND 产品序列号 != '***'
             GROUP BY 1, 2
        """, conn, params=[asof, asof])

        df = stock.merge(sales, on=['代理商', '内部型号'], how='outer')
        for c in ['现存台数', '现存货值', '现存货值_万', '动销30', '动销90']:
            df[c] = df[c].fillna(0)
        df['外部型号'] = df['外部型号'].fillna(df['外部型号_s'])
        df = df.drop(columns=['外部型号_s'])
        df['日均动销'] = (df['动销30'] / 30).round(3)
        df['可售天数'] = df.apply(
            lambda r: round(r['现存台数'] / r['日均动销'], 1)
            if r['日均动销'] > 0 else None, axis=1)

        # ── 进:dealer_purchase 最近6个可用月 ──
        try:
            mx = conn.execute(
                "SELECT MAX(数据年份*100+月份) FROM dealer_purchase").fetchone()[0]
            if mx:
                lo_y, lo_m = divmod(mx, 100)
                lo_m -= 5
                while lo_m <= 0:
                    lo_m += 12
                    lo_y -= 1
                buy = pd.read_sql("""
                    SELECT TRIM(下单客户) AS 代理商, 内部型号,
                           SUM(实发数量) AS 近6月进货
                      FROM dealer_purchase
                     WHERE 数据年份*100+月份 BETWEEN ? AND ?
                       AND 内部型号 IS NOT NULL AND 内部型号 NOT IN ('', '(空白)')
                     GROUP BY 1, 2
                """, conn, params=[lo_y * 100 + lo_m, mx])
                df = df.merge(buy, on=['代理商', '内部型号'], how='left')
        except (sqlite3.Error, pd.errors.DatabaseError):
            pass
        if '近6月进货' not in df.columns:
            df['近6月进货'] = 0
        df['近6月进货'] = df['近6月进货'].fillna(0)

        # ── 产品状态(报价表按外部型号) / 专项(物料号→product_focus) / 呆滞清单标记 ──
        try:
            quo = pd.read_sql(
                "SELECT 型号 AS 外部型号, MAX(产品状态) AS 产品状态 "
                "FROM dahua_quotation GROUP BY 1", conn)
            df = df.merge(quo, on='外部型号', how='left')
        except (sqlite3.Error, pd.errors.DatabaseError):
            df['产品状态'] = None
        try:
            foc = pd.read_sql("""
                SELECT pf.内部型号, GROUP_CONCAT(DISTINCT fc.专项) AS 专项
                  FROM (SELECT DISTINCT 内部型号, 物料号 FROM product_flow
                         WHERE 内部型号 IS NOT NULL AND 物料号 IS NOT NULL) pf
                  JOIN product_focus fc ON fc.物料号 = pf.物料号
                 GROUP BY 1
            """, conn)
            df = df.merge(foc, on='内部型号', how='left')
        except (sqlite3.Error, pd.errors.DatabaseError):
            df['专项'] = None
        try:
            # 未清理行的 是否上线 以 NULL 落库(生产实测 6358 行),!= 'Y' 三值逻辑会全排掉
            st_pairs = pd.read_sql("""
                SELECT DISTINCT TRIM(代理商) AS 代理商, 内部型号, 1 AS 呆滞清单
                  FROM stale_inventory WHERE COALESCE(是否上线,'') != 'Y'
            """, conn)
            df = df.merge(st_pairs, on=['代理商', '内部型号'], how='left')
        except (sqlite3.Error, pd.errors.DatabaseError):
            df['呆滞清单'] = None
        df['呆滞清单'] = df['呆滞清单'].fillna(0).astype(bool)

        df['盘库参与'] = df['代理商'].isin(part)
        df['_meta_quarter'] = q
        df['_meta_asof'] = asof
        return df
    finally:
        conn.close()


def inv_dealers(df: pd.DataFrame) -> set:
    """参加最新盘库的代理商(只有这些的现存/可售天数可信,能出双预警)。"""
    if '盘库参与' in df.columns:
        return set(df[df['盘库参与']]['代理商'].unique())
    return set(df[df['现存台数'] > 0]['代理商'].unique())


def stale_alerts(df: pd.DataFrame, *, max_sales90=2, min_units=5, min_value=5000):
    """滞销预警:现存压着 + 近90天几乎不动销。返回(明细, 全省型号汇总)。"""
    val_ok = (df['现存货值'] >= min_value) if min_value > 0 else False  # 0=禁用货值分支,防恒真
    m = df[(df['现存台数'] > 0) & (df['动销90'] <= max_sales90)
           & ((df['现存台数'] >= min_units) | val_ok)].copy()
    if m.empty:
        return m, m
    m['严重度'] = (m['现存货值'] * m['平均库龄天'].fillna(0) / 10000).round(1)
    cols = ['代理商', '内部型号', '外部型号', '现存台数', '现存货值_万', '平均库龄天',
            '动销90', '动销30', '最近上线日', '近6月进货', '产品状态', '专项',
            '呆滞清单', '严重度']
    m = m[cols].sort_values('严重度', ascending=False).reset_index(drop=True)
    prov = (m.groupby(['内部型号', '外部型号'], dropna=False)
            .agg(涉及代理商=('代理商', 'nunique'), 现存台数=('现存台数', 'sum'),
                 现存货值_万=('现存货值_万', 'sum'), 平均库龄天=('平均库龄天', 'mean'))
            .reset_index().sort_values('现存货值_万', ascending=False))
    prov['平均库龄天'] = prov['平均库龄天'].round(0)
    return m, prov


def restock_alerts(df: pd.DataFrame, *, min_sales30=3, days_threshold=15, target_days=30):
    """补货预警:动销活跃 + 可售天数不足。建议补货量=(目标天数−可售天数)×日均动销。
    仅限盘库参与的代理商 —— 未盘库的"现存0"是没数据不是卖光,不可报警。"""
    base = df[df['盘库参与']] if '盘库参与' in df.columns else df
    m = base[(base['动销30'] >= min_sales30) & (base['可售天数'].notna())
             & (base['可售天数'] < days_threshold)].copy()
    if m.empty:
        return m
    m['建议补货量'] = m.apply(
        lambda r: max(0, math.ceil((target_days - r['可售天数']) * r['日均动销'])), axis=1)
    st_txt = m['产品状态'].astype(str)
    stop = st_txt.isin(['停售', '已停售', '退市', '已退市'])
    soon = st_txt.str.contains('即将', na=False) & ~stop
    m.loc[stop, '建议补货量'] = 0
    m['提示'] = ''
    m.loc[stop, '提示'] = '⛔ 停售/退市,不建议补货'
    m.loc[soon, '提示'] = '⚠️ 即将退市/停售,谨慎补货'
    m = m[(m['建议补货量'] > 0) | stop]  # 建议0且非停售 = 阈值倒挂产物,不算预警
    cols = ['代理商', '内部型号', '外部型号', '动销30', '日均动销', '现存台数',
            '可售天数', '建议补货量', '产品状态', '专项', '近6月进货', '提示']
    return m[cols].sort_values(['可售天数', '动销30'],
                               ascending=[True, False]).reset_index(drop=True)


def dealer_detail(df: pd.DataFrame, dealer: str) -> pd.DataFrame:
    """单代理商全型号进/销/存明细(型号搜索在页面做)。"""
    d = df[df['代理商'] == dealer].copy()
    cols = ['内部型号', '外部型号', '动销30', '动销90', '日均动销', '现存台数',
            '现存货值_万', '平均库龄天', '可售天数', '近6月进货', '最近上线日',
            '产品状态', '专项', '呆滞清单']
    return (d[cols].sort_values('动销90', ascending=False).reset_index(drop=True))
