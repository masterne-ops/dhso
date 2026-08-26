"""漏斗口径自测：管理表等级 + 当年筛后 SO 时间轴。

跑法：python3 api/_verify_funnel4.py
不连生产库。rsync 时被 --exclude '_verify*.py' 排除。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp()) / "fake_prod.db"

DISTRICTS = [
    ("浙江", "杭州市", "西湖区", 500),
    ("浙江", "杭州市", "余杭区", 300),
    ("浙江", "宁波市", "海曙区", 200),
]

# code, city, district, tier, tag, signed, act, cert_type
CONTRACTS = [
    ("c0", "杭州市", "西湖区", "v0服务商", "", "2026-07-05", None, "SMB服务商"),
    ("c1", "杭州市", "西湖区", "v1服务商", "", "2026-07-06", None, "认证SMB服务商"),
    ("c2", "杭州市", "西湖区", "v2服务商", "", "2026-07-07", "2026-07-10", "认证SMB服务商"),
    ("c3", "杭州市", "西湖区", "v3服务商", "", "2026-07-08", "2026-07-11", "认证SMB服务商"),
    ("c4", "杭州市", "西湖区", "V4服务商", "", "2026-07-09", "2026-07-12", "SMB服务商"),
    ("c5", "杭州市", "西湖区", "v5服务商", "", "2025-12-01", "2026-06-01", "SMB服务商"),
    ("cx", "杭州市", "西湖区", "v5服务商", "授牌服务商", "2026-07-09", "2026-07-12", "认证SMB服务商"),
    ("cn", "杭州市", "西湖区", None, "", "2026-07-09", None, "SMB服务商"),
    ("cu", "杭州市", "西湖区", "未分级", "", "2026-07-09", None, None),
    ("cy", "杭州市", "余杭区", "v3服务商", "", "2026-07-02", "2026-07-03", "认证SMB服务商"),
    ("cnB", "宁波市", "海曙区", "v2服务商", "", "2026-07-02", "2026-07-03", "SMB服务商"),
]

# 上线客户编码, 上线时间, 序列号, 现有分销价, 二级产品线, 产品名称
# V1=首台 SO；V3=当年累计过 1 万。交换机(网络安全)不计入 SO。
REDPACKS = [
    # c1：7/8 首台 IPC → V1 时间；不过 1 万
    ("c1", "2026-07-08 10:00:00", "s1", 500, "IPC", "枪机A"),
    # c2：7/9 首台；激活 7/10；不过 1 万
    ("c2", "2026-07-09 10:00:00", "s2a", 800, "无线摄像机", "无线A"),
    ("c2", "2026-07-09 11:00:00", "s2b", 800, "IPC", "枪机B"),
    # c3：7/8 起累计，7/15 过 1 万
    ("c3", "2026-07-08 12:00:00", "s3a", 4000, "IPC", "枪机C"),
    ("c3", "2026-07-15 12:00:00", "s3b", 7000, "球机", "球机A"),
    # c4：7/9 起，7/16 过 1 万（且有一台交换机不应计入）
    ("c4", "2026-07-09 09:00:00", "s4a", 5000, "IPC", "枪机D"),
    ("c4", "2026-07-16 09:00:00", "s4b", 6000, "通用存储", "NVR-A"),
    ("c4", "2026-07-16 10:00:00", "s4x", 20000, "网络安全", "交换机应排除"),
    # c5：6 月首台并过 1 万
    ("c5", "2026-06-01 10:00:00", "s5a", 6000, "IPC", "枪机E"),
    ("c5", "2026-06-15 10:00:00", "s5b", 5000, "IPC", "枪机F"),
    # 余杭 cy：7/2 首台，7/4 过 1 万
    ("cy", "2026-07-02 10:00:00", "sy1", 5500, "IPC", "枪机G"),
    ("cy", "2026-07-04 10:00:00", "sy2", 5000, "IPC", "枪机H"),
    # 宁波 cnB：7/2 首台，不过 1 万
    ("cnB", "2026-07-02 11:00:00", "sn1", 1200, "IPC", "枪机I"),
    # 丰视：挂在 c1 上但应排除，不影响首台（已有更早 IPC）
    ("c1", "2026-07-07 09:00:00", "sf", 9000, "IPC", "丰视摄像机应排除"),
]


def build():
    c = sqlite3.connect(TMP)
    c.execute("CREATE TABLE district_base(省份 TEXT,城市 TEXT,区县 TEXT,服务商体量 INTEGER)")
    c.executemany("INSERT INTO district_base VALUES(?,?,?,?)", DISTRICTS)
    c.execute(
        "CREATE TABLE provider_contract("
        "客户编码 TEXT,客户城市 TEXT,客户区县 TEXT,服务商等级 TEXT,"
        "管理标签 TEXT,签约日期 TEXT,激活时间 TEXT,渠道客户类型 TEXT,"
        "协议类型 TEXT,分销商认证 TEXT)"
    )
    c.executemany(
        "INSERT INTO provider_contract "
        "(客户编码,客户城市,客户区县,服务商等级,管理标签,签约日期,激活时间,"
        "渠道客户类型,协议类型) VALUES(?,?,?,?,?,?,?,?,?)",
        [r + (r[7],) for r in CONTRACTS],
    )
    c.execute(
        "CREATE TABLE install_redpack("
        "上线客户编码 TEXT,上线时间 TEXT,产品序列号 TEXT,"
        "产品现有分销价 REAL,国内产品线二级 TEXT,产品名称 TEXT)"
    )
    c.executemany(
        "INSERT INTO install_redpack VALUES(?,?,?,?,?,?)", REDPACKS
    )
    c.execute(
        "CREATE TABLE gaode_potential_customer("
        "数据时点 TEXT,外部客户名称 TEXT,客户名称 TEXT,市 TEXT,区县 TEXT)"
    )
    c.executemany(
        "INSERT INTO gaode_potential_customer VALUES(?,?,?,?,?)",
        [
            ("2026-07-13", "店A", "已建档A", "杭州市", "西湖区"),
            ("2026-07-13", "店B", "", "杭州市", "西湖区"),
            ("2026-07-13", "店C", "", "宁波市", "海曙区"),
            ("2026-05-16", "店D", "", "杭州市", "西湖区"),
        ],
    )
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

    p.PROD_DB = TMP

    print("生产库可用:", p.available())

    print("\n── 地区树 ──")
    t = p.geo_tree()
    ck("province", t["province"], "浙江")
    ck("city_count", t["city_count"], 2)
    ck("district_count", t["district_count"], 3)

    print("\n── 全省 2026-07 期末存量 ──")
    d = p.funnel_data(start="2026-07-01", end="2026-07-31")
    y = d["ytd"]
    ck("pool", y["pool"], 1000)
    # 11 行合同排除授牌 → 10
    ck("authorized", y["authorized"], 10)
    # V1+：c1,c2,c3,c4,c5,cy,cnB = 7（均有筛后首台 SO ≤7/31）
    ck("activated_v1(V1+)", y["activated_v1"], 7)
    # V2+：无激活的 c1 不计 → c2,c3,c4,c5,cy,cnB = 6
    ck("activated(V2+)", y["activated"], 6)
    # V3+：c3(7/15),c4(7/16),c5(6/15),cy(7/4) = 4
    ck("senior(V3+)", y["senior"], 4)
    ck("penetration", d["penetration"], 1.0)

    print("\n  包含关系")
    ck("senior <= activated", y["senior"] <= y["activated"], True)
    ck("activated <= activated_v1", y["activated"] <= y["activated_v1"], True)
    ck("activated_v1 <= authorized", y["activated_v1"] <= y["authorized"], True)

    print("\n  周期增量（时间轴分叉）")
    per = d["period"]
    # 7 月新签：除 c5(2025-12) → 9
    ck("period.authorized", per["authorized"], 9)
    # 7 月首台 SO：c1,c2,c3,c4,cy,cnB（c5 在 6 月）= 6
    ck("period.activated_v1", per["activated_v1"], 6)
    # 7 月激活：c2,c3,c4,cy,cnB（c5 在 6 月）= 5
    ck("period.activated", per["activated"], 5)
    # 7 月过 1 万：c3,c4,cy（c5 在 6 月）= 3
    ck("period.senior", per["senior"], 3)
    ck("V1增量 > V2增量（不再塌成一样）",
       per["activated_v1"] > per["activated"], True)

    print("\n  丰视 / 交换机不进 SO")
    # c1 若误计 7/7 丰视，首台会变成 7/7；断言首台增量仍含 c1 且 V1 存量含 c1
    ck("c1 仍在 V1+（丰视未抢首台）", y["activated_v1"], 7)

    print("\n  意向旁注")
    ck("aside.intent", d["aside"]["intent"], 3)
    ck("aside.snapshot", d["aside"]["snapshot"], "2026-07-13")
    ck("stock_end", d["asof"]["stock_end"], "2026-07-31")

    print("\n── 4 月 / 6 月随时间动 ──")
    d4 = p.funnel_data(start="2026-04-01", end="2026-04-30")
    ck("4月授权仅 c5", d4["ytd"]["authorized"], 1)
    ck("4月尚无 SO → V1+=0", d4["ytd"]["activated_v1"], 0)
    ck("4月渗透率", d4["penetration"], 0.1)

    d6 = p.funnel_data(start="2026-06-01", end="2026-06-30")
    ck("6月 V1+", d6["ytd"]["activated_v1"], 1)
    ck("6月 V2+", d6["ytd"]["activated"], 1)
    ck("6月 V3+", d6["ytd"]["senior"], 1)
    ck("6月增量 V1", d6["period"]["activated_v1"], 1)
    ck("6月增量 V3", d6["period"]["senior"], 1)

    print("\n── 杭州市 / 西湖区 ──")
    d = p.funnel_data(city="杭州市", start="2026-07-01", end="2026-07-31")
    ck("杭州 authorized", d["ytd"]["authorized"], 9)
    ck("杭州 V2+", d["ytd"]["activated"], 5)
    ck("杭州 V3+", d["ytd"]["senior"], 4)

    d = p.funnel_data(
        city="杭州市", district="西湖区", start="2026-07-01", end="2026-07-31"
    )
    ck("西湖 authorized", d["ytd"]["authorized"], 8)
    ck("西湖 V1+", d["ytd"]["activated_v1"], 5)  # c1-c5
    ck("西湖 V2+", d["ytd"]["activated"], 4)     # c2-c5
    ck("西湖 V3+", d["ytd"]["senior"], 3)        # c3,c4,c5

    print("\n── 晚于数据的周期 ──")
    d = p.funnel_data(start="2026-09-01", end="2026-09-30")
    ck("9月增量全 0", d["period"],
       {"authorized": 0, "activated_v1": 0, "activated": 0, "senior": 0})
    ck("9月期末授权仍 10", d["ytd"]["authorized"], 10)

    print("\n── 认证维度 ──")
    dall = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="all")
    dc = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="cert")
    dn = p.funnel_data(start="2026-07-01", end="2026-07-31", cert="nocert")
    # 认证：c1,c2,c3,cy = 4
    ck("cert.authorized", dc["ytd"]["authorized"], 4)
    ck("nocert.authorized", dn["ytd"]["authorized"], 6)
    ck("认证+非认证==全部",
       dc["ytd"]["authorized"] + dn["ytd"]["authorized"],
       dall["ytd"]["authorized"])
    for k in ("activated_v1", "activated", "senior"):
        ck(f"认证+非认证==全部 ({k})",
           dc["ytd"][k] + dn["ytd"][k], dall["ytd"][k])
    for k in ("authorized", "activated_v1", "activated", "senior"):
        ck(f"增量 认证+非认证==全部 ({k})",
           dc["period"][k] + dn["period"][k], dall["period"][k])
    ck("cert.pool 全量", dc["ytd"]["pool"], dall["ytd"]["pool"])
    ck("渗透率可加",
       round(dc["penetration"] + dn["penetration"], 4), dall["penetration"])

    print("\n── 无匹配地区 ──")
    d = p.funnel_data(city="不存在市", start="2026-07-01", end="2026-07-31")
    ck("pool", d["ytd"]["pool"], 0)
    ck("authorized", d["ytd"]["authorized"], 0)
    ck("penetration None", d["penetration"], None)

    print("\n" + "=" * 46)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
