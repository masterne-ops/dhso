"""无线渠道明细 — 数据层。

每个服务商一行：
  服务商名称 / 客户编码 / 类型 / 所属一级(代理商) / 地市 / 区县 /
  激活台数 · 无线激活台数 / 总铺货台数 · 无线铺货台数 / 总体铺货完成率 · 无线铺货完成率

口径（与金先生的无线分析一致）：
  - 无线 = 产品线一级/产品一级 = '无线物联网络摄像机'
  - 激活：install_redpack（红包扫码口径，上线客户编码 归集，服务商可干净归属）
    台数 = COUNT(DISTINCT 产品序列号)，按 上线时间 落在 [start, end] 筛
    ⚠️ 不用 product_flow 全量：无线多为 4G 自助上线，66% 落在「其他」桶无法归属到服务商
  - 铺货：distribution_info（客户编码_下级 归集），完成 = 铺货单状态='已完成'，
    按 提交铺货时间 落在 [start, end] 筛
  - 服务商属性(名称/地市/区县/代理商/类型)：口径无关，取自 install_redpack 主档，
    画像 provider_profile 兜底；代理商 = 所属一级客户（出现最多者），类型 = 客户分类
"""
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from _loaders import DB_PATH

WIRELESS = '无线物联网络摄像机'

_OUT_COLS =['服务商名称', '客户编码', '类型', '激活台数', '无线激活台数',
             '总铺货台数', '无线铺货台数', '总体铺货完成率', '无线铺货完成率',
             '所属一级', '地市', '区县']


def _attr_master(conn):
    """服务商属性主档（口径无关）：客户编码 → 名称/地市/区县/代理商/类型。"""
    base = pd.read_sql(
        "SELECT 上线客户编码 AS 客户编码, MAX(上线客户名称) AS 服务商名称, "
        "MAX(上线客户地市) AS 地市, MAX(上线客户区县) AS 区县 "
        "FROM install_redpack "
        "WHERE 上线客户编码 IS NOT NULL AND 上线客户编码 != '' "
        "GROUP BY 上线客户编码", conn)

    # 代理商 = 所属一级客户（取出现次数最多的那个）
    dl = pd.read_sql(
        "SELECT 上线客户编码 AS 客户编码, 所属一级客户 AS 代理商, COUNT(*) AS n "
        "FROM install_redpack "
        "WHERE 所属一级客户 IS NOT NULL AND 所属一级客户 != '' "
        "AND 上线客户编码 IS NOT NULL AND 上线客户编码 != '' "
        "GROUP BY 上线客户编码, 所属一级客户", conn)
    if not dl.empty:
        dl = (dl.sort_values('n').drop_duplicates('客户编码', keep='last')
                [['客户编码', '代理商']])
        base = base.merge(dl, on='客户编码', how='left')
    else:
        base['代理商'] = None

    # 类型 + 画像兜底（地市/区县/代理商/名称）
    prof = pd.read_sql(
        "SELECT 客户编码, 客户分类 AS 类型, 地市 AS _p地市, 区县 AS _p区县, "
        "上级分销商名称 AS _p代理商, 公司名称 AS _p名 "
        "FROM provider_profile WHERE 客户编码 IS NOT NULL", conn).drop_duplicates('客户编码')

    base['客户编码'] = base['客户编码'].astype(str)
    prof['客户编码'] = prof['客户编码'].astype(str)
    m = base.merge(prof, on='客户编码', how='outer')
    m['服务商名称'] = m['服务商名称'].fillna(m['_p名'])
    m['地市'] = m['地市'].fillna(m['_p地市'])
    m['区县'] = m['区县'].fillna(m['_p区县'])
    m['代理商'] = m['代理商'].fillna(m['_p代理商'])
    return m[['客户编码', '服务商名称', '地市', '区县', '代理商', '类型']]


def _activation(conn, start, end_excl):
    """各服务商 激活台数 / 无线激活台数（红包扫码口径，服务商可归属）。"""
    sql = (
        'SELECT 上线客户编码 AS 客户编码, '
        'COUNT(DISTINCT 产品序列号) AS 激活台数, '
        'COUNT(DISTINCT CASE WHEN 产品一级 = ? THEN 产品序列号 END) AS 无线激活台数 '
        'FROM install_redpack '
        "WHERE 上线客户编码 IS NOT NULL AND 上线客户编码 != '' "
        'AND 上线时间 >= ? AND 上线时间 < ? '
        'GROUP BY 上线客户编码')
    df = pd.read_sql(sql, conn, params=(WIRELESS, start, end_excl))
    df['客户编码'] = df['客户编码'].astype(str)
    return df


def _distribution(conn, start, end_excl):
    """各服务商（下级）总铺货 / 无线铺货 / 完成数。"""
    sql = (
        "SELECT 客户编码_下级 AS 客户编码, "
        "MAX(客户名称_下级) AS _d名, MAX(客户所在城市_下级) AS _d地市, "
        "MAX(客户所在区县_下级) AS _d区县, MAX(客户名称_上级) AS _d代理商, "
        "COUNT(*) AS 总铺货台数, "
        "SUM(CASE WHEN 铺货单状态 = '已完成' THEN 1 ELSE 0 END) AS _总完成, "
        "SUM(CASE WHEN 产品线一级 = ? THEN 1 ELSE 0 END) AS 无线铺货台数, "
        "SUM(CASE WHEN 产品线一级 = ? AND 铺货单状态 = '已完成' THEN 1 ELSE 0 END) AS _无线完成 "
        "FROM distribution_info "
        "WHERE 客户编码_下级 IS NOT NULL AND 客户编码_下级 != '' "
        "AND 提交铺货时间 >= ? AND 提交铺货时间 < ? "
        "GROUP BY 客户编码_下级")
    df = pd.read_sql(sql, conn, params=(WIRELESS, WIRELESS, start, end_excl))
    df['客户编码'] = df['客户编码'].astype(str)
    return df


def get_wireless_provider_table(start_date, end_date):
    """服务商级无线明细表（红包扫码口径）。start_date/end_date 为 'YYYY-MM-DD'（含端点）。"""
    start = f'{start_date} 00:00:00'
    end_excl = (datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')

    conn = sqlite3.connect(DB_PATH)
    try:
        act = _activation(conn, start, end_excl)
        dist = _distribution(conn, start, end_excl)
        master = _attr_master(conn)
    finally:
        conn.close()

    df = act.merge(dist, on='客户编码', how='outer').merge(master, on='客户编码', how='left')
    if df.empty:
        return pd.DataFrame(columns=_OUT_COLS)

    # 属性兜底
    df['服务商名称'] = df['服务商名称'].fillna(df.get('_d名')).fillna(df['客户编码'])
    df['地市'] = df['地市'].fillna(df.get('_d地市'))
    df['区县'] = df['区县'].fillna(df.get('_d区县'))
    df['代理商'] = df['代理商'].fillna(df.get('_d代理商'))
    df['类型'] = df['类型'].fillna('(未分类)')

    # 数值兜底
    for c in ['激活台数', '无线激活台数', '总铺货台数', '无线铺货台数', '_总完成', '_无线完成']:
        df[c] = pd.to_numeric(df.get(c), errors='coerce').fillna(0).astype(int)

    # 完成率（分母为 0 → 留空）
    df['总体铺货完成率'] = df.apply(
        lambda r: r['_总完成'] / r['总铺货台数'] if r['总铺货台数'] else None, axis=1)
    df['无线铺货完成率'] = df.apply(
        lambda r: r['_无线完成'] / r['无线铺货台数'] if r['无线铺货台数'] else None, axis=1)

    df = df.rename(columns={'代理商': '所属一级'})
    df = df[_OUT_COLS].sort_values(
        ['无线激活台数', '激活台数'], ascending=False).reset_index(drop=True)
    return df
