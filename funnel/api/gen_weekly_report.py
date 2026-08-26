#!/usr/bin/env python3
"""
生成全省各地市转化率漏斗周报（静态 HTML）。

    cd funnel && SO_PROD_DB=... python3 -m api.gen_weekly_report \\
        --start 2026-08-17 --end 2026-08-23 \\
        -o reports/weekly_2026-08-17_2026-08-23.html

数据：provider_contract / district_base / install_redpack / visit_record /
      provider_target / kpi_rhythm（只读生产库）。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import factors as fac
from .overview import JUMPS, WEAK_RATE, _pct, _lookup_target
from .periods import period_meta, weeks_in_month
from .prod_db import (
    NOT_PLAQUE, SO_LINE_FILTER, TARGET_CITY_ALL, TIER_RANK,
    _conn, _geo_filter, _target_row, _month_rhythm, available,
    so_events, _so_year,
)

CITIES = [
    "杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市",
    "金华市", "衢州市", "舟山市", "台州市", "丽水市",
]

JUMP_LABELS = {k: lbl for k, lbl, _, _ in JUMPS}


def _empty() -> Dict[str, int]:
    return {"pool": 0, "authorized": 0, "activated_v1": 0,
            "activated": 0, "senior": 0}


def _load_bundle(start: str, end: str) -> Dict[str, Any]:
    year = int(start[:4])
    month = int(start[5:7])
    with _conn() as conn:
        pool_by: Dict[str, int] = {}
        for r in conn.execute(
            "SELECT 城市, COALESCE(SUM(服务商体量),0) v FROM district_base "
            "WHERE 城市 IS NOT NULL AND 城市 <> '' GROUP BY 城市"
        ):
            pool_by[r["城市"]] = r["v"] or 0

        events = so_events(_so_year(end, start))
        ytd_by: Dict[str, Dict[str, int]] = {c: _empty() for c in CITIES}
        period_by: Dict[str, Dict[str, int]] = {c: _empty() for c in CITIES}
        prov_ytd, prov_period = _empty(), _empty()

        rows = conn.execute(
            f"""SELECT 客户城市 AS city, 客户编码 AS code,
                       date(签约日期) AS signed, date(激活时间) AS act,
                       {TIER_RANK} AS rank
                FROM provider_contract WHERE {NOT_PLAQUE}"""
        ).fetchall()

        for r in rows:
            city = r["city"]
            if city not in ytd_by:
                continue
            signed, act, rank = r["signed"], r["act"], r["rank"]
            ev = events.get(r["code"] or "") or {}
            first_so, v3_time = ev.get("first_so"), ev.get("v3_time")

            def bump(slot: Dict[str, int], *, stock: bool) -> None:
                if stock:
                    if signed is not None and signed <= end:
                        slot["authorized"] += 1
                        if rank is not None:
                            if rank >= 1 and first_so and first_so <= end:
                                slot["activated_v1"] += 1
                            if rank >= 2 and act and act <= end:
                                slot["activated"] += 1
                            if (rank >= 3 and act and act <= end
                                    and v3_time and v3_time <= end):
                                slot["senior"] += 1
                else:
                    if signed and start <= signed <= end:
                        slot["authorized"] += 1
                    if rank is not None:
                        if rank >= 1 and first_so and start <= first_so <= end:
                            slot["activated_v1"] += 1
                        if rank >= 2 and act and start <= act <= end:
                            slot["activated"] += 1
                        if rank >= 3 and v3_time and start <= v3_time <= end:
                            slot["senior"] += 1

            bump(ytd_by[city], stock=True)
            bump(period_by[city], stock=False)
            bump(prov_ytd, stock=True)
            bump(prov_period, stock=False)

        for c in CITIES:
            ytd_by[c]["pool"] = pool_by.get(c, 0)

        prov_ytd["pool"] = sum(pool_by.values())

        tgt_rows = conn.execute(
            "SELECT * FROM provider_target WHERE 年度 = ?", (year,)
        ).fetchall()
        targets = {r["地市"]: dict(r) for r in tgt_rows}
        rhythm = _month_rhythm(conn, year, month)

        evidence: Dict[str, Dict[str, Any]] = {}
        for city in CITIES:
            v_all = fac.visit_all(conn, city, None, start, end) or {}
            v_dh = fac.visit_dahua(conn, city, None, start, end) or {}
            v_ag = fac.visit_dealer(conn, city, None, start, end) or {}
            new_open = fac.new_open_providers(conn, city, None, start, end) or {}
            so = _so_week(conn, city, start, end)
            evidence[city] = {
                "visit": v_all.get("value") or 0,
                "visit_dahua": v_dh.get("value") or 0,
                "visit_dealer": v_ag.get("value") or 0,
                "visit_customers": v_all.get("customers") or 0,
                "visit_people": v_all.get("people") or 0,
                "new_open": new_open.get("value") or 0,
                **so,
            }

        prov_ev = {
            "visit": sum(evidence[c]["visit"] for c in CITIES),
            "visit_dahua": sum(evidence[c]["visit_dahua"] for c in CITIES),
            "visit_dealer": sum(evidence[c]["visit_dealer"] for c in CITIES),
            "visit_customers": sum(evidence[c]["visit_customers"] for c in CITIES),
            "visit_people": sum(evidence[c]["visit_people"] for c in CITIES),
            "new_open": sum(evidence[c]["new_open"] for c in CITIES),
            **_so_week(conn, None, start, end),
        }

    return {
        "ytd_by": ytd_by, "period_by": period_by,
        "prov_ytd": prov_ytd, "prov_period": prov_period,
        "targets": targets, "rhythm": rhythm,
        "evidence": evidence, "prov_evidence": prov_ev,
        "year": year, "month": month,
    }


def _so_week(conn, city: Optional[str], start: str, end: str) -> Dict[str, Any]:
    gf, gp = _geo_filter(city, None, "p.客户城市", "p.客户区县")
    row = conn.execute(
        f"""SELECT COUNT(*) lines,
                   COUNT(DISTINCT r.上线客户编码) providers,
                   COALESCE(SUM(r.产品现有分销价), 0) amount
            FROM install_redpack r
            JOIN provider_contract p ON p.客户编码 = r.上线客户编码
            WHERE COALESCE(r.上线客户编码,'') <> ''
              AND {NOT_PLAQUE.replace('管理标签', 'p.管理标签')}
              AND date(r.上线时间) BETWEEN ? AND ?
              AND {SO_LINE_FILTER.replace('国内产品线二级', 'r.国内产品线二级').replace('产品名称', 'r.产品名称')}
              {gf}""",
        [start, end] + gp,
    ).fetchone()
    return {
        "so_lines": row["lines"] or 0,
        "so_providers": row["providers"] or 0,
        "so_amount": round(float(row["amount"] or 0), 0),
    }


def _weekly_auth_target(targets: Dict[str, Any], city: Optional[str],
                        rhythm: Optional[float], week_share: float
                        ) -> Optional[float]:
    row = _lookup_target(targets, city)
    if not row:
        return None
    annual = row.get("服务商签约数_含个人")
    if annual is None:
        return None
    r = rhythm if rhythm is not None else 1.0 / 12
    return round(annual * r * week_share)


def _rates(ytd: Dict[str, int]) -> Dict[str, Optional[float]]:
    return {k: _pct(ytd.get(nk), ytd.get(dk)) for k, _, nk, dk in JUMPS}


def _weak_keys(rates: Dict[str, Optional[float]]) -> List[str]:
    return [k for k, v in rates.items() if v is not None and v < WEAK_RATE]


def _auto_opinion(name: str, ytd: Dict[str, int], period: Dict[str, int],
                  rates: Dict[str, Optional[float]],
                  bench: Dict[str, Optional[float]],
                  ev: Dict[str, Any], weekly_tgt: Optional[float]) -> str:
    lines: List[str] = []
    p_auth = period.get("authorized") or 0
    if weekly_tgt is not None:
        pct = round(p_auth / weekly_tgt * 100) if weekly_tgt else None
        flag = "达标" if p_auth >= weekly_tgt else "未达"
        lines.append(
            f"本周新签 {p_auth} 家，周签约目标 {weekly_tgt:.0f} 家（{flag}"
            + (f"，完成率 {pct}%" if pct is not None else "") + "）。"
        )

    weak = _weak_keys(rates)
    if weak:
        labels = "、".join(JUMP_LABELS[k] for k in weak)
        lines.append(f"期末转化率短板：{labels}（<{int(WEAK_RATE)}%）。")

    vs = []
    for k, lbl, _, _ in JUMPS:
        r, b = rates.get(k), bench.get(k)
        if r is not None and b is not None and r < b - 5:
            vs.append(f"{lbl} {r}%（省均 {b}%）")
    if vs:
        lines.append("低于省均：" + "；".join(vs) + "。")

    visit = ev.get("visit") or 0
    p_v2 = period.get("activated") or 0
    if visit == 0 and p_v2 == 0 and p_auth == 0:
        lines.append("本周跑动、激活、签约均无产出，需排查一线覆盖。")
    elif visit >= 20 and p_v2 == 0:
        lines.append(f"跑动 {visit} 次但本周无激活增量，关注转化闭环。")
    elif visit < 5 and p_auth > 0:
        lines.append(f"跑动仅 {visit} 次却有新签，可能依赖存量转化。")

    so_amt = ev.get("so_amount") or 0
    so_pr = ev.get("so_providers") or 0
    if so_amt or so_pr:
        lines.append(
            f"筛后 SO：{so_pr} 家 / {ev.get('so_lines', 0)} 条 / "
            f"货值 {so_amt:,.0f} 元；新增开单 {ev.get('new_open', 0)} 家。"
        )

    if not lines:
        lines.append("各指标与省均接近，本周表现平稳。")
    return "\n".join(lines)


def _fmt_rate(v: Optional[float]) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _fmt_int(v: Optional[int]) -> str:
    return str(v) if v is not None else "—"


def _build_rows(bundle: Dict[str, Any], start: str, end: str,
                week_share: float) -> Tuple[List[Dict], Dict]:
    prov_rates = _rates(bundle["prov_ytd"])
    rows: List[Dict[str, Any]] = []

    def pack(name: str, city: Optional[str], ytd, period, ev):
        rates = _rates(ytd)
        wt = _weekly_auth_target(bundle["targets"], city,
                                 bundle["rhythm"], week_share)
        return {
            "name": name, "city": city or "",
            "ytd": ytd, "period": period, "rates": rates,
            "weekly_tgt": wt,
            "evidence": ev,
            "weak": _weak_keys(rates),
            "opinion": _auto_opinion(name, ytd, period, rates, prov_rates, ev, wt),
        }

    rows.append(pack("全省", None, bundle["prov_ytd"], bundle["prov_period"],
                     bundle["prov_evidence"]))
    for city in CITIES:
        rows.append(pack(
            city.rstrip("市"), city,
            bundle["ytd_by"][city], bundle["period_by"][city],
            bundle["evidence"][city],
        ))
    return rows, prov_rates


def _render_html(start: str, end: str, period_key: str,
                 rows: List[Dict], prov_rates: Dict[str, Optional[float]],
                 rhythm: Optional[float], week_share: float) -> str:
    gen_date = date.today().isoformat()
    rhythm_pct = f"{rhythm * 100:.1f}%" if rhythm is not None else "—"
    wks = round(1 / week_share) if week_share else "—"

    def th(*cells: str) -> str:
        return "<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells) + "</tr>"

    def td(*cells: str, cls: str = "") -> str:
        c = f' class="{cls}"' if cls else ""
        return "<tr" + c + ">" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    summary_cells = []
    prov = rows[0]
    for k, lbl, _, _ in JUMPS:
        summary_cells.append(
            f"<span><b>{html.escape(lbl)}</b> {_fmt_rate(prov['rates'][k])}</span>"
        )

    city_rows = []
    for i, r in enumerate(rows):
        if i == 0:
            continue
        y, p = r["ytd"], r["period"]
        ev = r["evidence"]
        wt = r["weekly_tgt"]
        auth_cmp = ""
        if wt is not None:
            diff = (p["authorized"] or 0) - wt
            auth_cmp = f'<span class="{"ok" if diff >= 0 else "warn"}">'
            auth_cmp += f"{'+' if diff >= 0 else ''}{diff:.0f}</span>"

        rate_cells = "".join(
            f'<td class="{"weak" if k in r["weak"] else ""}">{_fmt_rate(r["rates"][k])}</td>'
            for k, _, _, _ in JUMPS
        )
        bench_cells = "".join(
            f"<td>{_fmt_rate(prov_rates.get(k))}</td>"
            for k, _, _, _ in JUMPS
        )

        city_rows.append(f"""
        <section class="city" id="city-{html.escape(r['city'])}">
          <h2>{html.escape(r['name'])}</h2>
          <table class="grid">
            <thead>{th("指标", "期末存量", "本周增量", "周签约目标", "对比")}</thead>
            <tbody>
              {td("授权", _fmt_int(y['authorized']), _fmt_int(p['authorized']),
                  _fmt_int(round(wt)) if wt else "—", auth_cmp)}
              {td("V1+", _fmt_int(y['activated_v1']), _fmt_int(p['activated_v1']), "—", "—")}
              {td("V2+", _fmt_int(y['activated']), _fmt_int(p['activated']), "—", "—")}
              {td("V3+", _fmt_int(y['senior']), _fmt_int(p['senior']), "—", "—")}
            </tbody>
          </table>
          <table class="grid rates">
            <thead><tr><th colspan="4">期末转化率（vs 省均）</th></tr>
              <tr><th>渗透率</th><th>授权→开单</th><th>开单→激活</th><th>激活→高级</th></tr>
            </thead>
            <tbody><tr>{rate_cells}</tr>
              <tr class="bench">{bench_cells.replace('<td>', '<td>省均 ')}</tr>
            </tbody>
          </table>
          <table class="grid ev">
            <thead><tr><th>跑动(次)</th><th>大华</th><th>代理</th><th>触达客户</th>
              <th>SO家数</th><th>SO条数</th><th>SO货值(元)</th><th>新增开单</th></tr></thead>
            <tbody><tr>
              <td>{ev['visit']}</td><td>{ev['visit_dahua']}</td><td>{ev['visit_dealer']}</td>
              <td>{ev['visit_customers']}</td><td>{ev['so_providers']}</td>
              <td>{ev['so_lines']}</td><td>{ev['so_amount']:,.0f}</td><td>{ev['new_open']}</td>
            </tr></tbody>
          </table>
          <label class="op-label">分析意见（可编辑，自动保存到浏览器）</label>
          <div class="opinion" contenteditable="true" data-key="{html.escape(r['city'] or 'province')}">{html.escape(r['opinion'])}</div>
        </section>""")

    overview_rows = []
    for i, r in enumerate(rows):
        y, p, ev = r["ytd"], r["period"], r["evidence"]
        wt = r["weekly_tgt"]
        wt_s = f"{wt:.0f}" if wt is not None else "—"
        gap = ""
        if wt is not None:
            gap = f"{(p['authorized'] or 0) - wt:+.0f}"
        overview_rows.append(
            f"<tr><td>{html.escape(r['name'])}</td>"
            f"<td>{_fmt_rate(r['rates']['p2a'])}</td>"
            f"<td>{_fmt_rate(r['rates']['a2v1'])}</td>"
            f"<td>{_fmt_rate(r['rates']['a2t'])}</td>"
            f"<td>{_fmt_rate(r['rates']['t2v'])}</td>"
            f"<td>{p['authorized']}</td><td>{wt_s}</td><td>{gap}</td>"
            f"<td>{p['activated_v1']}</td><td>{p['activated']}</td><td>{p['senior']}</td>"
            f"<td>{ev['visit']}</td><td>{ev['so_amount']:,.0f}</td></tr>"
        )

    data_json = json.dumps({
        "period_key": period_key, "start": start, "end": end,
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>浙江服务商漏斗周报 {start} ~ {end}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#1a1a1a; --muted:#666; --line:#e2e5ea;
  --weak:#c0392b; --warn:#d35400; --ok:#1e8449; --accent:#2563eb; }}
* {{ box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  margin:0; background:var(--bg); color:var(--text); line-height:1.5; font-size:14px; }}
header {{ background:#1e293b; color:#fff; padding:20px 24px; }}
header h1 {{ margin:0 0 6px; font-size:20px; font-weight:600; }}
header p {{ margin:0; opacity:.85; font-size:13px; }}
main {{ max-width:1200px; margin:0 auto; padding:20px 16px 48px; }}
.card {{ background:var(--card); border-radius:8px; padding:16px 18px; margin-bottom:16px;
  box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.summary {{ display:flex; flex-wrap:wrap; gap:12px 20px; }}
.summary span {{ background:#f1f5f9; padding:6px 10px; border-radius:6px; font-size:13px; }}
h2 {{ margin:0 0 10px; font-size:16px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; margin-bottom:10px; }}
th, td {{ border:1px solid var(--line); padding:6px 8px; text-align:center; }}
th {{ background:#f8fafc; font-weight:600; }}
td.weak, .weak {{ color:var(--weak); font-weight:600; }}
.bench td {{ color:var(--muted); font-size:12px; }}
.warn {{ color:var(--warn); font-weight:600; }}
.ok {{ color:var(--ok); font-weight:600; }}
.city {{ background:var(--card); border-radius:8px; padding:16px 18px; margin-bottom:14px;
  box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.op-label {{ display:block; font-size:12px; color:var(--muted); margin:8px 0 4px; }}
.opinion {{ min-height:72px; padding:10px 12px; border:1px solid var(--line); border-radius:6px;
  background:#fffbeb; white-space:pre-wrap; outline:none; }}
.opinion:focus {{ border-color:var(--accent); box-shadow:0 0 0 2px rgba(37,99,235,.15); }}
.note {{ font-size:12px; color:var(--muted); margin-top:8px; }}
nav {{ margin-bottom:12px; font-size:13px; }}
nav a {{ color:var(--accent); text-decoration:none; margin-right:10px; }}
@media print {{ .opinion {{ border:1px solid #ccc; }} body {{ background:#fff; }} }}
</style>
</head>
<body>
<header>
  <h1>浙江服务商转化漏斗 · 周报</h1>
  <p>{start} ~ {end}（{html.escape(period_key)}） · 期末存量转化率 + 本周增量 · 生成 {gen_date}</p>
</header>
<main>
  <div class="card">
    <h2>全省概要</h2>
    <div class="summary">{''.join(summary_cells)}</div>
    <p class="note">期末存量截至 {end}；增量口径：新签=签约日、V1=筛后首台SO、V2=激活时间、V3=筛后累计过1万。
      周签约目标 = 全年目标 × {rhythm_pct}（8月节奏）÷ {wks} 周。SO 仅 IPC/无线/球机/通用存储，排除丰视。</p>
  </div>

  <div class="card">
    <h2>各地市一览</h2>
    <table>
      <thead><tr>
        <th>地市</th><th>渗透率</th><th>授权→开单</th><th>开单→激活</th><th>激活→高级</th>
        <th>新签</th><th>周目标</th><th>差值</th><th>V1+</th><th>V2+</th><th>V3+</th>
        <th>跑动</th><th>SO货值</th>
      </tr></thead>
      <tbody>{''.join(overview_rows)}</tbody>
    </table>
  </div>

  <nav>跳转：{' · '.join(f'<a href="#city-{html.escape(c)}">{c.rstrip("市")}</a>' for c in CITIES)}</nav>

  {''.join(city_rows)}

  <p class="note card">说明：转化率 &lt; {int(WEAK_RATE)}% 标红；分析意见框可直接修改，内容保存在本机浏览器 localStorage（键 funnel-weekly-{period_key}）。</p>
</main>
<script>
const META = {data_json};
const LS_KEY = 'funnel-weekly-' + META.period_key;
document.querySelectorAll('.opinion').forEach(el => {{
  const k = LS_KEY + ':' + el.dataset.key;
  const saved = localStorage.getItem(k);
  if (saved) el.textContent = saved;
  el.addEventListener('input', () => localStorage.setItem(k, el.textContent));
}});
</script>
</body>
</html>"""


def generate(start: str, end: str, period_key: Optional[str] = None) -> str:
    if not available():
        raise SystemExit("生产库不可用，请设置 SO_PROD_DB")
    if period_key is None:
        d = date.fromisoformat(start)
        iso_y, iso_w, _ = d.isocalendar()
        period_key = f"{iso_y}-W{iso_w:02d}"
    meta = period_meta(period_key)
    week_share = meta.get("week_share") or (1.0 / weeks_in_month(meta["year"], meta["month"]))
    bundle = _load_bundle(start, end)
    rows, prov_rates = _build_rows(bundle, start, end, week_share)
    return _render_html(start, end, period_key, rows, prov_rates,
                        bundle["rhythm"], week_share)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成漏斗周报 HTML")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--period-key", help="如 2026-W34")
    ap.add_argument("-o", "--output", required=True, help="输出 HTML 路径")
    args = ap.parse_args(argv)
    html_out = generate(args.start, args.end, args.period_key)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    print(f"Wrote {out} ({len(html_out):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
