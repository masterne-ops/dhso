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
    os.environ["SO_FUNNEL_SESSION_KEY_FILE"] = str(TMPD / "session.key")

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

    print("\n── 浏览器：表单登录 / 登出 / 重登（可反复）──")
    # 浏览器一律带 Sec-Fetch-*。带上它，Basic 通道就该被关掉——否则残留的
    # Basic 缓存会把登出顶回来，这正是之前反复翻车的地方。
    BROWSER = {"sec-fetch-mode": "navigate", "sec-fetch-site": "same-origin",
               "accept": "text/html,application/xhtml+xml"}
    XHR = {"sec-fetch-mode": "cors", "sec-fetch-site": "same-origin",
           "accept": "application/json"}

    def browse(path, **kw):
        return client.get(path, follow_redirects=False, **kw)

    ck("未登录访问 / 跳登录页", browse("/", headers=BROWSER).status_code, 303)
    ck("跳转目标是 /login",
       browse("/", headers=BROWSER).headers["location"].startswith("/login"), True)
    ck("浏览器带 Basic 也不放行",
       browse("/", headers={**BROWSER, **ADMIN}).status_code, 303)
    ck("浏览器 401 不带 WWW-Authenticate（不弹原生框）",
       "www-authenticate" not in {k.lower() for k in
                                  browse("/healthz", headers={**XHR, **ADMIN}).headers},
       True)
    ck("登录页 200", browse("/login").status_code, 200)
    ck("登录页是表单", 'name="password"' in browse("/login").text, True)
    ck("登录页可填账号（不预置）", 'value=""' in browse("/login").text, True)

    def form_login(u, pw, nxt="/"):
        return client.post("/login", data={"username": u, "password": pw, "next": nxt},
                           headers=BROWSER, follow_redirects=False)

    ck("错密码登录 401", form_login("admin", "wrong").status_code, 401)
    ck("错密码不发会话", "funnel_session" in client.cookies, False)

    for i in (1, 2, 3):
        r_in = form_login("admin", "adminpw")
        ck(f"第{i}次登录 303", r_in.status_code, 303)
        ck(f"第{i}次登录发会话", bool(client.cookies.get("funnel_session")), True)
        ck(f"第{i}次登录后进 /", browse("/", headers=BROWSER).status_code, 200)
        ck(f"第{i}次会话可调接口",
           client.get("/api/funnel/me", headers=XHR).json()["user"]["name"], "admin")
        r_out = client.post("/logout", headers=BROWSER, follow_redirects=False)
        ck(f"第{i}次登出 303", r_out.status_code, 303)
        ck(f"第{i}次登出清会话", client.cookies.get("funnel_session"), None)
        ck(f"第{i}次登出后被拦", browse("/", headers=BROWSER).status_code, 303)

    ck("登出后可换账号", form_login("hangzhou", "hzpw").status_code, 303)
    ck("换成杭州账号",
       client.get("/api/funnel/me", headers=XHR).json()["user"]["name"], "hangzhou")
    client.post("/logout", headers=BROWSER, follow_redirects=False)
    client.cookies.clear()

    print("\n── 脚本通道：Basic 仍可用（curl / 部署健康检查）──")
    ck("Basic 无 Sec-Fetch 头 200", client.get("/healthz", headers=ADMIN).status_code, 200)
    ck("Basic 401 带 WWW-Authenticate",
       "Basic" in client.get("/healthz").headers.get("www-authenticate", ""), True)

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

    print("\n── 管辖总览 /overview：一次聚合、按账号裁剪 ──")
    client.post("/api/funnel/state", headers=HZ, json={
        "period_type": "month",
        "state": {
            "todos": [{"text": "西湖跟进首单礼", "done": False},
                      {"text": "已完成项", "done": True}],
            "questions": [{"text": "余杭到场率为何低"}],
            "notes": "杭州7月要把首单礼铺开",
        },
    }, params={"geo_key": HZ_K, "period_key": "2026-07"})
    client.post("/api/funnel/state", headers=HZ, json={
        "period_type": "month",
        "state": {"todos": [{"text": "西湖区拆名单", "done": False}]},
    }, params={"geo_key": HZ_D, "period_key": "2026-07"})

    client.post("/api/funnel/state", headers=ADMIN, json={
        "period_type": "month",
        "state": {
            "questions": [{"text": "GTM跑动效率太低"}],
            "notes": "全省思考：下半年盯跑动效率",
        },
    }, params={"geo_key": PROV, "period_key": "2026-07"})

    ov = get("overview", ADMIN, period_key="2026-07").json()
    ck("admin 默认看地市", ov["level"], "city")
    ck("admin 看到全省+2市", {u["name"] for u in ov["units"]},
       {"全省", "杭州市", "宁波市"})
    ck("生产库来源", ov["source"], "prod")
    prov_u = next(u for u in ov["units"] if u["name"] == "全省")
    ck("全省切片的问题进总览", prov_u["question_open"], 1)
    ck("全省思考进总览",
       any(n.get("text") == "全省思考：下半年盯跑动效率" for n in prov_u["notes"]), True)
    ck("全省 geo_key", prov_u["geo_key"], PROV)
    hz_u = next(u for u in ov["units"] if u["name"] == "杭州市")
    ck("杭州待办只计未完成", hz_u["todo_open"], 1)
    ck("杭州已完成待办仍返回", hz_u["todo_done"], 1)
    ck("杭州问题 1", hz_u["question_open"], 1)
    ck("杭州思考进总览",
       any(n.get("text") == "杭州7月要把首单礼铺开" for n in hz_u["notes"]), True)
    ck("杭州思考条数", hz_u["note_count"], 1)
    ck("杭州目标缺口（1 < 90%×400）", hz_u["target_gap"], True)
    ck("KPI 待办含杭州", ov["kpi"]["open_todos"] >= 1, True)
    ck("KPI 问题含全省", ov["kpi"]["open_questions"] >= 1, True)
    ck("KPI 有思考含全省+杭州", ov["kpi"]["with_notes"] >= 2, True)

    ovh = get("overview", HZ, period_key="2026-07").json()
    ck("杭州账号默认看区县", ovh["level"], "district")
    ck("杭州见全市+区县", {u["name"] for u in ovh["units"]},
       {"全市", "西湖区", "余杭区"})
    ck("杭州总览不含全省", all(u["name"] != "全省" for u in ovh["units"]), True)
    ck("杭州总览不含宁波", all((u["city"] or "杭州市") == "杭州市" for u in ovh["units"]), True)
    hz_city = next(u for u in ovh["units"] if u["name"] == "全市")
    ck("杭州全市能看到本市思考",
       any(n.get("text") == "杭州7月要把首单礼铺开" for n in hz_city["notes"]), True)
    west = next(u for u in ovh["units"] if u["name"] == "西湖区")
    ck("西湖区挂了区县待办", west["todo_open"], 1)

    ck("杭州要宁波 city= → 403",
       get("overview", HZ, period_key="2026-07", city="宁波市").status_code, 403)
    ck("杭州 level=city 只 1 行",
       {u["name"] for u in get("overview", HZ, period_key="2026-07",
                               level="city").json()["units"]},
       {"杭州市"})
    ck("admin 区县+杭州过滤含全市",
       {u["name"] for u in get("overview", ADMIN, period_key="2026-07",
                               level="district", city="杭州市").json()["units"]},
       {"全市", "西湖区", "余杭区"})
    ck("杭州账号不能勾选全省问题",
       client.post("/api/funnel/overview/item", headers=HZ,
                   json={"kind": "question", "index": 0, "done": True},
                   params={"geo_key": PROV, "period_key": "2026-07"}).status_code,
       403)
    ck("非法周期 422",
       get("overview", ADMIN, period_key="2026-W99").status_code, 422)

    tog = client.post(
        "/api/funnel/overview/item", headers=HZ,
        json={"kind": "todo", "index": 0, "done": True, "text": "西湖跟进首单礼"},
        params={"geo_key": HZ_K, "period_key": "2026-07"})
    ck("杭州勾选本市待办 200", tog.status_code, 200)
    ck("勾选写回切片",
       db.load_latest_state(HZ_K, "2026-07")["todos"][0]["done"], True)
    hz_after = next(u for u in get("overview", ADMIN, period_key="2026-07").json()["units"]
                    if u["name"] == "杭州市")
    ck("勾选后总览待办变 0", hz_after["todo_open"], 0)
    ck("勾选后已完成仍在列表",
       any(t.get("done") and "西湖跟进" in t.get("text","") for t in hz_after["todos"]),
       True)
    q_tog = client.post(
        "/api/funnel/overview/item", headers=HZ,
        json={"kind": "question", "index": 0, "done": True, "text": "余杭到场率为何低"},
        params={"geo_key": HZ_K, "period_key": "2026-07"})
    ck("杭州勾选本市问题 200", q_tog.status_code, 200)
    hz_q = next(u for u in get("overview", ADMIN, period_key="2026-07").json()["units"]
                if u["name"] == "杭州市")
    ck("勾选后问题 open=0", hz_q["question_open"], 0)
    ck("勾选后问题仍在列表",
       any(t.get("done") and "余杭" in t.get("text","") for t in hz_q["questions"]),
       True)
    ck("宁波勾选杭州待办 → 403",
       client.post("/api/funnel/overview/item", headers=NB,
                   json={"kind": "todo", "index": 0, "done": True},
                   params={"geo_key": HZ_K, "period_key": "2026-07"}).status_code,
       403)

    ck("总览页 200", client.get("/overview", headers=ADMIN).status_code, 200)
    ck("总览页是总览壳", "管辖总览" in client.get("/overview", headers=ADMIN).text, True)
    ov_html = client.get("/overview", headers=ADMIN).text
    ck("总览页有独立思考栏", 'id="notesCard"' in ov_html and "思考 / 目标" in ov_html, True)
    ck("思考整块可折叠", '<details class="card notesCard' in ov_html, True)
    ck("思考不挤在待办侧栏", "待办 / 问题 / 思考" in ov_html, False)
    ck("总览加载 md 渲染", 'src="/md.js"' in ov_html, True)
    ck("admin 可从地市下钻区县", "drillCity" in ov_html, True)
    funnel_html = client.get("/", headers=ADMIN).text
    ck("完成情况可折叠", 'id="progressCard"' in funnel_html, True)
    ck("各周关键因素可折叠",
       'id="weekFactorCard"' in funnel_html and "foldCard" in funnel_html, True)
    ck("漏斗页有 Markdown 预览", "setNotesUi" in funnel_html, True)
    mdjs = client.get("/md.js", headers=ADMIN)
    ck("md.js 200", mdjs.status_code, 200)
    ck("md.js 导出 mdHtml", b"mdHtml" in mdjs.content, True)
    ck("未登录拉 md.js → 401 或跳登录",
       client.get("/md.js").status_code in (401, 303), True)

    print("\n── 月度 ← 各周切片 ──")
    from api.periods import weeks_of_month
    july_weeks = weeks_of_month(2026, 7)
    ck("2026-07 含 W31", "2026-W31" in july_weeks, True)
    ck("2026-07 五周", len(july_weeks), 5)
    client.post("/api/funnel/state", headers=HZ, json={
        "period_type": "week",
        "state": {
            "todos": [{"text": "W31 首单礼", "done": False}],
            "questions": [{"text": "W31 到场率为何低"}],
            "notes": "W31 周思考：先清西湖名单",
            "manual": {"a2v1": {"gift": 42}},
        },
    }, params={"geo_key": HZ_K, "period_key": "2026-W31"})
    wr = get("week-rollup", ADMIN, geo_key=HZ_K, period_key="2026-07").json()
    ck("月度 week-rollup mode", wr["mode"], "month")
    w31 = next(w for w in wr["weeks"] if w["period_key"] == "2026-W31")
    ck("W31 待办卷进月", w31["state"]["todos"][0]["text"], "W31 首单礼")
    ck("杭州不能卷宁波周",
       get("week-rollup", HZ, geo_key=NB_K, period_key="2026-07").status_code, 403)
    ov_m = get("overview", ADMIN, period_key="2026-07").json()
    hz_m = next(u for u in ov_m["units"] if u["name"] == "杭州市")
    ck("月度总览能看到周待办",
       any(t.get("text") == "W31 首单礼" and t.get("period_label") == "W31"
           for t in hz_m["todos"]), True)
    ck("月度总览能看到周思考",
       any(n.get("text") == "W31 周思考：先清西湖名单"
           and n.get("period_label") == "W31"
           for n in hz_m["notes"]), True)
    ck("月度总览同时保留月思考",
       any(n.get("text") == "杭州7月要把首单礼铺开" for n in hz_m["notes"]), True)
    ck("周待办勾选写回该周",
       client.post("/api/funnel/overview/item", headers=HZ,
                   json={"kind": "todo", "index": 0, "done": True, "text": "W31 首单礼"},
                   params={"geo_key": HZ_K, "period_key": "2026-W31"}).status_code,
       200)
    ck("写回的是周切片",
       db.load_latest_state(HZ_K, "2026-W31")["todos"][0]["done"], True)

    print("\n── 指标库 /factor-library：登录可读，不泄待办思考 ──")
    from api.factors import SEED_DEFS
    db.seed_factor_defs(SEED_DEFS)
    client.post("/api/funnel/state", headers=NB, json={
        "period_type": "week",
        "state": {
            "custom_factors": {
                f"{NB_K}/2026-W33/a2v1": [
                    {"name": "V0服务商跑动", "val": 12, "target": 0, "unit": "%"},
                ],
            },
            "todos": [{"text": "宁波秘密待办", "done": False}],
            "notes": "宁波秘密思考",
        },
    }, params={"geo_key": NB_K, "period_key": "2026-W33"})
    lib = get("factor-library", HZ).json()
    ck("杭州也能读指标库", get("factor-library", HZ).status_code, 200)
    names = {c["name"] for c in lib["community"]}
    ck("收获宁波手填名", "V0服务商跑动" in names, True)
    v0 = next(c for c in lib["community"] if c["name"] == "V0服务商跑动")
    ck("来源地市是宁波", v0["origin"]["geo_label"], "宁波市")
    ck("来源周期 W33", v0["origin"]["period_label"], "W33")
    ck("效果实际 12", v0["latest"]["val"], 12)
    blob = json.dumps(lib, ensure_ascii=False)
    ck("不含宁波待办", "宁波秘密待办" in blob, False)
    ck("不含宁波思考", "宁波秘密思考" in blob, False)
    ov_hz_w33 = json.dumps(
        get("overview", HZ, period_key="2026-W33", level="city").json(),
        ensure_ascii=False)
    ck("杭州总览不含宁波思考", "宁波秘密思考" in ov_hz_w33, False)
    ov_nb_w33 = get("overview", NB, period_key="2026-W33", level="city").json()
    nb_u = next(u for u in ov_nb_w33["units"] if u["name"] == "宁波市")
    ck("宁波总览能看本市思考",
       any(n.get("text") == "宁波秘密思考" for n in nb_u["notes"]), True)
    sys_ids = {s["id"] for s in lib["system"]}
    ck("系统项含跑动合计", "a2t_visit" in sys_ids, True)
    fo = next(s for s in lib["system"] if s["id"] == "a2t_fo")
    ck("首单礼按页面跳归到 a2v1", fo["conv_key"], "a2v1")
    only_v1 = get("factor-library", HZ, conv_key="a2v1").json()
    ck("conv_key=a2v1 过滤手填",
       {c["name"] for c in only_v1["community"]}, {"V0服务商跑动"})
    ck("conv_key=a2v1 系统项是首单礼+新增开单",
       {s["id"] for s in only_v1["system"]}, {"a2t_fo", "a2v1_new_open"})
    no = next(s for s in only_v1["system"] if s["id"] == "a2v1_new_open")
    ck("新增开单挂 a2v1.newOpen", (no["conv_key"], no["factor_key"]),
       ("a2v1", "newOpen"))
    client.post("/api/funnel/state", headers=HZ, json={
        "period_type": "week",
        "state": {"custom_factors": {
            f"{HZ_K}/2026-W33/a2v1": [
                {"name": "V0服务商跑动", "val": 8, "unit": "%"},
            ],
        }},
    }, params={"geo_key": HZ_K, "period_key": "2026-W33"})
    client.post("/api/funnel/state", headers=NB, json={
        "period_type": "week",
        "state": {"custom_factors": {
            f"{NB_K}/2026-W34/a2v1": [
                {"name": "一次性指标", "val": 1, "unit": "%"},
            ],
        }},
    }, params={"geo_key": NB_K, "period_key": "2026-W34"})
    ranked = get("factor-library", HZ, conv_key="a2v1").json()["community"]
    ck("引用多的排前面", ranked[0]["name"], "V0服务商跑动")
    ck("V0 引用 2 次", ranked[0]["use_count"], 2)

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
