"""下级目标卷积自测。造合成生产库 + 合成本地库，验卷积口径与缺口处理。

跑法：.venv/bin/python api/_verify_rollup.py
不连生产库，纯本地。rsync 时被 --exclude '_verify*.py' 排除。
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMPD = Path(tempfile.mkdtemp())
PROD = TMPD / "fake_prod.db"
LOCAL = TMPD / "funnel.db"

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}: {got}" + ("" if ok else f"  期望 {want}"))
    if not ok:
        FAILS.append(label)


def build_prod():
    c = sqlite3.connect(PROD)
    # 3 市 / 5 区县
    c.execute("CREATE TABLE district_base(省份 TEXT,城市 TEXT,区县 TEXT,服务商体量 INTEGER)")
    c.executemany("INSERT INTO district_base VALUES(?,?,?,?)", [
        ("浙江", "杭州市", "西湖区", 500), ("浙江", "杭州市", "余杭区", 300),
        ("浙江", "宁波市", "海曙区", 200), ("浙江", "宁波市", "鄞州区", 150),
        ("浙江", "丽水市", "莲都区", 100),
    ])
    # 下发目标：全省 1000，三市 400/300/200（**之和 900 < 全省 1000**，
    # 刻意留缓冲 —— 真实库里也是这样，卷积值不该等于本级目标）
    c.execute("CREATE TABLE provider_target(年度 INTEGER,地市 TEXT,"
              "服务商预测总家数 INTEGER,服务商签约数_含个人 INTEGER,新签目标 INTEGER)")
    c.executemany("INSERT INTO provider_target VALUES(?,?,?,?,?)", [
        (2026, "浙江合计", 1250, 1000, 300),
        (2026, "杭州市", 500, 400, 120),
        (2026, "宁波市", 400, 300, 90),
        (2026, "丽水市", 300, 200, 60),
    ])
    # 节奏：7 月 10%，其余月份平摊
    c.execute("CREATE TABLE kpi_rhythm(指标 TEXT,适用范围 TEXT,年度 INTEGER,"
              "月份 INTEGER,占比 REAL)")
    c.executemany("INSERT INTO kpi_rhythm VALUES(?,?,?,?,?)", [
        ("SMB服务商", "服务商签约", 2026, m, 0.10 if m == 7 else 0.90 / 11)
        for m in range(1, 13)])
    c.execute("CREATE TABLE _pad(x TEXT)")
    c.executemany("INSERT INTO _pad VALUES(?)", [("x" * 200,) for _ in range(20)])
    c.commit()
    c.close()


def main():
    build_prod()
    os.environ["SO_PROD_DB"] = str(PROD)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import api.db as db
    db.DB_PATH = LOCAL          # 本地库指到临时目录，绝不碰真库
    db.init_schema()
    import api.prod_db as p
    p.PROD_DB = PROD
    # 卷积自测用合成 provider_target 数，避开 api/year_targets.json 的真实任务表
    empty_yt = TMPD / "year_targets.json"
    empty_yt.write_text("{}")
    p.YEAR_TARGETS_PATH = empty_yt
    p._year_targets_file.cache_clear()
    import api.rollup as R

    print("生产库可用:", p.available())
    JUL = "2026-07"

    print("\n── 省级卷积地市：谁都没填过，全部走预设 ──")
    r = R.rollup("浙江//", JUL)
    ck("下级类型", r["children_kind"], "city")
    ck("下级个数", r["children_total"], 3)
    a = r["period"]["authorized"]
    # 预设 = 各市全年 × 10%：40 + 30 + 20 = 90
    ck("本期卷积 = 40+30+20", a["sum"], 90)
    ck("三个都是预设值", a["n_preset"], 3)
    ck("没有已填值", a["n_stored"], 0)
    ck("无缺口", a["n_missing"], 0)
    ck("本级本期目标 = 1000 × 10%", a["own"], 100)
    # 900 < 1000 是下发时留的缓冲，卷积必须如实反映这个差额、不许"凑平"
    ck("差额 = 90 - 100", a["diff"], -10)
    y = r["ytd"]["authorized"]
    ck("全年卷积 = 400+300+200", y["sum"], 900)
    ck("全年本级目标 1000", y["own"], 1000)
    ck("全年差额 -100（下发留缓冲，属正常）", y["diff"], -100)

    print("\n── 地市填了目标后，省级卷积跟着变 ──")
    db.save_state("浙江/杭州市/", "month", JUL,
                  {"targets": {"ytd": {"authorized": 500},
                               JUL: {"authorized": 88}}})
    r = R.rollup("浙江//", JUL)
    a = r["period"]["authorized"]
    # 杭州手填 88 顶掉预设 40 → 88 + 30 + 20 = 138
    ck("本期卷积 = 88+30+20", a["sum"], 138)
    ck("已填 1 个", a["n_stored"], 1)
    ck("预设 2 个", a["n_preset"], 2)
    ck("差额 = 138 - 100", a["diff"], 38)
    ck("全年卷积 = 500+300+200", r["ytd"]["authorized"]["sum"], 1000)

    print("\n── 只改本期不影响全年，反之亦然 ──")
    db.save_state("浙江/宁波市/", "month", JUL,
                  {"targets": {JUL: {"authorized": 5}}})   # 只有本期，没 ytd
    r = R.rollup("浙江//", JUL)
    ck("本期 = 88+5+20", r["period"]["authorized"]["sum"], 113)
    ck("全年不受影响 = 500+300+200", r["ytd"]["authorized"]["sum"], 1000)

    print("\n── 别的周期的填写不串进本周期 ──")
    db.save_state("浙江/丽水市/", "month", "2026-06",
                  {"targets": {"2026-06": {"authorized": 999}}})
    r = R.rollup("浙江//", JUL)
    ck("7 月卷积不含 6 月填的 999", r["period"]["authorized"]["sum"], 113)
    r6 = R.rollup("浙江//", "2026-06")
    # 6 月：杭州/宁波只填了 7 月 → 走预设(0.9/11≈8.18%)，丽水填了 999。
    # 按名字找而不是按下标 —— detail 的顺序跟着 SQL 的 ORDER BY 城市 走，
    # 中文排序结果不直观（按字节序是 丽/宁/杭），写死下标必错。
    by_name = {d["name"]: d for d in r6["detail"]}
    ck("6 月丽水用手填 999", by_name["丽水市"]["period"]["authorized"], 999)
    ck("6 月杭州走预设 = round(400×8.18%)",
       by_name["杭州市"]["period"]["authorized"], 33)

    print("\n── 市级卷积区县：区县无下发目标，全是缺口 ──")
    r = R.rollup("浙江/杭州市/", JUL)
    ck("下级类型", r["children_kind"], "district")
    ck("下级个数", r["children_total"], 2)
    a = r["period"]["authorized"]
    # 区县既没下发值也没预设，一个都没填 → sum 为 None，不是 0
    ck("无人填时 sum 为 None（不是 0）", a["sum"], None)
    ck("两个区县都缺", a["n_missing"], 2)
    ck("缺口名单", sorted(a["missing"]), ["余杭区", "西湖区"])
    ck("notes 说明区县无预设",
       any("只下发到地市级" in n for n in r["notes"]), True)

    print("\n── 部分区县填了：只加填过的，缺的不按 0 计入 ──")
    db.save_state("浙江/杭州市/西湖区", "month", JUL,
                  {"targets": {"ytd": {"authorized": 120},
                               JUL: {"authorized": 12}}})
    r = R.rollup("浙江/杭州市/", JUL)
    a = r["period"]["authorized"]
    ck("卷积只含西湖 12", a["sum"], 12)
    ck("已填 1 个", a["n_stored"], 1)
    ck("仍缺 1 个（余杭）", a["n_missing"], 1)
    ck("缺口名单", a["missing"], ["余杭区"])
    # 差额对标杭州市自己的目标 40（= 400 × 10%）
    ck("差额 = 12 - 40", a["diff"], -28)
    ck("有缺口时 notes 提示不按 0 计入",
       any("不按 0 计入" in n for n in r["notes"]), True)

    print("\n── 区县级没有下级 ──")
    r = R.rollup("浙江/杭州市/西湖区", JUL)
    ck("available=False", r["available"], False)
    ck("children_kind", r["children_kind"], "none")
    ck("ytd 为 None", r["ytd"], None)
    ck("notes 说明最细粒度",
       any("最细粒度" in n for n in r["notes"]), True)

    print("\n── 周维度：卷积走周口径（月 ÷ 当月周数）──")
    rw = R.rollup("浙江//", "2026-W30")
    aw = rw["period"]["authorized"]
    # 2026-07 有 5 个 ISO 周。杭州/宁波填的是**月**目标，不该串到周里 →
    # 三市都走周预设：round(400×10%/5)=8, round(300×.1/5)=6, round(200×.1/5)=4
    ck("周卷积 = 8+6+4（月填写不串到周）", aw["sum"], 18)
    ck("周本级目标 = round(1000×10%/5)", aw["own"], 20)

    print("\n── 激活/高级档：无任务表覆盖时库里也无预设 ──")
    r = R.rollup("浙江//", JUL)
    for lv in ("activated_v1", "activated", "senior"):
        ck(f"{lv} 无预设 → sum None", r["period"][lv]["sum"], None)
        ck(f"{lv} 三市全缺", r["period"][lv]["n_missing"], 3)

    print("\n── 手填激活目标后该档才有卷积值 ──")
    db.save_state("浙江/杭州市/", "month", JUL,
                  {"targets": {"ytd": {"authorized": 500},
                               JUL: {"authorized": 88, "activated": 33}}})
    r = R.rollup("浙江//", JUL)
    ck("activated 卷积 = 33（只有杭州填了）", r["period"]["activated"]["sum"], 33)
    ck("activated 仍缺 2 个", r["period"]["activated"]["n_missing"], 2)

    print("\n── 未下达年度：本级和下级都没目标 ──")
    r = R.rollup("浙江//", "2030-07")
    ck("2030 卷积为 None", r["period"]["authorized"]["sum"], None)
    ck("2030 本级目标为 None", r["period"]["authorized"]["own"], None)
    ck("2030 差额为 None（不拿 None 做减法）",
       r["period"]["authorized"]["diff"], None)

    print("\n── 坏数据不炸：state_json 不是合法 targets ──")
    db.save_state("浙江/丽水市/", "month", JUL, {"targets": "这不是字典"})
    r = R.rollup("浙江//", JUL)
    ck("丽水回落预设 20 → 88+5+20", r["period"]["authorized"]["sum"], 113)

    print("\n" + "=" * 46)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
