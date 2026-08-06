# -*- coding: utf-8 -*-
"""代理商作战方案生成器 · 后端

输入：代理商名 + 目标月 + 年度目标参数 → 产出 目标全景 / 逐月SO·SI / 当月目标(基准+挑战) / 激励方案(挑战值机制)。

方法论：月度SO作战方案/代理商目标分解与激励方法论-v1.md
口径：
  · SO = product_flow 上线·最新分销价（总盘基准）
  · 服务商指标 = 严口径（设备「上线」∩「扫码领红包」双双当月）+ 剔马甲（vest_account.对应一级）
  · 成交分档 = 累计成交额（V2≥0.1万 / V3≥1万 / V4≥3万）
"""
from __future__ import annotations
import sqlite3
from collections import Counter, defaultdict

PRICE_DEFAULT = 180.0          # 元/台
SO_RHYTHM = '全国'              # kpi_rhythm 适用范围
SI_RHYTHM = '分销商协议'

# 各维度激励权重（缺口×杠杆，默认值，可调）
DEFAULT_WEIGHTS = {
    '留存': 0.27, '激活': 0.22, '升档': 0.16, '新签': 0.09,
    '无线': 0.08, '夜视王': 0.05, '场景化': 0.06, 'base-load': 0.05,
}


# ───────────────────────── 基础 ─────────────────────────
def _vest(conn, dealer):
    vc, vn = set(), set()
    for code, nm in conn.execute(
        "SELECT 服务商客户编码,服务商客户名称 FROM vest_account WHERE 对应一级 LIKE ?",
        (f'%{dealer}%',)):
        if code: vc.add(code)
        if nm: vn.add(nm.strip())
    return vc, vn


def load_rhythm(conn, scope, year=2026):
    rows = conn.execute("SELECT 月份,占比 FROM kpi_rhythm WHERE 适用范围=? AND 年度=? ORDER BY 月份",
                        (scope, year)).fetchall()
    if not rows:
        rows = conn.execute("SELECT 月份,占比 FROM kpi_rhythm WHERE 适用范围=? ORDER BY 月份", (scope,)).fetchall()
    return {int(m): float(p or 0) for m, p in rows}


def list_dealers(conn, min_units=1000, year=2026):
    """可选代理商（按 SO 台数，倒序）"""
    ys = f"{year}-01"
    rows = conn.execute(
        "SELECT 出库客户名称, COUNT(*) n FROM product_flow_v WHERE 上线年月>=? "
        "GROUP BY 出库客户名称 HAVING n>=? ORDER BY n DESC", (ys, min_units)).fetchall()
    return [r[0] for r in rows]


def _tier(amt):
    amt = amt or 0
    if amt >= 30000: return 'V4'
    if amt >= 10000: return 'V3'
    if amt >= 1000:  return 'V2'
    if amt > 0:      return 'V1'
    return 'V0'


def _ramp(cur, tgt, month, start_month=6, end_month=12):
    """线性爬坡：从现状 cur 渐进到年底 tgt。返回 month 当月目标。
    当月目标 = cur + (tgt-cur)×(月序/总月数)，绝不在首月就要求年底值。
    例：start=6,end=12 → 6月走 1/7，12月走 7/7=年底值。"""
    span = end_month - (start_month - 1)
    if span <= 0:
        return tgt
    pos = max(0, min(span, month - (start_month - 1)))
    return cur + (tgt - cur) * pos / span


# 里程碑爬坡曲线（默认 6月30% / 9月50% / 12月100%，分段线性插值）—— 用户设定的达成节奏
MILESTONES = ((6, 0.30), (9, 0.50), (12, 1.00))


def _curve(m, milestones=MILESTONES):
    """返回累计完成比例(0..1)。m<首月里程碑=0，m≥末里程碑=1。"""
    pts = list(milestones)
    if m < pts[0][0]:
        return 0.0
    if m >= pts[-1][0]:
        return pts[-1][1]
    for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
        if m <= m1:
            return p0 + (p1 - p0) * (m - m0) / (m1 - m0)
    return pts[-1][1]


# ───────────────────────── 诊断（当前状态） ─────────────────────────
def diagnose(conn, dealer, target_month, year=2026):
    """返回当前状态：分档(剔马甲) / 品类占比 / 月月动销 / 当月激活近月 / YTD SO。
    dealer 可为单个名称(str) 或 多家名称(list)——传 list 即按组合并(IN)，跨家服务商按总成交额去重分档。"""
    A = '产品现有分销价'
    dealers = [dealer] if isinstance(dealer, str) else list(dealer)
    ph = ','.join('?' * len(dealers))
    vc, vn = set(), set()
    for dl in dealers:
        c1, c2 = _vest(conn, dl); vc |= c1; vn |= c2
    is_v = lambda code, nm: (code in vc) or ((nm or '').strip() in vn)
    recent = [f"{year}-{m:02d}" for m in range(max(1, target_month - 3), target_month)]  # 近3完整月
    ys = f"{year}-01"; ub = f"{year}-{target_month:02d}"
    rp = ','.join('?' * len(recent))

    # 成交分档（剔马甲，累计成交额；跨家按 上线客户编码 合并去重）
    rows = conn.execute(
        f'SELECT 上线客户编码 code,上线客户名称 nm,SUM("{A}") amt '
        f'FROM install_redpack_v WHERE 出货客户名称 IN ({ph}) AND 上线年月>=? GROUP BY 上线客户编码',
        (*dealers, ys)).fetchall()
    tier_cnt = Counter()
    real_amt = 0.0
    for r in rows:
        if is_v(r['code'], r['nm']): continue
        tier_cnt[_tier(r['amt'])] += 1; real_amt += (r['amt'] or 0)

    # base-load现状 = 真实渠道(剔马甲) 完整月月均
    bl_amt = 0.0
    for r in conn.execute(
        f'SELECT 上线客户编码 code,上线客户名称 nm,SUM("{A}") amt FROM install_redpack_v '
        f'WHERE 出货客户名称 IN ({ph}) AND 上线年月>=? AND 上线年月<? GROUP BY 上线客户编码',
        (*dealers, ys, ub)).fetchall():
        if is_v(r['code'], r['nm']): continue
        bl_amt += (r['amt'] or 0)
    baseload_now = round(bl_amt / 10000 / max(1, target_month - 1), 1)

    # 月月动销（近3月连续，剔马甲）
    sp_months = defaultdict(set)
    for r in conn.execute(
        f'SELECT 上线客户编码 code,上线客户名称 nm,substr(上线时间,1,7) m '
        f'FROM install_redpack_v WHERE 出货客户名称 IN ({ph}) AND substr(上线时间,1,7) IN ({rp}) '
        f'GROUP BY 上线客户编码,m', (*dealers, *recent)).fetchall():
        if is_v(r['code'], r['nm']): continue
        sp_months[r['code']].add(r['m'])
    loyal = sum(1 for v in sp_months.values() if len(v) == len(recent))

    # 当月激活（严口径：上线月==扫码月，剔马甲）近月均值
    act_by_m = defaultdict(set)
    for r in conn.execute(
        f'SELECT 上线客户编码 code,上线客户名称 nm,substr(上线时间,1,7) lm,substr(抽奖机会发放时间,1,7) sm '
        f'FROM install_redpack_v WHERE 出货客户名称 IN ({ph}) AND substr(上线时间,1,7) IN ({rp})',
        (*dealers, *recent)).fetchall():
        if is_v(r['code'], r['nm']): continue
        if r['lm'] and r['lm'] == r['sm']: act_by_m[r['lm']].add(r['code'])
    act_recent = round(sum(len(v) for v in act_by_m.values()) / max(1, len(recent)))

    # YTD SO（产品流向，出库 IN 组，仅目标月之前的完整月）+ 品类占比
    so = conn.execute(
        f"SELECT COUNT(*) tai, COALESCE(SUM(最新分销价),0) amt FROM product_flow_v "
        f"WHERE 出库客户名称 IN ({ph}) AND 上线年月>=? AND 上线年月<?", (*dealers, ys, ub)).fetchone()
    cat = {}
    crow = conn.execute(
        f"SELECT 三大重点专项 c, COUNT(*) tai, COALESCE(SUM(最新分销价),0) amt FROM product_flow_v "
        f"WHERE 出库客户名称 IN ({ph}) AND 上线年月>=? AND 上线年月<? GROUP BY 三大重点专项", (*dealers, ys, ub)).fetchall()
    tot_t = so['tai'] or 1; tot_a = so['amt'] or 1
    name_map = {'无线专项': '无线', '夜视王专项': '夜视王', '场景化专项': '场景化'}
    for r in crow:
        k = name_map.get(r['c'])
        if k: cat[k] = {'台占比': r['tai'] / tot_t, '额占比': r['amt'] / tot_a, '台': r['tai']}

    return {
        'tiers': {t: tier_cnt.get(t, 0) for t in ['V2', 'V3', 'V4']},
        'tier_active': sum(tier_cnt.values()),
        'real_amt_wan': round(real_amt / 10000, 1),
        'baseload_now_wan': baseload_now,  # 真实渠道(剔马甲) 完整月月均 = base-load现状
        'loyal_recent': loyal,
        'act_recent': act_recent,
        'ytd_so_wan': round((so['amt'] or 0) / 10000, 1),
        'ytd_so_units': so['tai'] or 0,
        'cat': cat,
        'vest_n': len(vc),
        'recent_months': recent,
    }


# ───────────────────────── 方案生成 ─────────────────────────
def recommend_targets(conn, dealer, year=2026):
    """按官方政策为任意代理商推荐年度目标参数（供生成器换代理商时自动预填）。
    SI=签约金额 / SO=签约值×0.7 / V档=现状×1.5 / 月月动销=活跃×10% / 品类=补到全省均值。"""
    so_r = load_rhythm(conn, SO_RHYTHM, year)
    dealers = [dealer] if isinstance(dealer, str) else list(dealer)
    ph = ','.join('?' * len(dealers))
    mx = conn.execute(f"SELECT MAX(上线年月) FROM product_flow_v WHERE 出库客户名称 IN ({ph}) AND 上线年月>=?",
                      (*dealers, f'{year}-01')).fetchone()[0]
    ref_m = int(str(mx)[5:7]) if mx else 6
    last_full = max(1, ref_m - 1)                          # 最新月多半不全，取前一月为最后完整月
    d = diagnose(conn, dealer, last_full + 1, year)
    frac = sum(so_r.get(mn, 0) for mn in range(1, last_full + 1)) or 0.34
    # 官方政策：签约值 = dealer_si_snapshot.签约金额（组=各家求和）；SO年目标 = 签约值 × 0.7
    sk_sum = 0.0
    for dl in dealers:
        r = conn.execute('SELECT MAX(签约金额) FROM dealer_si_snapshot WHERE "客户名称（无@办事处）"=? AND 签约金额>0', (dl,)).fetchone()
        if r and r[0]: sk_sum += float(r[0])
    if sk_sum > 0:
        si_year = int(round(sk_sum))
        so_year = int(round(si_year * 0.7))
    else:                                   # 签约值缺失 → 退回年化估算
        so_year = max(50, round(d['ytd_so_wan'] / frac / 10) * 10) if d['ytd_so_wan'] else 100
        si_year = round(so_year * 1.4 / 10) * 10
    t = d['tiers']
    v2, v3, v4 = max(t['V2'], round(t['V2'] * 1.5)), max(t['V3'], round(t['V3'] * 1.5)), max(t['V4'], round(t['V4'] * 1.5))
    loyal = max(5, round(d['tier_active'] * 0.10))
    pt = conn.execute("SELECT COUNT(*) FROM product_flow_v WHERE 上线年月>=?", (f'{year}-01',)).fetchone()[0] or 1
    cat = {}
    for col, name in [('无线专项', '无线'), ('夜视王专项', '夜视王'), ('场景化专项', '场景化')]:
        p = conn.execute("SELECT COUNT(*) FROM product_flow_v WHERE 上线年月>=? AND 三大重点专项=?",
                         (f'{year}-01', col)).fetchone()[0]
        cur = d['cat'].get(name, {}).get('台占比', 0)
        cat[name] = max(1, round(max(cur, p / pt) * 100))
    return {'so_year': int(so_year), 'si_year': int(si_year), 'v2': int(v2), 'v3': int(v3), 'v4': int(v4),
            'loyal': int(loyal), 'wireless': cat['无线'], 'nightking': cat['夜视王'], 'scenario': cat['场景化'],
            'ytd_so': d['ytd_so_wan'], 'tiers_now': t, 'active_now': d['tier_active']}


def build_plan(conn, dealer, target_month, *, year=2026,
               so_year_wan, si_year_wan, price=PRICE_DEFAULT,
               so_challenge_factor=1.08, challenge_mult=1.5, challenge_pool_wan=1.0,
               tier_year=None, loyal_year=15, baseload_year_wan=50.0, act_year=None,
               cat_year=None, weights=None, label=None):
    """生成完整作战方案。tier_year={'V2':150,'V3':50,'V4':20}; cat_year={'无线':0.10,...}
    act_year=年底「当月激活」run-rate目标(家/月)；None则维持现状跑速(不爬坡)。"""
    tier_year = tier_year or {'V2': 150, 'V3': 50, 'V4': 20}
    cat_year = cat_year or {'无线': 0.10, '夜视王': 0.06, '场景化': 0.08}
    weights = weights or DEFAULT_WEIGHTS
    d = diagnose(conn, dealer, target_month, year)
    if act_year is None:
        act_year = d['act_recent']                 # 默认维持现状跑速
    so_r = load_rhythm(conn, SO_RHYTHM, year)
    si_r = load_rhythm(conn, SI_RHYTHM, year)
    rem = list(range(target_month, 13))                       # 目标月..12
    rem_frac = sum(so_r.get(m, 0) for m in rem) or 1
    n_rem = len(rem)

    # 逐月 SO / SI
    monthly = []
    for m in range(target_month, 13):
        so_w = so_year_wan * so_r.get(m, 0)
        monthly.append({'month': m, 'rhythm': so_r.get(m, 0), 'so_wan': round(so_w, 1),
                        'so_units': int(round(so_w * 10000 / price)),
                        'si_wan': round(si_year_wan * si_r.get(m, 0), 1)})

    # ── 当月 SO/SI：流量，按节奏分配（非爬坡）──
    tm_so = so_year_wan * so_r.get(target_month, 0)
    tm_units = tm_so * 10000 / price
    sf = so_challenge_factor

    # ── 服务商漏斗：激活(新V2)→V2→V3→V4，反推年度流量以达成存量目标（同一条 _curve 驱动三档一致）──
    up_v4 = max(0, tier_year['V4'] - d['tiers']['V4'])               # V3→V4 升档
    up_v3 = max(0, tier_year['V3'] - d['tiers']['V3']) + up_v4       # V2→V3（含回填被升走的V4坑）
    act_total = max(0, tier_year['V2'] - d['tiers']['V2']) + up_v3   # 激活=新V2（含回填被升走的V3坑）

    def v2_at(m): return round(d['tiers']['V2'] + (act_total - up_v3) * _curve(m))
    def v3_at(m): return round(d['tiers']['V3'] + (up_v3 - up_v4) * _curve(m))
    def v4_at(m): return round(d['tiers']['V4'] + up_v4 * _curve(m))
    def act_at(m): return round(act_total * (_curve(m) - _curve(m - 1)))          # 当月新增V2
    def loyal_at(m): return max(d['loyal_recent'], round(d['loyal_recent'] + (loyal_year - d['loyal_recent']) * _curve(m)))
    def sh_at(k, m): return d['cat'].get(k, {}).get('台占比', 0) + (cat_year[k] - d['cat'].get(k, {}).get('台占比', 0)) * _curve(m)

    act_m, v2_m, v3_m, v4_m = act_at(target_month), v2_at(target_month), v3_at(target_month), v4_at(target_month)
    loyal_m = loyal_at(target_month)
    pct = _curve(target_month) * 100
    cdelta = _curve(target_month) - _curve(target_month - 1)
    up_v3_this, up_v4_this = round(up_v3 * cdelta), round(up_v4 * cdelta)
    up_this = max(1, up_v3_this + up_v4_this)                       # 本月升档户
    cat_u = {k: int(round(tm_units * sh_at(k, target_month))) for k in ['无线', '夜视王', '场景化']}

    # month_targets：tgt=实际目标默认值（=当月目标量）；key=与激励基数联动的键
    month_targets = [
        {'dim': '总量', 'ind': 'SO', 'base': f"{round(tm_so,1)}万 / {int(round(tm_units))}台",
         'chal': f"{round(tm_so*sf,1)}万 / {int(round(tm_units*sf))}台", 'tgt': int(round(tm_units)), 'key': 'SO总额', 'cal': '产品流向·台数（按节奏，@{}元/台）'.format(int(price))},
        {'dim': '总量', 'ind': 'SI', 'base': f"~{round(si_year_wan*si_r.get(target_month,0),1)}万",
         'chal': f"~{round(si_year_wan*si_r.get(target_month,0)*sf,1)}万", 'tgt': round(si_year_wan*si_r.get(target_month,0), 1), 'key': '', 'cal': '∝SO（按节奏）'},
        {'dim': '激活', 'ind': '当月新增V2服务商', 'base': f"{act_m}家", 'chal': f"{round(act_m*1.15)}家",
         'tgt': act_m, 'key': '激活', 'cal': f'漏斗·剔马甲（全年{act_total}家，里程碑{pct:.0f}%）'},
        {'dim': '留存', 'ind': '月月动销(近3月连续)', 'base': f"{loyal_m}家", 'chal': f"{loyal_m+1}家",
         'tgt': loyal_m, 'key': '留存', 'cal': f'剔马甲（现状{d["loyal_recent"]}→年底{loyal_year}）'},
        {'dim': '升档', 'ind': '本月升档(跨V3/跨V4)', 'base': f"{up_v3_this}跨V3+{up_v4_this}跨V4={up_this}户",
         'chal': f"{up_this+2}户", 'tgt': up_this, 'key': '升档', 'cal': f'年底存量{tier_year["V2"]}/{tier_year["V3"]}/{tier_year["V4"]}'},
    ]
    for k in ['无线', '夜视王', '场景化']:
        sh = sh_at(k, target_month); u = cat_u[k]
        month_targets.append({'dim': '品类', 'ind': k, 'base': f"{sh*100:.1f}% / ≥{u}台",
                              'chal': f"{int(round(u*1.15))}台", 'tgt': u, 'key': k, 'cal': f'专项标（爬坡至{cat_year[k]*100:.0f}%）'})

    # ── 逐月爬坡表（当月→12月，沿里程碑曲线，让渐进可见）──
    ramp = []
    for m in range(target_month, 13):
        u_m = so_year_wan * so_r.get(m, 0) * 10000 / price
        ramp.append({
            'month': m, 'so_units': int(round(u_m)),
            '激活': act_at(m), '月月动销': loyal_at(m),
            'V2': v2_at(m), 'V3': v3_at(m), 'V4': v4_at(m),
            '无线台': int(round(u_m * sh_at('无线', m))),
            '夜视王台': int(round(u_m * sh_at('夜视王', m))),
            '场景化台': int(round(u_m * sh_at('场景化', m))),
        })

    # 激励：基数(默认=当月实际目标量) × 激励单价 = 基准激励；挑战激励 = 基准×倍数
    inc_defs = [
        ('留存', '连续动销阶梯奖(连2/连3月)', loyal_m, '家', 360),
        ('激活', '新增V2首单返利', act_m, '家', 60),
        ('升档', '跳档奖(跨V3/跨V4)', up_this, '户', 110),
        ('无线', '无线专项上线奖', cat_u['无线'], '台', 4),
        ('夜视王', '夜视王专项上线奖', cat_u['夜视王'], '台', 3),
        ('场景化', '场景化专项上线奖', cat_u['场景化'], '台', 1.5),
        ('SO总额', 'SO总额达标返点(代理商·按台数)', int(round(tm_units)), '台', 0.1),
    ]
    inc_rows = []
    for dim, form, qty, unit, pr in inc_defs:
        base = round(qty * pr)
        inc_rows.append({'dim': dim, 'form': form, 'qty': qty, 'unit': unit, 'price': pr,
                         'base_amt': base, 'chal_amt': round(base * challenge_mult)})

    # 目标全景（现状→2026→2027翻倍；品类收敛10/10/10）
    overview = [
        {'dim': '总量', 'ind': '全年SO', 'cur': f"YTD {d['ytd_so_wan']}万", 'y26': f"{so_year_wan:.0f}万", 'y27': f"{so_year_wan*2:.0f}万"},
        {'dim': '总量', 'ind': '全年SI(∝SO)', 'cur': '—', 'y26': f"{si_year_wan:.0f}万", 'y27': f"{si_year_wan*2:.0f}万"},
        {'dim': '留存', 'ind': '月月动销服务商', 'cur': f"{d['loyal_recent']}", 'y26': f"{loyal_year}家", 'y27': f"{loyal_year*2}家"},
        {'dim': '结构', 'ind': '成交服务商V2/V3/V4', 'cur': f"{d['tiers']['V2']}/{d['tiers']['V3']}/{d['tiers']['V4']}",
         'y26': f"{tier_year['V2']}/{tier_year['V3']}/{tier_year['V4']}", 'y27': f"{tier_year['V2']*2}/{tier_year['V3']*2}/{tier_year['V4']*2}"},
        {'dim': '结构', 'ind': '激活=新增V2(全年)', 'cur': f"YTD约{d['tiers']['V2']}", 'y26': f"{act_total}家", 'y27': f"{act_total*2}家"},
    ]
    for k in ['无线', '夜视王', '场景化']:
        c = d['cat'].get(k, {}); cur = f"{c.get('台占比',0)*100:.1f}%"
        overview.append({'dim': '品类', 'ind': f'{k}占比', 'cur': cur, 'y26': f"{cat_year[k]*100:.0f}%", 'y27': '10%'})

    return {
        'dealer': label or (dealer if isinstance(dealer, str) else '代理商组'), 'month': target_month, 'year': year, 'price': price,
        'diag': d, 'monthly': monthly, 'month_targets': month_targets, 'ramp': ramp,
        'funnel': {'act_total': act_total, 'up_v3': up_v3, 'up_v4': up_v4},
        'incentive': {'so_base_wan': round(tm_so, 1), 'so_chal_wan': round(tm_so * so_challenge_factor, 1),
                      'mult': challenge_mult, 'rows': inc_rows,
                      'base_pool_wan': round(sum(r['base_amt'] for r in inc_rows) / 10000, 3),
                      'challenge_pool_wan': round(sum(r['chal_amt'] for r in inc_rows) / 10000, 3)},
        'overview': overview,
    }


# ───────────────────────── 导出 HTML ─────────────────────────
def render_html(plan):
    """把 build_plan 的结果渲染成可发的 HTML 文档。"""
    d = plan['diag']; inc = plan['incentive']; m = plan['month']
    esc = lambda x: str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    def rows_html(items, cols):
        return '\n'.join('<tr>' + ''.join(f'<td>{esc(it[c])}</td>' for c in cols) + '</tr>' for it in items)
    ov = '\n'.join(f"<tr><td class=l>{esc(o['dim'])}</td><td class=l>{esc(o['ind'])}</td><td>{esc(o['cur'])}</td><td class=big>{esc(o['y26'])}</td><td>{esc(o['y27'])}</td></tr>" for o in plan['overview'])
    mo = '\n'.join(f"<tr><td>{x['month']}月</td><td>{x['rhythm']*100:.1f}%</td><td class=big>{x['so_wan']}</td><td>{x['so_units']}</td><td>{x['si_wan']}</td></tr>" for x in plan['monthly'])
    rp = '\n'.join(f"<tr><td>{x['month']}月</td><td>{x['激活']}</td><td class=big>{x['月月动销']}</td><td>{x['V2']}/{x['V3']}/{x['V4']}</td><td>{x['无线台']}/{x['夜视王台']}/{x['场景化台']}</td></tr>" for x in plan['ramp'])
    rp_act6 = plan['ramp'][0]['激活'] if plan['ramp'] else 0
    mt = '\n'.join(f"<tr><td class=l>{esc(t['dim'])}</td><td class=l>{esc(t['ind'])}</td><td class=big>{esc(t['base'])}</td><td class=chal>{esc(t['chal'])}</td><td><input class=tg type=number step=any value={t['tgt']} data-k='{esc(t['key'])}'></td><td class=l>{esc(t['cal'])}</td></tr>" for t in plan['month_targets'])
    ir = '\n'.join(f"<tr><td class=l>{esc(r['dim'])}</td><td class=l>{esc(r['form'])}</td><td><input class=qt type=number value={r['qty']} data-k='{esc(r['dim'])}'> {esc(r['unit'])}</td><td><input class=pr type=number step=any value={r['price']}></td><td class=ba>{r['base_amt']}</td><td class=ca>{r['chal_amt']}</td></tr>" for r in inc['rows'])
    tot_b = sum(r['base_amt'] for r in inc['rows']); tot_c = sum(r['chal_amt'] for r in inc['rows'])
    return f"""<!DOCTYPE html><html lang=zh-CN><head><meta charset=UTF-8>
<title>{esc(plan['dealer'])} · {plan['year']}年{m}月作战方案</title><style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a2230;line-height:1.6;margin:0;background:#f6f8fb}}
.wrap{{max-width:980px;margin:0 auto;padding:30px 26px;background:#fff}}
h1{{font-size:23px;color:#1e5eb8;margin:0 0 4px}} .sub{{color:#5b6676;font-size:13px;margin-bottom:20px}}
h2{{font-size:17px;margin:26px 0 10px;padding-left:9px;border-left:4px solid #1e5eb8}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}} th,td{{border:1px solid #e3e8ef;padding:6px 8px;text-align:center}}
th{{background:#eef3fa;color:#28456f}} td.l,th.l{{text-align:left}} tr:nth-child(even) td{{background:#fafcff}}
.big{{font-weight:700;color:#1e5eb8}} .chal{{font-weight:700;color:#b9770e}}
.chalbox{{background:#fef6e7;border:1px solid #f0cf8a;border-radius:8px;padding:11px 13px;font-size:13px;margin:10px 0;color:#7a5612}}
.key{{background:#eef7f0;border:1px solid #bfe3cb;border-radius:8px;padding:11px 13px;font-size:13px;margin:10px 0;color:#1f5b38}}
.foot{{color:#5b6676;font-size:11.5px;margin-top:24px;border-top:1px solid #e3e8ef;padding-top:10px}}
input.qt{{width:52px}} input.pr{{width:62px}} input.tg{{width:82px;color:#b9770e!important}} input.qt,input.pr,input.tg{{font-size:13px;text-align:center;border:1px solid #cdd6e0;border-radius:4px;padding:2px;color:#1e5eb8;font-weight:700}}
.ba{{font-weight:700;color:#1e5eb8}}
.btn{{background:#1e5eb8;color:#fff;border:none;border-radius:5px;padding:4px 12px;cursor:pointer;font-size:12px;margin-left:8px}}
@media print{{.noprint{{display:none}} input.qt,input.pr,input.tg{{border:none;padding:0}}}}</style></head><body><div class=wrap>
<h1>{esc(plan['dealer'])} · {plan['year']}年{m}月作战方案</h1>
<div class=sub>均价 {plan['price']:.0f}元/台 ｜ 现状：成交服务商 {d['tier_active']}家(V2/V3/V4={d['tiers']['V2']}/{d['tiers']['V3']}/{d['tiers']['V4']})、月月动销 {d['loyal_recent']}家、当月激活近月均 {d['act_recent']}家、马甲 {d['vest_n']}个(已剔除)</div>
<h2>一、目标全景</h2><table><tr><th class=l>维度</th><th class=l>指标</th><th>现状</th><th>2026</th><th>2027</th></tr>{ov}</table>
<div class=key>2027 = 2026 翻倍（量类全部×2）；品类收敛到均衡 10/10/10。</div>
<h2>二、逐月 SO / SI（按节奏达成全年）</h2><table><tr><th>月</th><th>节奏</th><th>SO(万)</th><th>SO台数(@{plan['price']:.0f})</th><th>SI(万)</th></tr>{mo}</table>
<h2>二·B、逐月渐进目标（漏斗 + 里程碑曲线 · 非首月顶年底值）</h2>
<div class=key>激活=新增V2(漏斗反推全年{plan['funnel']['act_total']}家)；V2/V3/V4沿同一条里程碑曲线(6月30%/9月50%/12月100%)爬坡，12月精准落到年底目标。激活当月{rp_act6}家起步。</div>
<table><tr><th>月</th><th>激活(新V2)</th><th>月月动销</th><th>V2/V3/V4存量</th><th>无线/夜视王/场景化(台)</th></tr>{rp}</table>
<h2>三、{m}月目标（基准 / 挑战 / 实际）</h2><div class=key noprint>💡「实际目标」可填(默认=基准值)，会自动带入下方激励表的「基数」。</div><table><tr><th class=l>维度</th><th class=l>指标</th><th>基准</th><th>挑战</th><th>实际目标(填)</th><th class=l>计量口径</th></tr>{mt}</table>
<h2>四、{m}月激励方案（挑战值机制）</h2>
<div class=chalbox><b>🎯 挑战值机制：</b>达基准 SO <b>{inc['so_base_wan']}万</b> → 基准激励池 <b id=pb>{tot_b}元</b>；冲挑战 SO <b>{inc['so_chal_wan']}万</b> → 全维度 <b>×{inc['mult']} = <span id=pc>{tot_c}</span>元</b>。</div>
<div class=key noprint>💡 <b>公式：基准激励 = 基数 × 单价</b>。基数默认带入上方「实际目标」(改实际目标→基数自动同步)，也可直接改基数/单价。<button class=btn onclick="window.print()">🖨 打印/存PDF</button><button class=btn onclick="expCSV()">📊 导出Excel</button></div>
<table><tr><th class=l>维度</th><th class=l>激励形式</th><th>基数(=实际目标)</th><th>单价(元)</th><th>基准激励</th><th>挑战激励</th></tr>{ir}
<tr><td class=big><b>合计</b></td><td class=l></td><td></td><td></td><td class=big id=tot_b>{tot_b}</td><td class=chal id=tot_c>{tot_c}</td></tr></table>
<div class=key><b>防作弊：</b>服务商激励只认严口径（当月上线∩当月扫码·剔马甲），补扫旧货/马甲直单不兑现。</div>
<div class=foot>由「代理商作战方案生成器」自动生成 ｜ 方法论 v1 ｜ 口径：SO=产品流向最新分销价；服务商=严口径剔马甲；分档=累计成交额(V2≥0.1万/V3≥1万/V4≥3万)。金额为建议值，可在本页直接编辑后打印/存PDF。</div>
<script>
var MULT={inc['mult']};
function rc(){{var tb=0,tc=0;document.querySelectorAll('input.qt').forEach(function(q){{var tr=q.closest('tr'),p=tr.querySelector('.pr');var b=Math.round((parseFloat(q.value)||0)*(parseFloat(p.value)||0)),c=Math.round(b*MULT);tr.querySelector('.ba').textContent=b;tr.querySelector('.ca').textContent=c;tb+=b;tc+=c;}});var tbr=Math.round(tb),tcr=Math.round(tc);document.getElementById('tot_b').textContent=tbr;document.getElementById('tot_c').textContent=tcr;var pb=document.getElementById('pb');if(pb)pb.textContent=tbr+'元';var pc=document.getElementById('pc');if(pc)pc.textContent=tcr;}}
document.querySelectorAll('input.qt,input.pr').forEach(function(i){{i.addEventListener('input',rc);}});
document.querySelectorAll('input.tg').forEach(function(t){{t.addEventListener('input',function(){{var k=t.getAttribute('data-k');if(!k)return;var q=document.querySelector("input.qt[data-k='"+k+"']");if(q){{q.value=t.value;rc();}}}});}});
function expCSV(){{var rows=[['维度','激励形式','基数','单价','基准激励','挑战激励']];document.querySelectorAll('input.qt').forEach(function(q){{var tr=q.closest('tr'),td=tr.querySelectorAll('td');rows.push([td[0].textContent,td[1].textContent,q.value,tr.querySelector('.pr').value,tr.querySelector('.ba').textContent,tr.querySelector('.ca').textContent]);}});rows.push(['合计','','','',document.getElementById('tot_b').textContent,document.getElementById('tot_c').textContent]);var csv=rows.map(function(r){{return r.map(function(x){{return '"'+String(x).replace(/"/g,'""')+'"';}}).join(',');}}).join('\\n');var blob=new Blob(['\\ufeff'+csv],{{type:'text/csv;charset=utf-8'}});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='{esc(plan['dealer'])}-{m}月激励方案.csv';a.click();}}
</script>
</div></body></html>"""
