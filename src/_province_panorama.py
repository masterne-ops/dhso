"""全省全景 — 数据层。

gather_province  : 经营总览(总盘 + 月度SO趋势 + 11地市对比)
province_health  : 渠道健康度(进出水 / V0-V5 / 净流失 / 激活率),支持 cert_only(只看认证SMB服务商)

口径:
  - SI 总金额 = dealer_si_snapshot 最新时点 累计业绩达成（计任务）
  - SO 金额/台数 = product_flow_v 全量感知(当期)
  - 服务商总数 + V0-V5 = provider_contract(官方"服务商等级"字段)
  - 认证SMB服务商 = provider_contract.渠道客户类型 = '认证SMB服务商'
  - 渠道健康度(进/出水) = install_redpack 活跃口径:基线 2025-01 → 评估期前一月 vs 评估期
"""
import pandas as pd

from _provider_coverage import authorized_provider_by_city, authorized_provider_metrics

TIER_ORDER = ['v0服务商', 'v1服务商', 'v2服务商', 'v3服务商', 'v4服务商', 'v5服务商']
TIER_LABEL = {f'v{i}服务商': f'V{i}' for i in range(6)}
VCOLS = ['V0', 'V1', 'V2', 'V3', 'V4', 'V5']
CERT_TYPE = '认证SMB服务商'


def gather_province(conn, period_start, period_end):
    year = int(period_start.split('-')[0])
    months = pd.period_range(period_start, period_end, freq='M')
    month_nums = [m.month for m in months]
    mn_ph = ','.join('?' * len(month_nums))
    end_mn = int(period_end.split('-')[1])
    ytd_mns = list(range(1, end_mn + 1))
    ytd_ph = ','.join('?' * len(ytd_mns))

    def one(sql, p=()):
        return pd.read_sql(sql, conn, params=p).iloc[0]

    si_period = one("SELECT MAX(数据时点) v FROM dealer_si_snapshot")['v']
    si_amt = one('SELECT COALESCE(SUM("累计业绩达成（计任务）"),0) v FROM dealer_si_snapshot WHERE 数据时点=?',
                 (si_period,))['v']
    si_should = one('SELECT COALESCE(SUM("累计任务"),0) v FROM dealer_si_snapshot WHERE 数据时点=?',
                    (si_period,))['v']
    so = one("SELECT COALESCE(SUM(最新分销价)/10000,0) 万, COUNT(*) 台 "
             "FROM product_flow_v WHERE 上线年月 BETWEEN ? AND ?", (period_start, period_end))
    rp_n = one("SELECT COUNT(*) v FROM install_redpack_v WHERE 上线年月 BETWEEN ? AND ?",
               (period_start, period_end))['v']
    so_n = int(so['台'])
    ytd_should = one(f"SELECT COALESCE(SUM(t.SO目标_万*r.占比),0) v FROM kpi_targets t "
                     f"JOIN kpi_rhythm r ON r.年度=t.年度 WHERE t.年度=? "
                     f"AND r.指标 LIKE '省区SO进度条%' AND r.月份 IN ({ytd_ph})", (year, *ytd_mns))['v']
    ytd_act = one("SELECT COALESCE(SUM(最新分销价)/10000,0) v FROM product_flow_v WHERE 上线年月 BETWEEN ? AND ?",
                  (f'{year}-01', period_end))['v']
    tiers = pd.read_sql("SELECT 服务商等级 等级, COUNT(*) 数量 FROM provider_contract "
                        "WHERE 服务商等级 IS NOT NULL GROUP BY 1", conn)
    tmap = dict(zip(tiers['等级'], tiers['数量']))
    prov_total = int(one("SELECT COUNT(DISTINCT 客户编码) v FROM provider_contract")['v'])
    tier_dist = []
    for t in TIER_ORDER:
        n = int(tmap.get(t, 0))
        tier_dist.append({'等级': TIER_LABEL[t], '数量': n,
                          '占比': round(n / prov_total, 4) if prov_total else 0})
    # 授牌服务商 = 无价值客户(管理标签口径,见 _provider_flags),单独筛出展示
    worth_total = int(one("SELECT COUNT(*) v FROM provider_contract WHERE 管理标签='授牌服务商'")['v'])

    trend = pd.read_sql("SELECT 上线年月 月, COUNT(*) 台数, "
                        "ROUND(SUM(最新分销价)/10000,1) 金额_万 FROM product_flow_v "
                        "WHERE 上线年月 >= '2025-01' AND 上线年月 IS NOT NULL AND 上线年月 != '' "
                        "GROUP BY 1 ORDER BY 1", conn)

    c_so = pd.read_sql("SELECT 上线城市 城市, ROUND(SUM(最新分销价)/10000,1) SO实际万, COUNT(*) SO台数 "
                       "FROM product_flow_v WHERE 上线年月 BETWEEN ? AND ? AND 上线城市 LIKE '%市%' GROUP BY 1",
                       conn, params=(period_start, period_end))
    c_tgt = pd.read_sql(f"SELECT t.城市, ROUND(SUM(t.SO目标_万*r.占比),1) SO目标万 FROM kpi_targets t "
                        f"JOIN kpi_rhythm r ON r.年度=t.年度 WHERE t.年度=? "
                        f"AND r.指标 LIKE '省区SO进度条%' AND r.月份 IN ({mn_ph}) GROUP BY t.城市",
                        conn, params=(year, *month_nums))
    c_tier = pd.read_sql("SELECT 客户城市 城市, 服务商等级 等级, COUNT(*) 数量 FROM provider_contract "
                         "WHERE 客户城市 LIKE '%市%' AND 服务商等级 IS NOT NULL GROUP BY 1, 2", conn)
    c_worth = pd.read_sql("SELECT 客户城市 城市, COUNT(*) 授牌数 FROM provider_contract "
                          "WHERE 客户城市 LIKE '%市%' AND 管理标签='授牌服务商' GROUP BY 1", conn)
    if not c_tier.empty:
        c_tier['等级'] = c_tier['等级'].map(TIER_LABEL).fillna(c_tier['等级'])
        tier_pivot = c_tier.pivot_table(index='城市', columns='等级', values='数量', fill_value=0).reset_index()
    else:
        tier_pivot = pd.DataFrame({'城市': []})
    city = (c_so.merge(c_tgt, on='城市', how='outer').merge(tier_pivot, on='城市', how='outer')
                .merge(c_worth, on='城市', how='outer'))
    city = city.merge(pd.DataFrame(authorized_provider_by_city(conn)), on='城市', how='outer')
    city = city[city['城市'].notna() & city['城市'].astype(str).str.contains('市')]
    for col in ['SO实际万', 'SO台数', 'SO目标万', '授牌数'] + VCOLS:
        if col not in city.columns:
            city[col] = 0
        city[col] = city[col].fillna(0)
    city['授牌数'] = city['授牌数'].astype(int)
    city['服务商数'] = city[VCOLS].sum(axis=1).astype(int)
    city['SO完成率'] = city.apply(
        lambda r: round(r['SO实际万'] / r['SO目标万'], 4) if r['SO目标万'] and r['SO目标万'] > 0 else None, axis=1)
    for c in VCOLS + ['SO台数']:
        city[c] = city[c].astype(int)
    city = city.sort_values('SO实际万', ascending=False)

    return {
        '_周期': f'{period_start} ~ {period_end}',
        '_SI时点': si_period,
        '总盘': {
            'SI总金额_万': round(float(si_amt), 1),
            'SI截止应达成_万': round(float(si_should), 1),
            'SI完成率': round(float(si_amt) / float(si_should), 4) if si_should and si_should > 0 else None,
            'SO总金额_万': round(float(so['万']), 1),
            'SO总台数': so_n,
            '安装红包数': int(rp_n),
            '红包获取率': round(rp_n / so_n, 4) if so_n > 0 else None,
            'YTD应达成_万': round(float(ytd_should), 1),
            'YTD实际_万': round(float(ytd_act), 1),
            'YTD完成率': round(float(ytd_act) / float(ytd_should), 4) if ytd_should and ytd_should > 0 else None,
            '服务商总数': prov_total,
            'V0_V5分布': tier_dist,
            '授牌服务商数': worth_total,
            '授权服务商覆盖': authorized_provider_metrics(conn),
        },
        '月度趋势': trend.to_dict('records'),
        '地市对比': city[['城市', 'SO目标万', 'SO实际万', 'SO完成率', 'SO台数',
                       '测算服务商数', '授权签约数', '授权渗透率', '授权激活数', '授权激活率',
                       '服务商数'] + VCOLS + ['授牌数']].to_dict('records'),
    }


def province_health(conn, period_start, period_end, cert_only=False):
    """渠道健康度。cert_only=True 时只看认证SMB服务商(渠道客户类型='认证SMB服务商')。"""
    cert_where = f" AND 渠道客户类型='{CERT_TYPE}'" if cert_only else ""
    cert_set = None
    if cert_only:
        cert_set = set(pd.read_sql(
            f"SELECT DISTINCT 客户编码 FROM provider_contract WHERE 客户编码 IS NOT NULL AND 渠道客户类型='{CERT_TYPE}'",
            conn)['客户编码'].astype(str))

    # 服务商总数 + V0-V5(provider_contract, 可选 cert 过滤)
    prov_total = int(pd.read_sql(
        f"SELECT COUNT(DISTINCT 客户编码) v FROM provider_contract WHERE 客户编码 IS NOT NULL{cert_where}",
        conn).iloc[0]['v'])
    tiers = pd.read_sql(
        f"SELECT 服务商等级 等级, COUNT(*) 数量 FROM provider_contract WHERE 服务商等级 IS NOT NULL{cert_where} GROUP BY 1",
        conn)
    tmap = dict(zip(tiers['等级'], tiers['数量']))
    v2plus_total = sum(int(tmap.get(t, 0)) for t in ['v2服务商', 'v3服务商', 'v4服务商', 'v5服务商'])

    # 11地市等级
    c_tier = pd.read_sql(
        f"SELECT 客户城市 城市, 服务商等级 等级, COUNT(*) 数量 FROM provider_contract "
        f"WHERE 客户城市 LIKE '%市%' AND 服务商等级 IS NOT NULL{cert_where} GROUP BY 1, 2", conn)
    city_tier = {}
    for _, r in c_tier.iterrows():
        v = TIER_LABEL.get(r['等级'], r['等级'])
        d = city_tier.setdefault(r['城市'], {x: 0 for x in VCOLS})
        d[v] = int(r['数量'])

    # 活跃/基线(install_redpack, 可选 ∩ cert_set)
    base_end = str(pd.Period(period_start, freq='M') - 1)
    ev = pd.read_sql("SELECT DISTINCT 上线客户编码 编码, 上线客户地市 地市 FROM install_redpack_v "
                     "WHERE 上线年月 BETWEEN ? AND ? AND 上线客户编码 IS NOT NULL AND 上线客户编码!=''",
                     conn, params=(period_start, period_end))
    ba = pd.read_sql("SELECT DISTINCT 上线客户编码 编码, 上线客户地市 地市 FROM install_redpack_v "
                     "WHERE 上线年月 BETWEEN ? AND ? AND 上线客户编码 IS NOT NULL AND 上线客户编码!=''",
                     conn, params=('2025-01', base_end))
    if cert_only and cert_set is not None:
        ev = ev[ev['编码'].astype(str).isin(cert_set)]
        ba = ba[ba['编码'].astype(str).isin(cert_set)]
    ev_set, ba_set = set(ev['编码'].astype(str)), set(ba['编码'].astype(str))
    _new, _lost = len(ev_set - ba_set), len(ba_set - ev_set)

    health = {
        '基线期': f'2025-01 ~ {base_end}',
        '服务商总数': prov_total,
        '评估期活跃': len(ev_set), '基线期活跃': len(ba_set),
        '新增': _new, '流失': _lost, '净增': _new - _lost, '净流失': _lost - _new,
        '激活率': round(v2plus_total / prov_total, 4) if prov_total else None,
        'cert_only': cert_only,
    }

    evc = ev[ev['地市'].astype(str).str.contains('市', na=False)]
    bac = ba[ba['地市'].astype(str).str.contains('市', na=False)]
    ev_city = {k: set(g.astype(str)) for k, g in evc.groupby('地市')['编码']}
    ba_city = {k: set(g.astype(str)) for k, g in bac.groupby('地市')['编码']}
    ch_rows = []
    for c in sorted(set(ev_city) | set(ba_city) | set(city_tier)):
        if '市' not in str(c):
            continue
        e, b = ev_city.get(c, set()), ba_city.get(c, set())
        ct = city_tier.get(c, {x: 0 for x in VCOLS})
        _n, _l = len(e - b), len(b - e)
        sn = sum(ct.get(x, 0) for x in VCOLS)
        v2p = ct.get('V2', 0) + ct.get('V3', 0) + ct.get('V4', 0) + ct.get('V5', 0)
        row = {'城市': c, '活跃数': len(e), '新增': _n, '流失': _l, '净流失': _l - _n,
               '服务商数': sn, '激活率': round(v2p / sn, 4) if sn > 0 else None}
        for v in VCOLS:
            row[v] = ct.get(v, 0)
        ch_rows.append(row)
    ch_rows.sort(key=lambda r: -r['活跃数'])

    return {'渠道健康度': health, '地市健康度': ch_rows}
