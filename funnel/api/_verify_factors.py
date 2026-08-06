"""因子引擎 + 下发目标自测。造合成库，验口径与边界。

跑法：.venv/bin/python api/_verify_factors.py
不连生产库，纯本地。rsync 时被 --exclude '_verify*.py' 排除。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp()) / "fake_prod.db"

# 客户编码, 客户城市, 客户区县, 管理标签
PROVIDERS = [
    ("C1", "杭州市", "西湖区", ""),
    ("C2", "杭州市", "西湖区", ""),
    ("C3", "杭州市", "余杭区", ""),
    ("C4", "宁波市", "海曙区", ""),
    ("C5", "杭州市", "西湖区", "授牌服务商"),   # 红线：不进任何口径
]

# 卡券编码, 发放客户编码, 卡券名称, 卡券状态, 发放时间
GRANTS = [
    # ── 西湖区 2026-07：发 4 张首单礼，2 张已兑换 → 50%
    ("K01", "C1", "首单礼红包券", "已兑换", "2026-07-03"),
    ("K02", "C1", "首单礼红包券", "已兑换", "2026-07-05"),
    ("K03", "C2", "首单礼红包券", "待解锁", "2026-07-06"),
    ("K04", "C2", "首单礼红包券", "已失效", "2026-07-07"),
    # ── 余杭区 2026-07：1 张，已兑换 → 100%
    ("K05", "C3", "首单礼红包券", "已兑换", "2026-07-08"),
    # ── 宁波海曙 2026-07：1 张，待兑换 → 0%（分母算它，不是无样本）
    ("K06", "C4", "首单礼红包券", "待兑换", "2026-07-09"),
    # ── 授牌服务商的券：全程排除
    ("K07", "C5", "首单礼红包券", "已兑换", "2026-07-09"),
    # ── 别的券种：不该混进首单礼
    ("K08", "C1", "业务员见面红包券", "已兑换", "2026-07-04"),
    ("K09", "C1", "回归礼红包券", "已失效", "2026-07-04"),
    # ── 上期（6 月）首单礼：不进 7 月周期
    ("K10", "C1", "首单礼红包券", "已兑换", "2026-06-20"),
    # ── join 不上 provider_contract（客户已退出）：无地区归属，不计入
    ("K11", "C99", "首单礼红包券", "已兑换", "2026-07-10"),
]

# 客户编码, 拜访客户城市, 拜访客户区县, 打卡人姓名, 打卡人所属公司, 活动创建时间, 拜访时间
# 打卡人所属公司 空 = 🏢 大华分销经理；非空 = 🏪 代理商业务员
VISITS = [
    # ── 西湖区 2026-07：大华 3 次（张三2次/李四1次）、代理商 2 次
    ("C1", "杭州市", "西湖区", "张三", "", "2026-07-02 09:30:00", "2026-07-02 08:00:00"),
    ("C1", "杭州市", "西湖区", "张三", "", "2026-07-08 14:10:00", "2026-07-08 08:00:00"),
    ("C2", "杭州市", "西湖区", "李四", "", "2026-07-09 11:00:00", "2026-07-09 08:00:00"),
    ("C1", "杭州市", "西湖区", "王五", "湖州名泽电子", "2026-07-10 10:00:00", "2026-07-10 08:00:00"),
    ("C2", "杭州市", "西湖区", "王五", "湖州名泽电子", "2026-07-11 10:00:00", "2026-07-11 08:00:00"),
    # ── 余杭区 2026-07：大华 1 次
    ("C3", "杭州市", "余杭区", "张三", "", "2026-07-15 09:00:00", "2026-07-15 08:00:00"),
    # ── 宁波海曙 2026-07：代理商 1 次
    ("C4", "宁波市", "海曙区", "赵六", "宁波某代理", "2026-07-20 16:00:00", "2026-07-20 08:00:00"),
    # ── 月末深夜：活动创建时间 7/31 23:30，正常计入 7 月
    ("C1", "杭州市", "西湖区", "张三", "", "2026-07-31 23:30:00", "2026-07-31 08:00:00"),
    # ── 6 月：不进 7 月周期
    ("C1", "杭州市", "西湖区", "张三", "", "2026-06-15 09:00:00", "2026-06-15 08:00:00"),
    # ── 活动创建时间为空 → COALESCE 回落到 拜访时间（8 月，不进 7 月）
    ("C1", "杭州市", "西湖区", "张三", "", None, "2026-08-03 08:00:00"),
]

FAILS = []


def build():
    c = sqlite3.connect(TMP)
    # 因子引擎只用到这几列，但 available() 要求库 >1KB，故建全 district_base 撑体积
    c.execute("CREATE TABLE district_base(省份 TEXT,城市 TEXT,区县 TEXT,服务商体量 INTEGER)")
    c.executemany("INSERT INTO district_base VALUES(?,?,?,?)", [
        ("浙江", "杭州市", "西湖区", 500), ("浙江", "杭州市", "余杭区", 300),
        ("浙江", "宁波市", "海曙区", 200),
    ])
    c.execute("CREATE TABLE provider_contract(客户编码 TEXT,客户城市 TEXT,"
              "客户区县 TEXT,管理标签 TEXT)")
    c.executemany("INSERT INTO provider_contract VALUES(?,?,?,?)", PROVIDERS)
    c.execute("CREATE TABLE dahua_redpack_grant(卡券编码 TEXT,发放客户编码 TEXT,"
              "卡券名称 TEXT,卡券状态 TEXT,发放时间 TEXT)")
    c.executemany("INSERT INTO dahua_redpack_grant VALUES(?,?,?,?,?)", GRANTS)
    c.execute("CREATE TABLE visit_record(客户编码 TEXT,拜访客户城市 TEXT,"
              "拜访客户区县 TEXT,打卡人姓名 TEXT,打卡人所属公司 TEXT,"
              "活动创建时间 TEXT,拜访时间 TEXT)")
    c.executemany("INSERT INTO visit_record VALUES(?,?,?,?,?,?,?)", VISITS)
    # ── 下发目标：地市级 12 行的缩微版（浙江合计 + 2 市）。
    # 「宁波」故意不带「市」，验 _target_row 的去尾重试。
    c.execute("CREATE TABLE provider_target(年度 INTEGER,地市 TEXT,"
              "服务商预测总家数 INTEGER,服务商签约数_含个人 INTEGER,"
              "新签目标 INTEGER)")
    c.executemany("INSERT INTO provider_target VALUES(?,?,?,?,?)", [
        (2026, "浙江合计", 13591, 12000, 3694),
        (2026, "杭州市", 3000, 2720, 837),
        (2026, "宁波", 2000, 1800, 500),
    ])
    # 节奏：7 月占比 0.10，其余 11 个月平摊 0.90 —— 12 个月加总为 1
    # ⚠️ 两列按**生产库实际**填：指标='SMB服务商'（业务线）、
    # 适用范围='服务商签约'（具体指标）—— 与列名直觉相反，见 prod_db.RHYTHM_SIGN。
    # 另塞一组「服务商激活」节奏，验取数不会串到别的指标上。
    c.execute("CREATE TABLE kpi_rhythm(指标 TEXT,适用范围 TEXT,年度 INTEGER,"
              "月份 INTEGER,占比 REAL)")
    c.executemany("INSERT INTO kpi_rhythm VALUES(?,?,?,?,?)", [
        ("SMB服务商", "服务商签约", 2026, m, 0.10 if m == 7 else 0.90 / 11)
        for m in range(1, 13)
    ] + [
        # 干扰组：若取数把两列写反或漏了 适用范围 条件，7 月会算成 0.50
        ("SMB服务商", "服务商激活", 2026, m, 0.50 if m == 7 else 0.50 / 11)
        for m in range(1, 13)
    ])
    # 撑过 available() 的 1KB 门槛
    c.execute("CREATE TABLE _pad(x TEXT)")
    c.executemany("INSERT INTO _pad VALUES(?)", [("x" * 200,) for _ in range(20)])
    c.commit()
    c.close()


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
    import api.factors as f

    print("生产库可用:", p.available())
    JUL = ("2026-07-01", "2026-07-31")

    print("\n── 全省 2026-07 ──")
    # 分母：西湖 4 + 余杭 1 + 海曙 1 = 6（授牌 K07、join 不上的 K11、
    # 别的券种 K08/K09、上期 K10 全部排除）
    # 分子：K01 K02 K05 = 3 → 50.0%
    d = f.compute("a2t_fo", None, None, *JUL)
    ck("den", d["den"], 6)
    ck("num", d["num"], 3)
    ck("value", d["value"], 50.0)
    ck("pending(待解锁+待兑换)", d["pending"], 2)
    ck("expired(已失效)", d["expired"], 1)
    ck("num+pending+expired == den", d["num"] + d["pending"] + d["expired"], d["den"])

    print("\n── 杭州市 ──")
    d = f.compute("a2t_fo", "杭州市", None, *JUL)
    ck("den", d["den"], 5)
    ck("num", d["num"], 3)
    ck("value", d["value"], 60.0)

    print("\n── 杭州市西湖区 ──")
    d = f.compute("a2t_fo", "杭州市", "西湖区", *JUL)
    ck("den", d["den"], 4)
    ck("num", d["num"], 2)
    ck("value", d["value"], 50.0)

    print("\n── 余杭区（全兑换）──")
    d = f.compute("a2t_fo", "杭州市", "余杭区", *JUL)
    ck("value", d["value"], 100.0)

    print("\n── 海曙区（发了券但一张没兑）──")
    d = f.compute("a2t_fo", "宁波市", "海曙区", *JUL)
    ck("den", d["den"], 1)
    ck("value 是 0 而非 None（发了券就有样本）", d["value"], 0.0)

    print("\n── 无样本区分：分母为 0 返回 None，不是 0% ──")
    ck("空周期(9月)", f.compute("a2t_fo", None, None, "2026-09-01", "2026-09-30"), None)
    ck("无匹配地区", f.compute("a2t_fo", "不存在市", None, *JUL), None)

    print("\n── 地区可加性：各市之和 == 全省 ──")
    hz = f.compute("a2t_fo", "杭州市", None, *JUL)
    nb = f.compute("a2t_fo", "宁波市", None, *JUL)
    prov = f.compute("a2t_fo", None, None, *JUL)
    ck("den 可加", hz["den"] + nb["den"], prov["den"])
    ck("num 可加", hz["num"] + nb["num"], prov["num"])

    print("\n── 上期（2026-06）独立成期 ──")
    d = f.compute("a2t_fo", None, None, "2026-06-01", "2026-06-30")
    ck("6月只有 K10 一张", d["den"], 1)
    ck("6月 100%", d["value"], 100.0)

    print("\n── 业务员红包券使用率 a2t_meet（不筛券种）──")
    # 全省 7 月全部券种（排除授牌 K07、join 不上 K11）：
    # K01 K02 K03 K04 K05 K06 + K08 + K09 = 8 张
    # 已兑换：K01 K02 K05 K08 = 4 → 50.0%
    m = f.compute("a2t_meet", None, None, *JUL)
    ck("den 含全部券种", m["den"], 8)
    ck("num", m["num"], 4)
    ck("value", m["value"], 50.0)
    ck("券种全集 den >= 首单礼 den", m["den"] >= prov["den"], True)

    print("\n── 跑动次数：合计 / 大华 / 代理商 分开看 ──")
    # 全省 7 月：大华 5 次（西湖 4 含月末深夜那条 + 余杭 1）、代理商 3 次
    # （西湖 2 + 海曙 1）→ 合计 8。6 月那条和活动创建时间为空回落到 8 月那条都不计。
    va = f.compute("a2t_visit", None, None, *JUL)
    vd = f.compute("a2t_visit_dahua", None, None, *JUL)
    vg = f.compute("a2t_visit_dealer", None, None, *JUL)
    ck("合计", va["value"], 8)
    ck("大华", vd["value"], 5)
    ck("代理商", vg["value"], 3)
    ck("合计 == 大华 + 代理商", vd["value"] + vg["value"], va["value"])
    ck("单位是次数型（den 为 None）", va["den"], None)
    ck("覆盖客户数（C1~C4 四家）", va["customers"], 4)
    ck("打卡人数（张三/李四/王五/赵六）", va["people"], 4)
    ck("大华打卡人（张三/李四）", vd["people"], 2)

    print("\n  跑动地区下钻")
    ck("西湖 合计", f.compute("a2t_visit", "杭州市", "西湖区", *JUL)["value"], 6)
    ck("西湖 大华", f.compute("a2t_visit_dahua", "杭州市", "西湖区", *JUL)["value"], 4)
    ck("西湖 代理商", f.compute("a2t_visit_dealer", "杭州市", "西湖区", *JUL)["value"], 2)
    ck("杭州 合计", f.compute("a2t_visit", "杭州市", None, *JUL)["value"], 7)
    ck("宁波 大华为 0", f.compute("a2t_visit_dahua", "宁波市", None, *JUL)["value"], 0)

    print("\n  跑动可加性：各市之和 == 全省")
    hzv = f.compute("a2t_visit", "杭州市", None, *JUL)["value"]
    nbv = f.compute("a2t_visit", "宁波市", None, *JUL)["value"]
    ck("7+1 == 8", hzv + nbv, va["value"])

    print("\n  时间列口径：用 COALESCE(活动创建时间, 拜访时间)")
    ck("6月只有 1 次", f.compute("a2t_visit", None, None,
                                "2026-06-01", "2026-06-30")["value"], 1)
    # 活动创建时间为空那条按 拜访时间 落到 8 月
    ck("8月 1 次（活动创建时间为空，回落拜访时间）",
       f.compute("a2t_visit", None, None, "2026-08-01", "2026-08-31")["value"], 1)
    ck("跑动 0 次返回 0 而非 None（没跑就是真没跑）",
       f.compute("a2t_visit", None, None, "2026-09-01", "2026-09-30")["value"], 0)

    print("\n── compute_all / 未注册因子 ──")
    allv = f.compute_all(None, None, *JUL)
    ck("含全部已注册因子", sorted(allv.keys()),
       ["a2t_fo", "a2t_meet", "a2t_visit", "a2t_visit_dahua", "a2t_visit_dealer"])
    ck("未注册因子返回 None", f.compute("a2t_pa", None, None, *JUL), None)

    print("\n── 券种/状态诊断 ──")
    rv = f.redpack_values()
    names = {r["v"]: r["n"] for r in rv["卡券名称"]}
    # 9 = K01~K07 + K10(上期) + K11(join 不上)。诊断口径不做任何过滤，
    # 所以它比因子口径的分母(6)大 —— 差额正是授牌 1 + 上期 1 + 无归属 1。
    ck("首单礼券总数（含授牌/无归属/跨期，诊断不过滤）", names["首单礼红包券"], 9)
    ck("首单礼 LIKE 命中数", rv["首单礼"]["n"], 9)
    ck("join 命中 10/11", (rv["join命中"]["matched"], rv["join命中"]["total"]), (10, 11))

    print("\n── 下发目标：全年只读 + 本期按节奏分解 ──")
    from api.periods import period_meta

    def tg(city, district, pkey):
        m = period_meta(pkey)
        return p.targets(city=city, district=district, period_key=pkey,
                         year=m["year"], month=m["month"],
                         week_share=m["week_share"])

    # 全省 2026-07：全年 12000，7 月节奏 10% → 本期 1200
    t = tg(None, None, "2026-07")
    ck("全省 全年授权目标（库里权威值）", t["ytd"]["authorized"], 12000)
    ck("全省 7月目标 = 12000 × 10%", t["period"]["authorized"], 1200)
    ck("全年目标只读", t["editable"], False)
    ck("本期目标可改", t["period_editable"], True)
    ck("激活无下发目标", t["ytd"]["activated"], None)
    ck("高级服务商无下发目标（红包分档口径不同，不套用）", t["ytd"]["senior"], None)
    ck("旁注：预测总家数", t["aside"]["pool_target"], 13591)

    # 杭州市：全年 2720，7 月 → 272
    t = tg("杭州市", None, "2026-07")
    ck("杭州市 全年", t["ytd"]["authorized"], 2720)
    ck("杭州市 7月 = 2720 × 10%", t["period"]["authorized"], 272)

    # 「宁波市」查库里的「宁波」—— 去尾重试
    t = tg("宁波市", None, "2026-07")
    ck("宁波市 → 库里「宁波」（去「市」重试命中）", t["ytd"]["authorized"], 1800)
    ck("命中行的地市名", t["source_row"]["地市"], "宁波")

    # 周 = 月 ÷ 当月周数。2026-07 有 5 个 ISO 周（周四落在 7 月）
    m = period_meta("2026-W30")
    ck("2026-W30 归属 7 月", (m["year"], m["month"]), (2026, 7))
    ck("7 月周数", m["weeks_in_month"], 5)
    t = tg(None, None, "2026-W30")
    ck("全省 W30 = 12000 × 10% ÷ 5", t["period"]["authorized"], 240)
    ck("week_share = 1/5", t["week_share"], 0.2)
    # 跨月周整周归到周四那个月：W27 周一在 6/29，周四 7/2 → 算 7 月
    t = tg(None, None, "2026-W27")
    ck("跨月周 W27 按周四归 7 月，同为 240", t["period"]["authorized"], 240)

    print("\n  区县级：库里没有下发目标 → 全部留空，不按体量摊派")
    t = tg("杭州市", "西湖区", "2026-07")
    ck("区县 全年为 None", t["ytd"]["authorized"], None)
    ck("区县 本期为 None", t["period"]["authorized"], None)
    ck("区县 允许手填", t["editable"], True)
    ck("scope", t["scope"], "district")

    print("\n  未下达年度：不编数，返回空 + 说明")
    t = tg(None, None, "2030-07")
    ck("2030 全年为 None", t["ytd"]["authorized"], None)
    ck("2030 允许手填", t["editable"], True)

    print("\n  节奏占比异常大的月份（年初结转存量）要出提示")
    c3 = sqlite3.connect(TMP)
    c3.execute("UPDATE kpi_rhythm SET 占比 = 0.70 WHERE 适用范围 = ? AND 月份 = 1",
               ("服务商签约",))
    c3.commit(); c3.close()
    t = tg(None, None, "2026-01")
    ck("1月 = 12000 × 70%", t["period"]["authorized"], 8400)
    ck("notes 提示这是结转存量、非新签目标",
       any("结转" in n for n in t["notes"]), True)
    t = tg(None, None, "2026-07")
    ck("7月（3%）不出该提示", any("结转" in n for n in t["notes"]), False)
    # 改回去 —— 后面还要验「12 个月之和 == 全年目标」，那条依赖占比合计为 1
    c3 = sqlite3.connect(TMP)
    c3.execute("UPDATE kpi_rhythm SET 占比 = ? WHERE 适用范围 = ? AND 月份 = 1",
               (0.90 / 11, "服务商签约"))
    c3.commit(); c3.close()

    print("\n  无节奏曲线的年度：回落 1/12 均分并在 notes 里说明")
    c2 = sqlite3.connect(TMP)
    c2.executemany("INSERT INTO provider_target VALUES(?,?,?,?,?)",
                   [(2027, "浙江合计", 14000, 12000, 3700)])
    c2.commit(); c2.close()
    t = tg(None, None, "2027-07")
    ck("2027 本期 = 12000/12", t["period"]["authorized"], 1000)
    ck("notes 提到按 1/12 均分",
       any("1/12" in n for n in t["notes"]), True)

    print("\n  12 个月本期目标之和 == 全年目标（节奏占比加总为 1）")
    tot = sum(tg(None, None, f"2026-{mm:02d}")["period"]["authorized"]
              for mm in range(1, 13))
    ck("月度之和 ≈ 12000（四舍五入误差 ≤12）", abs(tot - 12000) <= 12, True)

    print("\n" + "=" * 46)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
