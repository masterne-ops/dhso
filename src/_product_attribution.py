#!/usr/bin/env python3
"""产品视角经营归因分析 — 取数层 (P1 识别+下钻 / P1.5 九因素)

主轴 = 产品子系列。流程:
  1. identify_subseries: 子系列层面找 good/bad(按"同比增量金额"排,达量门槛过滤)
  2. drill_subseries: 每个入选子系列下钻 地市/区县/代理商/服务商,按增量找涨跌贡献点
  3. 九因素:
     - 子系列级(挂每个切片'因素'): 铺货 / 安装红包 / 备货 / 服务商画像(含采购习惯)
     - 经营背景(挂顶层'经营背景'): 推广会 / 跑动 / 业务员红包 / 门头
  4. build: 组装 attribution.json(归因键 P2 由沙箱 LLM 写)

口径:
  - 销量主口径 = 全量感知 product_flow_v.最新分销价; 服务商/红包口径 = install_redpack_v.产品现有分销价(去马甲)
  - 铺货/备货无"产品子系列-新"字段,用 物料号 桥接(从 product_flow_v 取该子系列物料号集合)
  - 同比 vs 去年同期等长周期; 环比 vs 紧邻上一个等长周期

dry-run(需在有数据的库上跑;本地为空壳):
  python3 _product_attribution.py --start 2026-01 --end 2026-04 [--city 杭州市] [--no-factors]
  python3 _product_attribution.py --start 2026-01 --end 2026-04 --json
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH as DB


def shift_period(start, end, months):
    s = pd.Period(start, freq='M') + months
    e = pd.Period(end, freq='M') + months
    return str(s), str(e)


def n_months(start, end):
    return (pd.Period(end, freq='M') - pd.Period(start, freq='M')).n + 1


def pct(cur, base):
    if base and base > 0:
        return round((cur - base) / base, 4)
    return None


def _jsonify(o):
    """递归把 nan→None(json 不接受 NaN),保证产物可 json.dump。"""
    if isinstance(o, dict):
        return {k: _jsonify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonify(v) for v in o]
    if isinstance(o, float) and o != o:
        return None
    return o


# ══════════════════════ 识别 + 下钻 ══════════════════════

def identify_subseries(conn, ps, pe, city=None, top_n=5, bottom_n=5, min_wan=5.0):
    """子系列层面识别亮点/问题。好坏按"同比增量金额"排(对大盘绝对贡献)。"""
    ys, ye = shift_period(ps, pe, -12)
    n = n_months(ps, pe)
    ms, me = shift_period(ps, pe, -n)
    pf_city, pf_p = ("", [])
    if city:
        pf_city, pf_p = (" AND 上线城市 = ?", [city])

    def by_sub(s, e):
        # 子系列空(多为 NVR 下挂、取不到序列号的 IPC,序列号带$)归"无型号",台数/金额照算(口径:都算上)
        return pd.read_sql(f"""
            SELECT COALESCE(NULLIF([产品子系列-新],''),'无型号') AS 子系列,
                   SUM(最新分销价)/10000.0 AS 货值_万,
                   COUNT(*) AS 台数
              FROM product_flow_v
             WHERE 上线年月 BETWEEN ? AND ? {pf_city}
             GROUP BY 1
        """, conn, params=[s, e, *pf_p])

    cur = by_sub(ps, pe).set_index('子系列')
    yoy = by_sub(ys, ye).set_index('子系列')['货值_万']
    mom = by_sub(ms, me).set_index('子系列')['货值_万']

    df = cur.copy()
    df['同期_万'] = yoy.reindex(df.index)
    df['环期_万'] = mom.reindex(df.index)
    df = df.reset_index()
    df['同比'] = [pct(c, b) for c, b in zip(df['货值_万'], df['同期_万'])]
    df['环比'] = [pct(c, b) for c, b in zip(df['货值_万'], df['环期_万'])]
    df['增量_万'] = (df['货值_万'].fillna(0) - df['同期_万'].fillna(0)).round(2)
    for col in ['货值_万', '同期_万', '环期_万']:
        df[col] = df[col].round(2)
    df = df.sort_values('货值_万', ascending=False)

    # 好坏按增量金额排;达量门槛=当期或同期任一≥min_wan(不漏高位跌落,不放小基数噪音)。
    # "无型号"计入全量(df,总量与SO对齐),但排除出亮点/问题榜——无型号没法做型号归因。
    q = df[(df[['货值_万', '同期_万']].max(axis=1) >= min_wan) & (df['子系列'] != '无型号')]
    good = q.sort_values('增量_万', ascending=False).head(top_n)
    bad = q.sort_values('增量_万', ascending=True).head(bottom_n)
    return df, good, bad


def _dim_yoy_pf(conn, sub, ps, pe, dim_field, pf_city, pf_p):
    ys, ye = shift_period(ps, pe, -12)
    # 全外连接(dims=当期∪同期维度):当期归零、同期有货的维度也保留,否则"从大跌到0"会被结构性丢弃
    sql = f"""
      WITH cur AS (
        SELECT {dim_field} AS 维度, SUM(最新分销价) AS v, COUNT(*) AS c
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新]=?
           AND {dim_field} IS NOT NULL AND {dim_field}!='' AND {dim_field} NOT LIKE '%***%' {pf_city}
         GROUP BY 1),
      yoy AS (
        SELECT {dim_field} AS 维度, SUM(最新分销价) AS v0
          FROM product_flow_v
         WHERE 上线年月 BETWEEN ? AND ? AND [产品子系列-新]=?
           AND {dim_field} IS NOT NULL AND {dim_field}!='' AND {dim_field} NOT LIKE '%***%' {pf_city}
         GROUP BY 1),
      dims AS (SELECT 维度 FROM cur UNION SELECT 维度 FROM yoy)
      SELECT d.维度, ROUND(COALESCE(cur.v,0)/10000.0,2) AS 货值_万, COALESCE(cur.c,0) AS 台数,
             ROUND(COALESCE(yoy.v0,0)/10000.0,2) AS 同期_万,
             CASE WHEN yoy.v0>0 THEN ROUND((COALESCE(cur.v,0)-yoy.v0)/yoy.v0,4) END AS 同比,
             ROUND((COALESCE(cur.v,0)-COALESCE(yoy.v0,0))/10000.0,2) AS 增量_万
        FROM dims d LEFT JOIN cur USING(维度) LEFT JOIN yoy USING(维度)
       ORDER BY 增量_万
    """
    return pd.read_sql(sql, conn, params=[ps, pe, sub, *pf_p, ys, ye, sub, *pf_p])


def _sp_yoy(conn, sub, ps, pe, rp_city, rp_p):
    ys, ye = shift_period(ps, pe, -12)
    # 全外连接(codes=当期∪同期编码):同期有货、当期归零的服务商也保留(否则崩到0的头部服务商被丢)。
    # 子系列匹配 COALESCE([产品子系列-新],产品子系列) 兜底空值;同期侧同样去马甲+同 city 过滤,与当期一致。
    sql = f"""
      WITH cur AS (
        SELECT ir.上线客户编码 AS 编码, MAX(ir.上线客户名称) AS 服务商,
               MAX(ir.上线客户地市) AS 地市, MAX(ir.所属一级客户) AS 代理商,
               SUM(ir.产品现有分销价) AS v, COUNT(*) AS c
          FROM install_redpack_v ir
          LEFT JOIN vest_account va ON va.服务商客户编码 = ir.上线客户编码
         WHERE ir.上线年月 BETWEEN ? AND ? AND COALESCE(ir.[产品子系列-新],ir.产品子系列)=?
           AND va.服务商客户编码 IS NULL AND ir.上线客户编码 IS NOT NULL {rp_city}
         GROUP BY ir.上线客户编码),
      yoy AS (
        SELECT ir.上线客户编码 AS 编码, MAX(ir.上线客户名称) AS 服务商,
               MAX(ir.上线客户地市) AS 地市, MAX(ir.所属一级客户) AS 代理商,
               SUM(ir.产品现有分销价) AS v0
          FROM install_redpack_v ir
          LEFT JOIN vest_account va ON va.服务商客户编码 = ir.上线客户编码
         WHERE ir.上线年月 BETWEEN ? AND ? AND COALESCE(ir.[产品子系列-新],ir.产品子系列)=?
           AND va.服务商客户编码 IS NULL AND ir.上线客户编码 IS NOT NULL {rp_city}
         GROUP BY ir.上线客户编码),
      codes AS (SELECT 编码 FROM cur UNION SELECT 编码 FROM yoy)
      SELECT co.编码,
             COALESCE(cur.服务商, yoy.服务商) AS 服务商,
             COALESCE(cur.地市, yoy.地市) AS 地市,
             COALESCE(cur.代理商, yoy.代理商) AS 代理商,
             ROUND(COALESCE(cur.v,0)/10000.0,2) AS 货值_万, COALESCE(cur.c,0) AS 台数,
             ROUND(COALESCE(yoy.v0,0)/10000.0,2) AS 同期_万,
             ROUND((COALESCE(cur.v,0)-COALESCE(yoy.v0,0))/10000.0,2) AS 增量_万
        FROM codes co LEFT JOIN cur USING(编码) LEFT JOIN yoy USING(编码)
       ORDER BY 增量_万
    """
    return pd.read_sql(sql, conn, params=[ps, pe, sub, *rp_p, ys, ye, sub, *rp_p])


def _split_drill(recs, head):
    """recs 已按增量_万升序。按符号切分,避免维度<2*head 时两端重叠+符号串台:
    跌幅Top=增量<0(升序最跌在前);涨幅Top=增量>0(降序最涨在前)。增量=0 两边都不入。"""
    neg = [r for r in recs if (r.get('增量_万') or 0) < 0]
    pos = [r for r in recs if (r.get('增量_万') or 0) > 0]
    return {'跌幅Top': neg[:head], '涨幅Top': list(reversed(pos))[:head]}


def drill_subseries(conn, sub, ps, pe, city=None, head=6):
    """对单个子系列,下钻地市/区县/代理商/服务商,取涨跌贡献两端各 head 名。"""
    pf_city, pf_p = ("", [])
    rp_city, rp_p = ("", [])
    if city:
        pf_city, pf_p = (" AND 上线城市 = ?", [city])
        rp_city, rp_p = (" AND ir.上线客户地市 = ?", [city])

    out = {}
    for key, fld in [('地市', '上线城市'), ('区县', '上线区县'), ('代理商', '出库客户名称')]:
        d = _dim_yoy_pf(conn, sub, ps, pe, fld, pf_city, pf_p).where(lambda x: pd.notnull(x), None)
        out[key] = _split_drill(d.to_dict('records'), head)
    sp = _sp_yoy(conn, sub, ps, pe, rp_city, rp_p).where(lambda x: pd.notnull(x), None)
    out['服务商'] = _split_drill(sp.to_dict('records'), head)
    return out


# ══════════════════════ P1.5 因素:子系列级 ══════════════════════

def _subseries_materials(conn, sub):
    """子系列-新 → 物料号集合(桥接 distribution_info / inventory_snapshot 等无'-新'字段的表)。"""
    return pd.read_sql(
        "SELECT DISTINCT 物料号 FROM product_flow_v "
        "WHERE [产品子系列-新]=? AND 物料号 IS NOT NULL AND 物料号!=''",
        conn, params=[sub])['物料号'].tolist()


def factor_distribution(conn, sub, ps, pe, materials):
    """铺货(distribution_info,物料号桥):当期vs同期 铺货台数/金额/覆盖下级服务商。按提交铺货时间。"""
    if not materials:
        return {'说明': '该子系列在 product_flow 无物料号映射,跳过铺货'}
    ys, ye = shift_period(ps, pe, -12)
    ph = ','.join('?' * len(materials))

    def q(s, e):
        return pd.read_sql(f"""
            SELECT COUNT(*) AS 铺货台数,
                   ROUND(COALESCE(SUM(产品分销价),0)/10000.0,2) AS 铺货金额_万,
                   COUNT(DISTINCT 客户编码_下级) AS 覆盖下级服务商
              FROM distribution_info
             WHERE 物料号 IN ({ph}) AND substr(提交铺货时间,1,7) BETWEEN ? AND ?
        """, conn, params=[*materials, s, e]).iloc[0].to_dict()
    return {'当期': q(ps, pe), '同期': q(ys, ye)}


def factor_redpack(conn, sub, ps, pe, city=None):
    """安装红包(install_redpack_v):当期vs同期 上线台数/发放抽奖台数/中奖金额/覆盖服务商。"""
    ys, ye = shift_period(ps, pe, -12)
    cw, cp = ("", [])
    if city:
        cw, cp = (" AND 上线客户地市=?", [city])

    def q(s, e):
        return pd.read_sql(f"""
            SELECT COUNT(*) AS 上线台数,
                   SUM(CASE WHEN 中奖金额>0 THEN 1 ELSE 0 END) AS 中奖台数,
                   ROUND(COALESCE(SUM(CASE WHEN 中奖金额>0 THEN 中奖金额 ELSE 0 END),0),0) AS 中奖金额,
                   COUNT(DISTINCT 上线客户编码) AS 覆盖服务商
              FROM install_redpack_v
             WHERE COALESCE([产品子系列-新],产品子系列)=? AND 上线年月 BETWEEN ? AND ? {cw}
        """, conn, params=[sub, s, e, *cp]).iloc[0].to_dict()
    return {'当期': q(ps, pe), '同期': q(ys, ye)}


def factor_inventory(conn, sub, materials):
    """备货(inventory_snapshot,料号桥):最新盘库季度 在库台数/货值/平均库龄(存量快照)。"""
    if not materials:
        return {'说明': '无物料号映射,跳过备货'}
    ph = ','.join('?' * len(materials))
    row = pd.read_sql(f"""
        WITH latest AS (SELECT MAX(盘库季度) AS q FROM inventory_snapshot WHERE 盘库季度!='其他')
        SELECT (SELECT q FROM latest) AS 盘库季度,
               SUM(CASE WHEN 是否在库 IN ('是','1',1,'Y') THEN 1 ELSE 0 END) AS 在库台数,
               ROUND(COALESCE(SUM(CASE WHEN 是否在库 IN ('是','1',1,'Y') THEN 产品现有分销价 ELSE 0 END),0)/10000.0,2) AS 在库货值_万,
               ROUND(AVG(CASE WHEN 是否在库 IN ('是','1',1,'Y') THEN 库龄天数 END),0) AS 平均库龄天
          FROM inventory_snapshot
         WHERE 产品料号 IN ({ph}) AND 盘库季度=(SELECT q FROM latest)
    """, conn, params=[*materials]).iloc[0].to_dict()
    return row


def factor_provider_profile(conn, sub, ps, pe, city=None):
    """购买服务商画像+采购习惯(provider_contract):该子系列当期购买服务商(去马甲)的
    分类/竞品TOP/新签激活 + 交易频次/未交易天数。"""
    cw, cp = ("", [])
    if city:
        cw, cp = (" AND ir.上线客户地市=?", [city])
    codes = pd.read_sql(f"""
        SELECT DISTINCT ir.上线客户编码 AS 编码
          FROM install_redpack_v ir
          LEFT JOIN vest_account va ON va.服务商客户编码=ir.上线客户编码
         WHERE COALESCE(ir.[产品子系列-新],ir.产品子系列)=? AND ir.上线年月 BETWEEN ? AND ?
           AND va.服务商客户编码 IS NULL AND ir.上线客户编码 IS NOT NULL {cw}
    """, conn, params=[sub, ps, pe, *cp])['编码'].tolist()
    if not codes:
        return {'说明': '当期无购买服务商'}
    ph = ','.join('?' * len(codes))
    prof = pd.read_sql(f"""
        SELECT COUNT(*) AS 命中签约服务商,
               SUM(CASE WHEN [是否竞品TOP服务商（安防体量≥20W）]='Y' THEN 1 ELSE 0 END) AS 竞品TOP数,
               SUM(CASE WHEN 是否新签='Y' THEN 1 ELSE 0 END) AS 新签数,
               SUM(CASE WHEN 是否激活='Y' THEN 1 ELSE 0 END) AS 激活数,
               ROUND(AVG(本年总交易频次),1) AS 平均本年交易频次,
               ROUND(AVG(未交易天数),0) AS 平均未交易天数
          FROM provider_contract WHERE 客户编码 IN ({ph})
    """, conn, params=codes).iloc[0].to_dict()
    prof['购买服务商总数'] = len(codes)
    cls = pd.read_sql(f"""
        SELECT 客户分类, COUNT(*) AS n FROM provider_contract
         WHERE 客户编码 IN ({ph}) AND 客户分类 IS NOT NULL GROUP BY 1 ORDER BY n DESC
    """, conn, params=codes)
    prof['客户分类分布'] = dict(zip(cls['客户分类'].astype(str), cls['n'].astype(int)))
    return prof


# ══════════════════════ P1.5 因素:经营背景(区域级) ══════════════════════

def background_promotion(conn, ps, pe, city=None):
    """推广会(promotion_meeting_summary):当期vs同期 场次/签到公司/转化/券50解锁/会后红包/夜视王台数。"""
    ys, ye = shift_period(ps, pe, -12)
    cw, cp = ("", [])
    if city:
        cw, cp = (" AND 地市=?", [city])

    def q(s, e):
        return pd.read_sql(f"""
            SELECT COUNT(*) AS 场次,
                   COALESCE(SUM(签到公司数),0) AS 签到公司,
                   COALESCE(SUM(客户转化数量),0) AS 转化客户,
                   ROUND(COALESCE(SUM(券50_已解锁金额),0),0) AS 券50解锁金额,
                   ROUND(COALESCE(SUM(参会后安装红包金额),0),0) AS 会后红包金额,
                   COALESCE(SUM(夜视王上线台数),0) AS 夜视王上线台数
              FROM promotion_meeting_summary
             WHERE substr(活动开始时间,1,7) BETWEEN ? AND ? {cw}
        """, conn, params=[s, e, *cp]).iloc[0].to_dict()
    return {'当期': q(ps, pe), '同期': q(ys, ye)}


def background_visit(conn, ps, pe, city=None):
    """跑动(visit_record):当期vs同期 拜访次数/覆盖服务商/大华打卡占比。"""
    ys, ye = shift_period(ps, pe, -12)
    cw, cp = ("", [])
    if city:
        cw, cp = (" AND 拜访客户城市=?", [city])

    def q(s, e):
        return pd.read_sql(f"""
            SELECT COUNT(*) AS 拜访次数,
                   COUNT(DISTINCT 客户编码) AS 覆盖服务商,
                   SUM(CASE WHEN 打卡人所属公司 IS NULL OR 打卡人所属公司='' THEN 1 ELSE 0 END) AS 大华打卡次数
              FROM visit_record
             WHERE substr(拜访时间,1,7) BETWEEN ? AND ? {cw}
        """, conn, params=[s, e, *cp]).iloc[0].to_dict()
    return {'当期': q(ps, pe), '同期': q(ys, ye)}


def background_salesredpack(conn, ps, pe):
    """业务员红包(dahua_redpack_quota,省级无地市):当期vs同期 使用金额/发放客户/转化激活/使用率/解锁率/带来上线金额。"""
    ys, ye = shift_period(ps, pe, -12)

    def q(s, e):
        return pd.read_sql(f"""
            SELECT ROUND(COALESCE(SUM([红包使用金额（元）]),0),0) AS 红包使用金额,
                   COALESCE(SUM(发放客户数),0) AS 发放客户数,
                   COALESCE(SUM([红包发放后30天内转化激活客户数]),0) AS 转化激活客户,
                   ROUND(AVG(红包配额使用率),3) AS 平均配额使用率,
                   ROUND(AVG(服务商红包解锁率),3) AS 平均解锁率,
                   ROUND(COALESCE(SUM([红包发放后30天内安装红包上线金额]),0),0) AS 带来上线金额
              FROM dahua_redpack_quota WHERE substr(时间,1,7) BETWEEN ? AND ?
        """, conn, params=[s, e]).iloc[0].to_dict()
    return {'当期': q(ps, pe), '同期': q(ys, ye)}


def background_storefront(conn, ps, pe, city=None):
    """门头(provider_storefront_invest,年度滞后):取最新可用年份(门头投入是年度数据,
    当期年常未录入,现库内仅 2025)投入服务商/投入额/激活率/带来上线金额(ROI)。"""
    yr_row = pd.read_sql("SELECT MAX(投入年份) AS y FROM provider_storefront_invest", conn)
    yv = yr_row['y'].iloc[0]
    yr = str(int(yv)) if yv is not None else ps[:4]
    cw, cp = ("", [])
    if city:
        cw, cp = (" AND 地市=?", [city])
    row = pd.read_sql(f"""
        SELECT COUNT(*) AS 投入服务商,
               ROUND(COALESCE(SUM(投入金额_元),0)/10000.0,2) AS 投入金额_万,
               SUM(CASE WHEN 是否激活='Y' THEN 1 ELSE 0 END) AS 激活数,
               ROUND(COALESCE(SUM(当年上线金额_元),0)/10000.0,2) AS 带来上线金额_万
          FROM provider_storefront_invest
         WHERE CAST(投入年份 AS TEXT)=? {cw}
    """, conn, params=[yr, *cp]).iloc[0].to_dict()
    return {'年份': yr, **row}


# ══════════════════════ 组装 ══════════════════════

def build(period_start, period_end, city=None, top_n=5, bottom_n=5, min_wan=5.0, with_factors=True):
    """组装 attribution.json(识别+下钻 + 九因素;归因键 P2 由沙箱 LLM 写)。"""
    conn = sqlite3.connect(str(DB))
    try:
        ys, ye = shift_period(period_start, period_end, -12)
        n = n_months(period_start, period_end)
        ms, me = shift_period(period_start, period_end, -n)
        df, good, bad = identify_subseries(conn, period_start, period_end, city,
                                           top_n, bottom_n, min_wan)
        out = {
            '_meta': {
                'city': city or '全省',
                'period_start': period_start, 'period_end': period_end,
                'yoy_start': ys, 'yoy_end': ye, 'mom_start': ms, 'mom_end': me,
                'n_months': n,
                'top_n': top_n, 'bottom_n': bottom_n, 'min_货值万': min_wan,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                '口径提示': (
                    '城市过滤双口径:识别+地市/区县/代理商下钻按 product_flow「上线城市」(设备点亮地);'
                    '服务商下钻+安装红包/服务商画像因素按 install_redpack「上线客户地市」(服务商注册地)。'
                    '同城市切片下二者是不同设备集,勿把识别SO与红包因素逐台硬对账,只看趋势方向。'
                ) if city else '全省口径,无城市过滤差异。',
            },
            '全量子系列': df.where(lambda x: pd.notnull(x), None).to_dict('records'),
            '亮点子系列': [],
            '问题子系列': [],
        }
        if with_factors:
            out['经营背景'] = {
                '推广会': background_promotion(conn, period_start, period_end, city),
                '跑动': background_visit(conn, period_start, period_end, city),
                '业务员红包': background_salesredpack(conn, period_start, period_end),
                '门头': background_storefront(conn, period_start, period_end, city),
            }
        for tag, sub_df in [('亮点子系列', good), ('问题子系列', bad)]:
            for _, r in sub_df.iterrows():
                sub = r['子系列']
                rec = {
                    '子系列': sub,
                    '货值_万': r['货值_万'], '台数': int(r['台数']),
                    '同期_万': r['同期_万'], '增量_万': r['增量_万'], '同比': r['同比'],
                    '环期_万': r['环期_万'], '环比': r['环比'],
                    '下钻': drill_subseries(conn, sub, period_start, period_end, city),
                    '因素': None,
                    '归因': None,   # P2: 沙箱 LLM 写归因/归咎+佐证
                }
                if with_factors:
                    mats = _subseries_materials(conn, sub)
                    rec['因素'] = {
                        '铺货': factor_distribution(conn, sub, period_start, period_end, mats),
                        '安装红包': factor_redpack(conn, sub, period_start, period_end, city),
                        '备货': factor_inventory(conn, sub, mats),
                        '服务商画像': factor_provider_profile(conn, sub, period_start, period_end, city),
                    }
                out[tag].append(rec)
        return _jsonify(out)
    finally:
        conn.close()


def _fmt(d, *keys):
    """从 dict 取多个键拼短串,缺失显示 -。"""
    if not isinstance(d, dict):
        return str(d)
    return '  '.join(f"{k}={d.get(k)}" for k in keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True, help='起始年月 如 2026-01')
    ap.add_argument('--end', required=True, help='结束年月 如 2026-04')
    ap.add_argument('--city', default=None, help='限定地市(默认全省)')
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--bottom', type=int, default=5)
    ap.add_argument('--min-wan', type=float, default=5.0)
    ap.add_argument('--no-factors', action='store_true', help='只识别,不取九因素(快速看切片)')
    ap.add_argument('--json', action='store_true', help='输出完整 JSON 而非摘要')
    a = ap.parse_args()
    rep = build(a.start, a.end, a.city, a.top, a.bottom, a.min_wan, with_factors=not a.no_factors)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return
    m = rep['_meta']
    print(f"\n=== 产品归因 dry-run | {m['city']} | {m['period_start']}~{m['period_end']} "
          f"(同期 {m['yoy_start']}~{m['yoy_end']} / 环期 {m['mom_start']}~{m['mom_end']}) ===")
    if '经营背景' in rep:
        bg = rep['经营背景']
        print("\n── 经营背景(区域级,当期 vs 同期)──")
        pm = bg['推广会']
        print(f"  推广会: 当期 {_fmt(pm['当期'],'场次','签到公司','转化客户','券50解锁金额','夜视王上线台数')}")
        vs = bg['跑动']
        print(f"  跑动:   当期 {_fmt(vs['当期'],'拜访次数','覆盖服务商','大华打卡次数')}")
        sr = bg['业务员红包']
        print(f"  业务员红包: 当期 {_fmt(sr['当期'],'红包使用金额','发放客户数','转化激活客户','平均解锁率')}")
        sf = bg['门头']
        print(f"  门头({sf['年份']}年): {_fmt(sf,'投入服务商','投入金额_万','激活数','带来上线金额_万')}")
    for tag in ['亮点子系列', '问题子系列']:
        print(f"\n── {tag} ──")
        for s in rep[tag]:
            tb = '↑' if (s['同比'] or 0) >= 0 else '↓'
            hb = '↑' if (s['环比'] or 0) >= 0 else '↓'
            print(f"  {s['子系列']:<14} 货值 {s['货值_万']:>8} 万  增量 {s['增量_万']:>+9} 万  "
                  f"同比 {tb}{abs((s['同比'] or 0))*100:>5.1f}%  环比 {hb}{abs((s['环比'] or 0))*100:>5.1f}%  ({s['台数']}台)")
            f = s.get('因素')
            if f:
                pu = f['铺货'].get('当期') if isinstance(f['铺货'], dict) else None
                rp = f['安装红包'].get('当期') if isinstance(f['安装红包'], dict) else None
                iv = f['备货'] if isinstance(f['备货'], dict) else None
                pp = f['服务商画像'] if isinstance(f['服务商画像'], dict) else None
                if pu:
                    print(f"      铺货: {_fmt(pu,'铺货台数','铺货金额_万','覆盖下级服务商')}")
                if rp:
                    print(f"      安装红包: {_fmt(rp,'上线台数','中奖台数','中奖金额','覆盖服务商')}")
                if iv:
                    print(f"      备货({iv.get('盘库季度')}): {_fmt(iv,'在库台数','在库货值_万','平均库龄天')}")
                if pp:
                    print(f"      服务商画像: {_fmt(pp,'购买服务商总数','竞品TOP数','新签数','激活数','平均本年交易频次')}")


if __name__ == '__main__':
    main()
