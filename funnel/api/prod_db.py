"""
生产库（product_flow.db）只读接入层。

设计要点：
- **只读**：sqlite URI 加 mode=ro，任何写操作都会被 SQLite 直接拒绝，杜绝这个
  页面误改生产数据的可能。
- 库不存在时（本地开发机）所有函数返回 None，路由层据此让前端回落演示数据，
  不抛异常。
- 地区口径统一用 district_base：省份='浙江'、城市='杭州市'（带"市"）、区县='西湖区'。
  provider_contract 的 客户城市/客户区县 与之 100% 对齐（9890/9890 行实测）。

漏斗五阶取数口径（金总 2026-07-30 定四阶，08-01 加 V1 一阶）：
  1 城市总量   district_base.服务商体量 之和            全省 13591
  2 授权签约   provider_contract 排除"授牌服务商"        全省  7755
  3 V1 以上    上表再筛 服务商等级 ≥ v1（签约口径）      —— 08-01 新增
  4 已激活     上表再筛 服务商等级 ≥ v2（签约口径）
  5 高级服务商 上表再筛 服务商等级 ≥ v3（V3~V5 合并）

V1 单独成一阶的理由：授权→V1 是「签约后首次开单」，V1→V2 才是「激活」，两跳的
业务抓手不同（前者靠首单礼，后者靠激活台数解锁的转化红包），合成一跳看不出卡在
哪一步。五阶仍同源同口径、逐层收紧，转化率恒 ≤100%。

**核心指标 = 授权 / 城市总量**，即我司品牌在市场客群里的渗透率。

等级口径（`provider_contract.服务商等级`，签约固化口径 v0~v5）：
- 激活 = V2 以上（含 V2），不再用 `是否激活='Y'` 标志位。
- V3/V4/V5 合并为「高级服务商」一阶，高级服务商内部分层转化不是当前目标。
- 这套等级与视图 `provider_tier_v` 的**货值口径**（按累计上线货值现算，最高 v4、
  无 v5）是两套标准，不可混用。旧版实现拿 `是否激活='Y'` 作分母、签约口径等级作
  分子，两者不同源（签约挂 v3 的可能从未激活），转化率能算出 >100%，现已统一。

意向（潜客池）**不再作为漏斗层级**，降为「城市总量」这一层的旁注：潜客池是 2026
年才建的运营工具，签约是 2025-01 起的存量累积，两者独立来源、不构成包含关系，
插在总量与签约之间会造成漏斗倒挂。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# 生产库路径。服务器上是 /opt/so-data-analytics/db/product_flow.db，
# 本地开发机没有这个库（或为 0 字节占位），此时全部降级。
PROD_DB = Path(os.environ.get(
    "SO_PROD_DB", "/opt/so-data-analytics/db/product_flow.db"))

# 排除无价值客户：管理标签='授牌服务商'（业务红线，2026-07-13 金总定）
NOT_PLAQUE = "COALESCE(管理标签,'') <> '授牌服务商'"

# ── 认证维度 ───────────────────────────────────────────────────────────────────
# 库里有三列都带"认证"字样（见 docs/数据库说明.md:281-283）：
#   渠道客户类型  SMB服务商 / 认证SMB服务商
#   协议类型      SMB服务商协议 / 认证SMB服务商协议
#   分销商认证    (一星)SMB服务商 / (三星)认证SMB服务商
# 取 协议类型 作判定列。2026-07-31 实测三列切分（LIKE '%认证%'，已排除授牌）：
#   渠道客户类型  认证 5970 / 非认证 1785  —— 7 种取值，混进了「专项代理商」
#                 「电商经销商」「普通工程商」，还有 3 条把协议类型串了进来
#   协议类型      认证 5973 / 非认证 1782  —— 只有 2 种取值，无空值，干净
#   分销商认证    认证 5973 / 非认证 1782  —— 12 种取值，带星级前缀
# 协议类型 与 分销商认证 完全吻合、且是二值列，故选它；渠道客户类型 差 3 家（脏值）。
# 判定用 LIKE '%认证%' 而不是等值匹配，兜住将来新增的其它认证类型。
# 换列只需改这一个常量。实际取值分布随时可查 GET /api/funnel/cert-values。
CERT_COL = "协议类型"
CERT_YES = f"COALESCE({CERT_COL},'') LIKE '%认证%'"
CERT_NO = f"COALESCE({CERT_COL},'') NOT LIKE '%认证%'"


def _cert_filter(cert: Optional[str]) -> str:
    """认证维度 WHERE 片段。cert: 'all'(默认) | 'cert' | 'nocert'。"""
    if cert == "cert":
        return f" AND {CERT_YES}"
    if cert == "nocert":
        return f" AND {CERT_NO}"
    return ""


# 服务商等级 → 数字档位。样本值形如 'v0服务商'…'v5服务商'，取第 2 个字符转整数。
# lower() 兜住大小写混写（库里是小写 v，但业务口头写 V）；LIKE 'v_%' 先挡掉空值和
# 不合规值，这类记录 rank 为 NULL，因而不会落进任何 ≥N 的档位。
TIER_RANK = ("CASE WHEN lower(COALESCE(服务商等级,'')) LIKE 'v_%' "
             "THEN CAST(substr(lower(服务商等级), 2, 1) AS INTEGER) END")


def available() -> bool:
    """生产库是否可用（存在且非空占位文件）。"""
    try:
        return PROD_DB.is_file() and PROD_DB.stat().st_size > 1024
    except OSError:
        return False


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── 地区维度 ───────────────────────────────────────────────────────────────────

def geo_tree() -> Optional[Dict[str, Any]]:
    """
    返回 district_base 的完整地区树，供前端下拉框用。
    形如 {"province":"浙江","cities":[{"city":"杭州市","districts":["西湖区",...]}]}
    """
    if not available():
        return None
    with _conn() as conn:
        rows = conn.execute(
            "SELECT 省份, 城市, 区县 FROM district_base "
            "WHERE 城市 IS NOT NULL AND 城市 <> '' ORDER BY 城市, 区县"
        ).fetchall()
    if not rows:
        return None
    province = rows[0]["省份"]
    cities: Dict[str, List[str]] = {}
    for r in rows:
        cities.setdefault(r["城市"], []).append(r["区县"])
    return {
        "province": province,
        "cities": [{"city": c, "districts": d} for c, d in cities.items()],
        "city_count": len(cities),
        "district_count": len(rows),
    }


def cert_values() -> Optional[Dict[str, Any]]:
    """
    认证相关三列的实际取值分布，用来核对 CERT_COL / LIKE '%认证%' 选得对不对。
    纯诊断用途，页面不依赖它。
    """
    if not available():
        return None
    out: Dict[str, Any] = {"cert_col": CERT_COL, "columns": {}}
    with _conn() as conn:
        for col in ("渠道客户类型", "协议类型", "分销商认证"):
            try:
                rows = conn.execute(
                    f"SELECT COALESCE({col},'<NULL>') v, COUNT(*) n "
                    f"FROM provider_contract WHERE {NOT_PLAQUE} "
                    f"GROUP BY 1 ORDER BY n DESC"
                ).fetchall()
                split = conn.execute(
                    f"SELECT SUM(CASE WHEN COALESCE({col},'') LIKE '%认证%' "
                    f"           THEN 1 ELSE 0 END) cert, "
                    f"SUM(CASE WHEN COALESCE({col},'') NOT LIKE '%认证%' "
                    f"    THEN 1 ELSE 0 END) nocert, "
                    f"SUM(CASE WHEN COALESCE({col},'')='' THEN 1 ELSE 0 END) empty "
                    f"FROM provider_contract WHERE {NOT_PLAQUE}"
                ).fetchone()
                out["columns"][col] = {
                    "distinct": [{"value": r["v"], "n": r["n"]} for r in rows],
                    "like_认证": {"cert": split["cert"], "nocert": split["nocert"],
                                  "empty": split["empty"]},
                }
            except sqlite3.Error as e:
                out["columns"][col] = {"error": str(e)}
    return out


# ── 漏斗取数 ───────────────────────────────────────────────────────────────────

def _geo_filter(city: Optional[str], district: Optional[str],
                city_col: str, district_col: str):
    """拼地区 WHERE 片段。返回 (sql, params)，省级时为空条件。"""
    sql, params = "", []
    if city:
        sql += f" AND {city_col} = ?"
        params.append(city)
        if district:
            sql += f" AND {district_col} = ?"
            params.append(district)
    return sql, params


# ── 目标（全年 + 本期应达成）────────────────────────────────────────────────────
# 全年目标取自 provider_target（地市级，12 行 = 浙江合计 + 11 地市，年初下达）。
# 库里的值即权威值，页面只读不可改。
#
# ⚠️ 三处口径限制，都会体现在返回的 notes 里：
# 1. **只到地市级**。选到区县时没有下发目标，返回 null —— 不按体量摊派，那是编数。
# 2. **激活目标库里没有**。provider_target 只有签约总数和 V2/V3/V4 分档；
#    kpi_rhythm 有「服务商激活」节奏曲线，但没有对应的年度激活目标值。
# 3. **V2/V3/V4 是安装红包成交额分级**（V3 = 成交额 1万–3万），漏斗的「高级服务商
#    V3+」是 provider_contract.服务商等级 签约口径。两套标准不可混用，故不把
#    安装红包V3_家数 当作高级服务商目标，该行留空。
TARGET_CITY_ALL = "浙江合计"

# 本期应达成 = 全年目标 × 该月节奏占比（kpi_rhythm）。
# 节奏是「全年目标在 1–12 月各应完成百分之几」，12 个月占比之和为 1。
#
# ⚠️ 列义与列名直觉相反，2026-08-01 实测（GET /api/funnel/target-values）：
#   指标 = 'SMB服务商'      ← 业务线
#   适用范围 = '服务商签约'  ← 具体指标
# 也就是说这两列的内容是**对调**的（文档 docs/数据库说明.md:907 按列名描述，
# 实际数据反着存）。全库 2026 年 7 组节奏，适用范围取值：服务商签约 / 服务商激活 /
# V2服务商 / V3服务商 / V4及以上服务商（指标均为 SMB服务商），另有两条进度条
# （指标为「省区SO进度条」「客户SI进度条(返利前)」）。7 组占比合计均为 1.0。
RHYTHM_SIGN = ("SMB服务商", "服务商签约")


def _target_row(conn, city: Optional[str], year: int):
    """provider_target 里该地市（或全省合计）那一行。取不到返回 None。"""
    key = city or TARGET_CITY_ALL
    row = conn.execute(
        "SELECT * FROM provider_target WHERE 年度 = ? AND 地市 = ?",
        (year, key)).fetchone()
    if row is None and city:
        # 库里地市名可能不带「市」（如「杭州」vs「杭州市」），去尾再试一次
        row = conn.execute(
            "SELECT * FROM provider_target WHERE 年度 = ? AND 地市 = ?",
            (year, city.rstrip("市"))).fetchone()
    return row


def _month_rhythm(conn, year: int, month: int) -> Optional[float]:
    """该月的签约节奏占比。取不到返回 None（调用方回落到均分）。"""
    row = conn.execute(
        "SELECT 占比 FROM kpi_rhythm WHERE 指标 = ? AND 适用范围 = ? "
        "AND 年度 = ? AND 月份 = ?",
        (*RHYTHM_SIGN, year, month)).fetchone()
    return row["占比"] if row and row["占比"] is not None else None


def targets(city: Optional[str] = None, district: Optional[str] = None,
            period_key: Optional[str] = None,
            year: Optional[int] = None, month: Optional[int] = None,
            week_share: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """
    全年目标（库里权威值，只读）+ 本期目标（按月节奏分解）。

    本期目标 = 全年目标 × 当月节奏占比；周则再按当月周数均分。
    「周就是月的均分」—— 节奏只到月粒度，周内没有更细的下发曲线。

    区县级无下发目标 → 全部返回 None，页面显示空、由用户手填。
    """
    if not available():
        return None

    with _conn() as conn:
        # 区县没有下发目标：provider_target 只到地市
        if district:
            return {
                "ytd": {"authorized": None, "activated_v1": None,
                        "activated": None, "senior": None},
                "period": {"authorized": None, "activated_v1": None,
                           "activated": None, "senior": None},
                "scope": "district",
                "editable": True,
                "notes": ["目标只下发到地市级（provider_target），"
                          "区县无下发目标，请手动填写。"],
            }

        row = _target_row(conn, city, year)
        if row is None:
            return {
                "ytd": {"authorized": None, "activated_v1": None,
                        "activated": None, "senior": None},
                "period": {"authorized": None, "activated_v1": None,
                           "activated": None, "senior": None},
                "scope": "city" if city else "province",
                "editable": True,
                "notes": [f"{year} 年{city or '全省'}未找到下发目标（provider_target）。"],
            }

        keys = row.keys()
        authorized = row["服务商签约数_含个人"] if "服务商签约数_含个人" in keys else None
        pool = row["服务商预测总家数"] if "服务商预测总家数" in keys else None
        new_sign = row["新签目标"] if "新签目标" in keys else None

        rhythm = _month_rhythm(conn, year, month)
        notes = [
            "全年目标取自 provider_target（年初下达），库内即权威值，页面不可改。",
        ]
        if rhythm is None:
            # 没有节奏曲线就按 1/12 均分 —— 说明清楚，别让人以为这是下发节奏
            rhythm = 1.0 / 12
            notes.append(f"{year} 年未找到「服务商签约」月度节奏（kpi_rhythm），"
                         f"本期目标按 1/12 均分估算。")
        else:
            notes.append(f"本期目标 = 全年目标 × {month} 月节奏占比 "
                         f"{round(rhythm * 100, 1)}%（kpi_rhythm）。")
            # 2026 年 1 月节奏是 70%（其余 11 个月各 1~4%）—— 那是年初结转的存量
            # 签约数，不是 1 月的新签目标。签约是累计存量指标，节奏曲线把开年存量
            # 压在了 1 月这一格。照公式算不错，但读成「1 月要新签 8400 家」就错了，
            # 所以占比异常大的月份显式提示一句。
            if rhythm > 0.3:
                notes.append(f"⚠️ {month} 月节奏占比达 {round(rhythm * 100)}%，"
                             f"这一格是年初结转的存量签约数，不是当月新签目标，"
                             f"不宜直接当增量考核。")

        share = rhythm * (week_share if week_share else 1.0)
        if week_share:
            notes.append("周目标 = 月目标 ÷ 当月周数（节奏只到月粒度，周内按均分）。")

        def per(v):
            return round(v * share) if v is not None else None

        notes.append("「已开单 V1+」「已激活 V2+」「高级服务商 V3+」库中均无对应"
                     "下发目标：provider_target 只有签约总数与安装红包 V2/V3/V4 "
                     "分档，而该分档是成交额口径、与漏斗的签约等级口径不同，"
                     "故不套用。这三档请手动填写。")

        return {
            "ytd": {"authorized": authorized, "activated_v1": None,
                    "activated": None, "senior": None},
            "period": {"authorized": per(authorized), "activated_v1": None,
                       "activated": None, "senior": None},
            "scope": "city" if city else "province",
            "editable": False,          # 全年目标只读
            "period_editable": True,    # 本期目标是预设值，用户可改
            "rhythm": round(rhythm, 6),
            "week_share": week_share,
            "aside": {"pool_target": pool, "new_sign_target": new_sign},
            "source_row": {"地市": row["地市"], "年度": row["年度"]},
            "notes": notes,
        }


def target_values() -> Optional[Dict[str, Any]]:
    """
    provider_target 与 kpi_rhythm 的实际内容。诊断用：核对地市命名（「杭州」还是
    「杭州市」）、哪些列有值、节奏占比是否 12 个月加总为 1。页面不依赖它。
    """
    if not available():
        return None
    out: Dict[str, Any] = {}
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM provider_target ORDER BY 年度, 地市").fetchall()
            out["provider_target"] = {
                "columns": list(rows[0].keys()) if rows else [],
                "rows": [dict(r) for r in rows],
            }
        except sqlite3.Error as e:
            out["provider_target"] = {"error": str(e)}
        try:
            out["kpi_rhythm_指标"] = [dict(r) for r in conn.execute(
                "SELECT 指标, 适用范围, 年度, COUNT(*) 月数, "
                "ROUND(SUM(占比), 4) 占比合计 FROM kpi_rhythm "
                "GROUP BY 1,2,3 ORDER BY 3,1").fetchall()]
            out["kpi_rhythm_签约"] = [dict(r) for r in conn.execute(
                "SELECT 年度, 月份, 占比 FROM kpi_rhythm "
                "WHERE 指标 = ? AND 适用范围 = ? ORDER BY 年度, 月份",
                RHYTHM_SIGN).fetchall()]
        except sqlite3.Error as e:
            out["kpi_rhythm"] = {"error": str(e)}
    return out


def funnel_data(city: Optional[str] = None,
                district: Optional[str] = None,
                start: Optional[str] = None,
                end: Optional[str] = None,
                cert: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    取指定地区的漏斗五阶绝对值。

    ytd    —— 存量口径（截至库内最新数据），五阶全量 + 意向旁注
    period —— [start, end] 区间内的增量，按日期列落区间统计
    cert   —— 认证维度筛选：'all'(默认) / 'cert' 认证 / 'nocert' 非认证

    ⚠️ 认证筛选只作用于 provider_contract 那四阶（授权/V1+/激活/高级）。
    「城市总量」来自 district_base 的 服务商体量，是市场天花板估算、没有认证维度，
    切认证时保持全量不变。因此认证态下的渗透率 = 认证授权 ÷ 城市总量（全量），
    读作"认证服务商的市场渗透率"，认证+非认证两者相加等于全量渗透率。
    """
    if not available():
        return None

    with _conn() as conn:
        q = lambda s, p=(): conn.execute(s, p).fetchone()

        # ── 1 城市总量：区县服务商体量之和 ──
        gf, gp = _geo_filter(city, district, "城市", "区县")
        pool = q(f"SELECT COALESCE(SUM(服务商体量),0) v FROM district_base "
                 f"WHERE 1=1{gf}", gp)["v"]

        # ── 旁注：潜客池（不占漏斗层级，见模块 docstring）──
        snap_row = q("SELECT MAX(数据时点) v FROM gaode_potential_customer")
        snap = snap_row["v"] if snap_row else None
        intent, intent_signed = 0, 0
        if snap:
            gf2, gp2 = _geo_filter(city, district, "市", "区县")
            intent = q(f"SELECT COUNT(*) v FROM gaode_potential_customer "
                       f"WHERE 数据时点 = ?{gf2}", [snap] + gp2)["v"]
            intent_signed = q(
                f"SELECT COUNT(*) v FROM gaode_potential_customer "
                f"WHERE 数据时点 = ? AND COALESCE(客户名称,'') <> ''{gf2}",
                [snap] + gp2)["v"]

        # ── 2/3/4 授权 / 激活(≥v2) / 高级(≥v3)：provider_contract，排除授牌 ──
        # 三阶同源同口径，逐层收紧，故 高级 ⊆ 激活 ⊆ 授权 恒成立，转化率必然 ≤100%。
        # 认证筛选加在这里，pool 不受影响（见函数 docstring）。
        gf3, gp3 = _geo_filter(city, district, "客户城市", "客户区县")
        cf = _cert_filter(cert)
        base = f"FROM provider_contract WHERE {NOT_PLAQUE}{gf3}{cf}"
        authorized = q(f"SELECT COUNT(*) v {base}", gp3)["v"]
        # V1 以上：签约后「开了张」但还没到激活门槛的那一层。2026-08-01 金总要求
        # 单独成一阶 —— 授权→V1 是「首次开单」，V1→V2 才是「激活」，两跳的抓手
        # 不同（前者靠首单礼，后者靠激活台数解锁的转化红包），合成一跳看不出卡在哪。
        activated_v1 = q(f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 1", gp3)["v"]
        activated = q(f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 2", gp3)["v"]
        senior = q(f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 3", gp3)["v"]

        # 分档明细：不进漏斗，仅供核对与后续下钻
        tiers = {}
        for lv in range(6):
            tiers[f"v{lv}"] = q(
                f"SELECT COUNT(*) v {base} AND {TIER_RANK} = ?",
                gp3 + [lv])["v"]

        # ── 周期增量 ──
        period: Dict[str, Optional[int]] = {}
        if start and end:
            period["authorized"] = q(
                f"SELECT COUNT(*) v {base} AND date(签约日期) BETWEEN ? AND ?",
                gp3 + [start, end])["v"]
            # 等级是当前快照、没有"升档时间"，所以这里用激活时间落区间 + 当前等级
            # 达标来近似：口径是"本期激活且现处该档"，不等于"本期升到该档"。
            period["activated_v1"] = q(
                f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 1 "
                f"AND date(激活时间) BETWEEN ? AND ?", gp3 + [start, end])["v"]
            period["activated"] = q(
                f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 2 "
                f"AND date(激活时间) BETWEEN ? AND ?", gp3 + [start, end])["v"]
            period["senior"] = q(
                f"SELECT COUNT(*) v {base} AND {TIER_RANK} >= 3 "
                f"AND date(激活时间) BETWEEN ? AND ?", gp3 + [start, end])["v"]

        # ── 认证/非认证 各自的授权数（不受当前 cert 筛选影响，供页面显示占比）──
        base_all = f"FROM provider_contract WHERE {NOT_PLAQUE}{gf3}"
        cert_split = {
            "cert": q(f"SELECT COUNT(*) v {base_all} AND {CERT_YES}", gp3)["v"],
            "nocert": q(f"SELECT COUNT(*) v {base_all} AND {CERT_NO}", gp3)["v"],
        }

        # ── 数据新鲜度 ──
        fresh = q("SELECT MAX(date(签约日期)) s, MAX(date(激活时间)) a "
                  "FROM provider_contract")

    penetration = round(authorized / pool * 100, 1) if pool else None

    return {
        "ytd": {
            "pool": pool, "authorized": authorized,
            "activated_v1": activated_v1,
            "activated": activated, "senior": senior,
        },
        "period": period or None,
        # 渗透率 = 授权/城市总量，品牌在市场客群的渗透率，是本页核心指标
        "penetration": penetration,
        # 潜客池：挂在「城市总量」层的旁注，不是漏斗阶段
        "aside": {
            "intent": intent,
            "intent_signed": intent_signed,
            "snapshot": snap,
        },
        "tiers": tiers,
        "cert": {
            "mode": cert or "all",
            "col": CERT_COL,
            # 本地区认证/非认证各自的授权数，不随 mode 变，用来显示占比
            "split": cert_split,
            # 城市总量无认证维度，切认证时 pool 保持全量 —— 前端据此加脚注
            "pool_split": False,
        },
        "asof": {
            "contract_signed": fresh["s"], "contract_activated": fresh["a"],
            "potential_snapshot": snap,
        },
        "notes": {
            "caveats": ([
                f"当前只统计{'认证' if cert == 'cert' else '非认证'}服务商"
                f"（按 {CERT_COL}）。城市总量无认证维度，仍是全量，"
                f"故此处渗透率读作「{'认证' if cert == 'cert' else '非认证'}"
                f"服务商的市场渗透率」。",
            ] if cert in ("cert", "nocert") else []) + [
                "五阶同源 provider_contract、同用签约口径 服务商等级，逐层收紧"
                "（授权 ⊇ V1+ ⊇ 激活V2+ ⊇ 高级V3+），转化率恒 ≤100%。",
                "已排除 管理标签='授牌服务商'（无价值客户，业务红线）。",
                "V3/V4/V5 合并为「高级服务商」，内部分层转化非当前目标。",
                "等级为当前快照、无升档时间，周期增量口径是"
                "「本期激活且现处该档」，不等于「本期升到该档」。",
                "意向（潜客池）为旁注指标，不占漏斗层级：与签约表是独立来源。",
            ],
        },
        "source": "prod",
    }
