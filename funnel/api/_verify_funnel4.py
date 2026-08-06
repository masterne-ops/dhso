"""四阶漏斗口径自测。造一个合成库，验证包含关系与地区下钻。

跑法：.venv/bin/python api/_verify_funnel4.py
不连生产库，纯本地。rsync 时被 --exclude '_verify*.py' 排除。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp()) / "fake_prod.db"

DISTRICTS = [
    # 省份, 城市, 区县, 服务商体量
    ("浙江", "杭州市", "西湖区", 500),
    ("浙江", "杭州市", "余杭区", 300),
    ("浙江", "宁波市", "海曙区", 200),
]

# 客户城市, 客户区县, 服务商等级, 管理标签, 签约日期, 激活时间, 渠道客户类型
# 渠道客户类型：含"认证"字样 = 认证服务商
CONTRACTS = [
    ("杭州市", "西湖区", "v0服务商", "", "2026-07-05", None, "SMB服务商"),
    ("杭州市", "西湖区", "v1服务商", "", "2026-07-06", None, "认证SMB服务商"),
    ("杭州市", "西湖区", "v2服务商", "", "2026-07-07", "2026-07-10", "认证SMB服务商"),
    ("杭州市", "西湖区", "v3服务商", "", "2026-07-08", "2026-07-11", "认证SMB服务商"),
    ("杭州市", "西湖区", "V4服务商", "", "2026-07-09", "2026-07-12", "SMB服务商"),  # 大写等级
    ("杭州市", "西湖区", "v5服务商", "", "2025-12-01", "2026-06-01", "SMB服务商"),  # 上期
    ("杭州市", "西湖区", "v5服务商", "授牌服务商", "2026-07-09", "2026-07-12", "认证SMB服务商"),  # 排除
    ("杭州市", "西湖区", None, "", "2026-07-09", None, "SMB服务商"),        # 等级空
    ("杭州市", "西湖区", "未分级", "", "2026-07-09", None, None),           # 等级不合规+认证列空
    ("杭州市", "余杭区", "v3服务商", "", "2026-07-02", "2026-07-03", "认证SMB服务商"),
    ("宁波市", "海曙区", "v2服务商", "", "2026-07-02", "2026-07-03", "SMB服务商"),
]


def build():
    c = sqlite3.connect(TMP)
    c.execute("CREATE TABLE district_base(省份 TEXT,城市 TEXT,区县 TEXT,服务商体量 INTEGER)")
    c.executemany("INSERT INTO district_base VALUES(?,?,?,?)", DISTRICTS)
    c.execute("CREATE TABLE provider_contract(客户城市 TEXT,客户区县 TEXT,服务商等级 TEXT,"
              "管理标签 TEXT,签约日期 TEXT,激活时间 TEXT,渠道客户类型 TEXT,"
              "协议类型 TEXT,分销商认证 TEXT)")
    # 协议类型 是当前判定列（CERT_COL），合成库里让它与 渠道客户类型 同值，
    # 这样两列谁做判定列断言都成立；分销商认证 留空不参与。
    c.executemany("INSERT INTO provider_contract "
                  "(客户城市,客户区县,服务商等级,管理标签,签约日期,激活时间,"
                  "渠道客户类型,协议类型) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  [r + (r[6],) for r in CONTRACTS])
    c.execute("CREATE TABLE gaode_potential_customer(数据时点 TEXT,外部客户名称 TEXT,"
              "客户名称 TEXT,市 TEXT,区县 TEXT)")
    c.executemany("INSERT INTO gaode_potential_customer VALUES(?,?,?,?,?)", [
        ("2026-07-13", "店A", "已建档A", "杭州市", "西湖区"),
        ("2026-07-13", "店B", "", "杭州市", "西湖区"),
        ("2026-07-13", "店C", "", "宁波市", "海曙区"),
        ("2026-05-16", "店D", "", "杭州市", "西湖区"),  # 旧快照，不该计入
    ])
    c.commit()
    c.close()


FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}: {got}" + ("" if ok else f"  期望 {want}"))
    if not ok:
        FAILS.append(label)


def main():
    build()
    os.environ["SO_PROD_DB"] = str(TMP)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import api.prod_db as p
    p.PROD_DB = TMP  # 模块已在别处 import 过时，环境变量不会重读

    print("生产库可用:", p.available())

    print("\n── 地区树 ──")
    t = p.geo_tree()
    ck("province", t["province"], "浙江")
    ck("city_count", t["city_count"], 2)
    ck("district_count", t["district_count"], 3)

    print("\n── 全省 2026-07 ──")
    d = p.funnel_data(start="2026-07-01", end="2026-07-31")
    y = d["ytd"]
    ck("pool", y["pool"], 1000)
    # 11 行合同，排除 1 行授牌 → 10 行
    ck("authorized", y["authorized"], 10)
    # ≥v1: 下面 6 家再加 西湖 v1 = 7（v0/等级空/未分级 三家不算）
    ck("activated_v1(V1+)", y["activated_v1"], 7)
    # ≥v2: 西湖 v2/v3/V4/v5(上期) + 余杭 v3 + 海曙 v2 = 6
    ck("activated(V2+)", y["activated"], 6)
    # ≥v3: 西湖 v3/V4/v5 + 余杭 v3 = 4
    ck("senior(V3+)", y["senior"], 4)
    ck("penetration", d["penetration"], 1.0)

    print("\n  包含关系（五阶必须逐层收紧）")
    ck("senior <= activated", y["senior"] <= y["activated"], True)
    ck("activated <= activated_v1", y["activated"] <= y["activated_v1"], True)
    ck("activated_v1 <= authorized", y["activated_v1"] <= y["authorized"], True)
    ck("authorized <= pool", y["authorized"] <= y["pool"], True)
    # V1+ 与 V2+ 之差 == 恰好挂 v1 的家数（这一阶存在的意义就是让这批人可见）
    ck("activated_v1 - activated == tiers.v1",
       y["activated_v1"] - y["activated"], d["tiers"]["v1"])

    print("\n  分档明细（授牌已排除，空/未分级不落任何档）")
    ck("tiers", d["tiers"], {"v0": 1, "v1": 1, "v2": 2, "v3": 2, "v4": 1, "v5": 1})
    ck("分档合计 == authorized - 2(空/未分级)",
       sum(d["tiers"].values()), y["authorized"] - 2)

    print("\n  周期增量")
    per = d["period"]
    # 7月签约、非授牌 = 10 行里除掉 v5(2025-12) 那行 = 9（含等级空/未分级两行，
    # 它们算签约但不落任何等级档，正是分档合计 < authorized 的原因）
    ck("period.authorized", per["authorized"], 9)
    # 7月激活且现≥v1：与 ≥v2 同为 5 —— 合成库里 v1 那家 激活时间 为空（签约了但
    # 没激活记录），落不进任何激活区间。这正是 V1 阶的典型形态。
    ck("period.activated_v1", per["activated_v1"], 5)
    # 7月激活且现≥v2：西湖 v2/v3/V4 + 余杭 v3 + 海曙 v2 = 5（西湖 v5 是 6 月激活）
    ck("period.activated", per["activated"], 5)
    # 7月激活且现≥v3：西湖 v3/V4 + 余杭 v3 = 3
    ck("period.senior", per["senior"], 3)
    ck("period.senior <= period.activated", per["senior"] <= per["activated"], True)
    ck("period.activated <= period.activated_v1",
       per["activated"] <= per["activated_v1"], True)

    print("\n  意向旁注（只取最新快照）")
    ck("aside.intent", d["aside"]["intent"], 3)
    ck("aside.intent_signed", d["aside"]["intent_signed"], 1)
    ck("aside.snapshot", d["aside"]["snapshot"], "2026-07-13")
    ck("ytd 不含 intent 键", "intent" in y, False)

    print("\n── 杭州市 ──")
    d = p.funnel_data(city="杭州市", start="2026-07-01", end="2026-07-31")
    y = d["ytd"]
    ck("pool", y["pool"], 800)
    ck("authorized", y["authorized"], 9)
    ck("activated", y["activated"], 5)
    ck("senior", y["senior"], 4)  # 西湖 v3/V4/v5 + 余杭 v3
    ck("aside.intent", d["aside"]["intent"], 2)

    print("\n── 杭州市西湖区 ──")
    d = p.funnel_data(city="杭州市", district="西湖区", start="2026-07-01", end="2026-07-31")
    y = d["ytd"]
    ck("pool", y["pool"], 500)
    ck("authorized", y["authorized"], 8)
    ck("activated", y["activated"], 4)
    ck("senior", y["senior"], 3)
    ck("penetration", d["penetration"], 1.6)

    print("\n── 空周期（数据外区间）──")
    d = p.funnel_data(start="2026-09-01", end="2026-09-30")
    ck("period 全 0", d["period"],
       {"authorized": 0, "activated_v1": 0, "activated": 0, "senior": 0})
    ck("ytd 不受周期影响", d["ytd"]["authorized"], 10)

    print("\n── 认证维度筛选（全省 2026-07）──")
    dall = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="all")
    dc = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="cert")
    dn = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="nocert")

    # 认证：西湖 v1/v2/v3 + 余杭 v3 = 4（授牌那行已排除）
    ck("cert.authorized", dc["ytd"]["authorized"], 4)
    # 非认证：西湖 v0/V4/v5(上期)/空/未分级 + 海曙 v2 = 6
    ck("nocert.authorized", dn["ytd"]["authorized"], 6)
    ck("认证 + 非认证 == 全部",
       dc["ytd"]["authorized"] + dn["ytd"]["authorized"],
       dall["ytd"]["authorized"])

    for k in ("activated_v1", "activated", "senior"):
        ck(f"认证 + 非认证 == 全部 ({k})",
           dc["ytd"][k] + dn["ytd"][k], dall["ytd"][k])
    for k in ("authorized", "activated_v1", "activated", "senior"):
        ck(f"周期增量 认证+非认证 == 全部 ({k})",
           dc["period"][k] + dn["period"][k], dall["period"][k])

    print("\n  认证态下 pool 保持全量（城市总量无认证维度）")
    ck("cert.pool", dc["ytd"]["pool"], dall["ytd"]["pool"])
    ck("nocert.pool", dn["ytd"]["pool"], dall["ytd"]["pool"])
    ck("渗透率可加：认证+非认证 == 全部",
       round(dc["penetration"] + dn["penetration"], 4),
       dall["penetration"])

    print("\n  包含关系在认证维度下仍成立")
    for tag, dd in (("cert", dc), ("nocert", dn)):
        y2 = dd["ytd"]
        ck(f"{tag}: senior<=activated<=V1+<=authorized<=pool",
           y2["senior"] <= y2["activated"] <= y2["activated_v1"]
           <= y2["authorized"] <= y2["pool"], True)

    print("\n  cert 元信息")
    ck("mode 回显", dc["cert"]["mode"], "cert")
    ck("默认 mode=all", dall["cert"]["mode"], "all")
    ck("判定列", dall["cert"]["col"], "协议类型")
    ck("split 不随 mode 变", dc["cert"]["split"], dall["cert"]["split"])
    ck("split 合计 == 全量授权",
       sum(dall["cert"]["split"].values()), dall["ytd"]["authorized"])
    ck("pool_split=False", dall["cert"]["pool_split"], False)
    ck("认证态首条 caveat 提到城市总量",
       "城市总量" in dc["notes"]["caveats"][0], True)
    ck("全部态不加认证 caveat",
       "只统计" in dall["notes"]["caveats"][0], False)

    print("\n  非法/异常 cert 值按 all 处理（路由层已用正则挡住，这里兜底）")
    dbad = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="junk")
    ck("cert='junk' 等同 all", dbad["ytd"]["authorized"], dall["ytd"]["authorized"])
    dnone = p.funnel_data(start="2026-07-01", end="2026-07-31", cert=None)
    ck("cert=None 等同 all", dnone["ytd"]["authorized"], dall["ytd"]["authorized"])

    print("\n  认证列取值分布诊断")
    cv = p.cert_values()
    ck("cert_col", cv["cert_col"], "协议类型")
    ck("协议类型 认证数", cv["columns"]["协议类型"]["like_认证"]["cert"], 4)
    ck("协议类型 非认证数", cv["columns"]["协议类型"]["like_认证"]["nocert"], 6)

    print("\n── 地区 × 认证 交叉（杭州市）──")
    hc = p.funnel_data(city="杭州市", start="2026-07-01", end="2026-07-31", cert="cert")
    hn = p.funnel_data(city="杭州市", start="2026-07-01", end="2026-07-31", cert="nocert")
    ha = p.funnel_data(city="杭州市", start="2026-07-01", end="2026-07-31")
    ck("杭州 认证.authorized", hc["ytd"]["authorized"], 4)
    ck("杭州 认证+非认证 == 全部",
       hc["ytd"]["authorized"] + hn["ytd"]["authorized"], ha["ytd"]["authorized"])
    ck("杭州 split 只含本市",
       sum(ha["cert"]["split"].values()), ha["ytd"]["authorized"])

    print("\n── 无匹配地区 ──")
    d = p.funnel_data(city="不存在市", start="2026-07-01", end="2026-07-31")
    ck("pool", d["ytd"]["pool"], 0)
    ck("authorized", d["ytd"]["authorized"], 0)
    ck("penetration 为 None（不除零）", d["penetration"], None)

    print("\n" + "=" * 46)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
