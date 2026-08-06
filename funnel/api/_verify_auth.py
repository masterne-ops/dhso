"""多账号 + 地区级授权自测。跑真实 ASGI 请求，验越权一律 403。

跑法：.venv/bin/python api/_verify_auth.py
不连生产库，纯本地。rsync 时被 --exclude '_verify*.py' 排除。
"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMPD = Path(tempfile.mkdtemp())
PROD = TMPD / "fake_prod.db"
LOCAL = TMPD / "funnel.db"
USERS = TMPD / "users.json"

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}: {got}" + ("" if ok else f"  期望 {want}"))
    if not ok:
        FAILS.append(label)


def build_prod():
    c = sqlite3.connect(PROD)
    c.execute("CREATE TABLE district_base(省份 TEXT,城市 TEXT,区县 TEXT,服务商体量 INTEGER)")
    c.executemany("INSERT INTO district_base VALUES(?,?,?,?)", [
        ("浙江", "杭州市", "西湖区", 500), ("浙江", "杭州市", "余杭区", 300),
        ("浙江", "宁波市", "海曙区", 200),
    ])
    c.execute("CREATE TABLE provider_contract(客户城市 TEXT,客户区县 TEXT,服务商等级 TEXT,"
              "管理标签 TEXT,签约日期 TEXT,激活时间 TEXT,协议类型 TEXT,客户编码 TEXT)")
    c.executemany("INSERT INTO provider_contract VALUES(?,?,?,?,?,?,?,?)", [
        ("杭州市", "西湖区", "v2服务商", "", "2026-07-05", "2026-07-06", "认证SMB服务商协议", "C1"),
        ("宁波市", "海曙区", "v3服务商", "", "2026-07-05", "2026-07-06", "SMB服务商协议", "C2"),
    ])
    c.execute("CREATE TABLE gaode_potential_customer(数据时点 TEXT,外部客户名称 TEXT,"
              "客户名称 TEXT,市 TEXT,区县 TEXT)")
    c.execute("CREATE TABLE provider_target(年度 INTEGER,地市 TEXT,"
              "服务商预测总家数 INTEGER,服务商签约数_含个人 INTEGER,新签目标 INTEGER)")
    c.executemany("INSERT INTO provider_target VALUES(?,?,?,?,?)", [
        (2026, "浙江合计", 1250, 1000, 300), (2026, "杭州市", 500, 400, 120),
        (2026, "宁波市", 400, 300, 90),
    ])
    c.execute("CREATE TABLE kpi_rhythm(指标 TEXT,适用范围 TEXT,年度 INTEGER,"
              "月份 INTEGER,占比 REAL)")
    c.executemany("INSERT INTO kpi_rhythm VALUES(?,?,?,?,?)",
                  [("SMB服务商", "服务商签约", 2026, m, 1 / 12) for m in range(1, 13)])
    c.execute("CREATE TABLE dahua_redpack_grant(卡券编码 TEXT,发放客户编码 TEXT,"
              "卡券名称 TEXT,卡券状态 TEXT,发放时间 TEXT)")
    c.execute("CREATE TABLE visit_record(客户编码 TEXT,拜访客户城市 TEXT,"
              "拜访客户区县 TEXT,打卡人姓名 TEXT,打卡人所属公司 TEXT,"
              "活动创建时间 TEXT,拜访时间 TEXT)")
    c.execute("CREATE TABLE _pad(x TEXT)")
    c.executemany("INSERT INTO _pad VALUES(?)", [("x" * 200,) for _ in range(20)])
    c.commit()
    c.close()


def main():
    build_prod()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    # ⚠️ 环境变量必须在**任何** api 子模块 import 之前设好。
    # api/__init__.py 里有 `from .main import app`，所以哪怕只 import api.users，
    # Python 也会先跑 api/__init__ → api.main → install_auth()。等 import 完再改
    # users.USERS_PATH 就晚了：中间件已经按默认路径（空用户表）挂成无鉴权了。
    os.environ["SO_FUNNEL_USERS"] = str(USERS)
    os.environ["SO_PROD_DB"] = str(PROD)

    # 先只借 hashlib 生成哈希，不 import api ——
    # 用户表得在 app 起来之前写好，否则 install_auth 读到的是空表。
    import hashlib
    import secrets as _s

    def hash_password(pw, iters=1000):
        salt = _s.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
        return {"algo": "pbkdf2_sha256", "iters": iters,
                "salt": salt.hex(), "hash": dk.hex()}

    # 迭代次数压到 1000 —— 自测要发几十个请求，24 万次会让这个脚本跑十几秒
    USERS.write_text(json.dumps({"users": [
        {"name": "admin", "scope": "*", "label": "省区管理员",
         **hash_password("adminpw", iters=1000)},
        {"name": "hangzhou", "scope": "杭州市", "label": "杭州市",
         **hash_password("hzpw", iters=1000)},
        {"name": "ningbo", "scope": "宁波市", "label": "宁波市",
         **hash_password("nbpw", iters=1000)},
    ]}, ensure_ascii=False), encoding="utf-8")

    # 用户表已就位，现在才 import api（这一步会跑 install_auth）
    import api.db as db
    db.DB_PATH = LOCAL
    db.init_schema()
    import api.prod_db as p
    p.PROD_DB = PROD
    from fastapi.testclient import TestClient
    import api.main as m
    client = TestClient(m.app)

    def auth(u, pw):
        tok = base64.b64encode(f"{u}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}

    ADMIN, HZ, NB = auth("admin", "adminpw"), auth("hangzhou", "hzpw"), auth("ningbo", "nbpw")
    PROV, HZ_K, HZ_D, NB_K = "浙江//", "浙江/杭州市/", "浙江/杭州市/西湖区", "浙江/宁波市/"

    def get(path, hdr=None, **q):
        return client.get(f"/api/funnel/{path}", headers=hdr or {}, params=q)

    print("── 鉴权已启用 ──")
    ck("healthz auth=True", client.get("/healthz", headers=ADMIN).json()["auth"], True)
    ck("无凭证 401", client.get("/healthz").status_code, 401)
    ck("错密码 401", client.get("/healthz", headers=auth("hangzhou", "wrong")).status_code, 401)
    ck("不存在的账号 401", client.get("/healthz", headers=auth("nobody", "x")).status_code, 401)

    print("\n── /me 回报自己的范围 ──")
    ck("admin is_admin", get("me", ADMIN).json()["user"]["is_admin"], True)
    ck("杭州 scope", get("me", HZ).json()["user"]["scope"], "杭州市")
    ck("杭州 label", get("me", HZ).json()["user"]["label"], "杭州市")

    print("\n── 管理员：全省 + 任意地市都通 ──")
    for k in (PROV, HZ_K, HZ_D, NB_K):
        ck(f"admin GET data {k}", get("data", ADMIN, geo_key=k, period_key="2026-07").status_code, 200)

    print("\n── 地市账号：本市及其区县通 ──")
    ck("杭州 → 杭州市", get("data", HZ, geo_key=HZ_K, period_key="2026-07").status_code, 200)
    ck("杭州 → 西湖区", get("data", HZ, geo_key=HZ_D, period_key="2026-07").status_code, 200)

    print("\n── 地市账号：省级汇总一律 403（会暴露其它地市数字）──")
    for path, extra in (("data", {"period_key": "2026-07"}),
                        ("state", {"period_key": "2026-07"}),
                        ("targets", {"period_key": "2026-07"}),
                        ("target-rollup", {"period_key": "2026-07"}),
                        ("factor-values", {"period_key": "2026-07"})):
        ck(f"杭州 GET {path} 省级 → 403",
           get(path, HZ, geo_key=PROV, **extra).status_code, 403)

    print("\n── 地市账号：别人的市一律 403 ──")
    for path, extra in (("data", {"period_key": "2026-07"}),
                        ("state", {"period_key": "2026-07"}),
                        ("targets", {"period_key": "2026-07"}),
                        ("target-rollup", {"period_key": "2026-07"}),
                        ("factor-values", {"period_key": "2026-07"})):
        ck(f"杭州 GET {path} 宁波 → 403",
           get(path, HZ, geo_key=NB_K, **extra).status_code, 403)
        ck(f"宁波 GET {path} 杭州 → 403",
           get(path, NB, geo_key=HZ_K, **extra).status_code, 403)
    ck("杭州 GET data 宁波区县 → 403",
       get("data", HZ, geo_key="浙江/宁波市/海曙区", period_key="2026-07").status_code, 403)

    print("\n── 写接口（POST /state）同样受限 ──")
    body = {"period_type": "month", "state": {"targets": {"ytd": {"authorized": 1}}}}
    ck("杭州写本市 → 200",
       client.post("/api/funnel/state", headers=HZ, json=body,
                   params={"geo_key": HZ_K, "period_key": "2026-07"}).status_code, 200)
    ck("杭州写宁波 → 403",
       client.post("/api/funnel/state", headers=HZ, json=body,
                   params={"geo_key": NB_K, "period_key": "2026-07"}).status_code, 403)
    ck("杭州写省级 → 403",
       client.post("/api/funnel/state", headers=HZ, json=body,
                   params={"geo_key": PROV, "period_key": "2026-07"}).status_code, 403)
    # 越权写必须真的没落库 —— 只看状态码不够，得确认库里没这行
    ck("宁波的切片没被杭州写进去", db.load_latest_state(NB_K, "2026-07"), None)
    ck("省级切片没被杭州写进去", db.load_latest_state(PROV, "2026-07"), None)
    ck("杭州自己那行写进去了",
       db.load_latest_state(HZ_K, "2026-07")["targets"]["ytd"]["authorized"], 1)

    print("\n── /slices 不传 geo_key 也不能看别人的（旁路检查）──")
    client.post("/api/funnel/state", headers=NB, json=body,
                params={"geo_key": NB_K, "period_key": "2026-07"})
    ks = {s["geo_key"] for s in get("slices", HZ).json()["slices"]}
    ck("杭州只看到自己的切片", ks, {HZ_K})
    ck("宁波只看到自己的切片",
       {s["geo_key"] for s in get("slices", NB).json()["slices"]}, {NB_K})
    ck("admin 看到两份",
       {s["geo_key"] for s in get("slices", ADMIN).json()["slices"]}, {HZ_K, NB_K})
    ck("杭州显式传宁波 geo_key → 403", get("slices", HZ, geo_key=NB_K).status_code, 403)

    print("\n── /geo 在服务端就裁掉别的市 ──")
    g = get("geo", HZ).json()
    ck("杭州只见 1 个市", g["geo"]["city_count"], 1)
    ck("就是杭州市", [c["city"] for c in g["geo"]["cities"]], ["杭州市"])
    ck("区县数只算本市", g["geo"]["district_count"], 2)
    ck("回带 user", g["user"]["scope"], "杭州市")
    ga = get("geo", ADMIN).json()
    ck("admin 见全部 2 个市", ga["geo"]["city_count"], 2)

    print("\n── 卷积也受限：地市只能卷自己的区县 ──")
    r = get("target-rollup", HZ, geo_key=HZ_K, period_key="2026-07").json()
    ck("杭州卷区县", r["children_kind"], "district")
    ck("下级只有本市 2 个区县", r["children_total"], 2)

    print("\n── 403 的文案说清范围（给用户看的）──")
    d = get("data", HZ, geo_key=PROV, period_key="2026-07").json()["detail"]
    ck("提到账号名", "杭州市" in d, True)
    ck("提到不含省级", "省级" in d, True)

    print("\n── 密码不以明文存在于用户表 ──")
    raw = USERS.read_text(encoding="utf-8")
    for pw in ("adminpw", "hzpw", "nbpw"):
        ck(f"users.json 不含明文 {pw}", pw in raw, False)
    ck("存的是 pbkdf2", "pbkdf2_sha256" in raw, True)

    print("\n" + "=" * 46)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
