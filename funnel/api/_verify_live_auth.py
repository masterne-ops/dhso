"""线上地区授权验收。验地市账号真的被关在本市，越权一律 403。

两种跑法：

    # A. 在服务器上跑（凭证不出机器，推荐）
    scp api/_verify_live_auth.py root@HOST:/tmp/
    ssh root@HOST '/opt/so-funnel/venv/bin/python3 /tmp/_verify_live_auth.py'

    # B. 从本地跑（自动 ssh 取凭证 + 打公网端口；ssh 不稳时会重试）
    .venv/bin/python api/_verify_live_auth.py --remote

凭证来自 /root/so-funnel-creds.txt（mk_users.py --creds 那份 TSV），**密码不打印**。
只读接口 + 两笔越权写（预期 403，不落库）；不写任何合法切片，
所以不会污染生产 funnel_state。rsync 时被 --exclude '_verify*.py' 排除。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER = "root@121.196.152.24"
CREDS_PATH = "/root/so-funnel-creds.txt"
PERIOD = "2026-07"

PASS, FAIL = 0, 0


def read_creds_local() -> str:
    with open(CREDS_PATH, encoding="utf-8") as f:
        return f.read()


def read_creds_ssh(tries: int = 8, wait: int = 10) -> str:
    """ssh 取凭证。这台机器的 sshd 会间歇性在 banner 阶段断连
    （kex_exchange_identification），单次失败不代表机器挂了 —— 所以重试。"""
    opts = ["-o", "ConnectTimeout=20", "-o", "ControlPath=none",
            "-o", "BatchMode=yes"]
    for i in range(1, tries + 1):
        r = subprocess.run(["ssh", *opts, SERVER, f"cat {CREDS_PATH}"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            print(f"（ssh 第 {i} 次取到凭证）")
            return r.stdout
        print(f"  ssh 第 {i}/{tries} 次失败：{r.stderr.strip()[:70]}")
        if i < tries:
            time.sleep(wait)
    raise RuntimeError("ssh 连不上，取不到凭证")


def parse_creds(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        parts = line.rstrip().split("\t")
        if len(parts) == 3 and parts[0] != "账号":
            out[parts[0]] = (parts[0], parts[2])
    return out


def make_call(base):
    def call(cred, path, geo=None, period=PERIOD, method="GET", body=None):
        """(status, 完整响应文本)。

        响应必须**读全**，别图省事截前 N 字节 —— admin 的 /geo 是 11 市 97 区县
        的完整树，截断后 json.loads 会炸在「Unterminated string」上，看着像接口
        返回了坏 JSON，实际是测试脚本自己切的。

        geo_key 必须 urlencode —— 它含 '/' 和中文，不转义服务端收到的是被截断的
        参数，回 400 而不是我们要验的 403。
        """
        url = f"{base}{path}"
        if geo is not None:
            url += "?" + urllib.parse.urlencode(
                {"geo_key": geo, "period_key": period})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if cred:
            tok = base64.b64encode(f"{cred[0]}:{cred[1]}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        if data:
            req.add_header("Content-Type", "application/json")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")
            except Exception as e:                        # noqa: BLE001
                if attempt == 2:
                    return 0, f"{type(e).__name__}: {e}"
                time.sleep(2)
        return 0, "unreachable"
    return call


def ck(label, got, want):
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {'✅' if ok else '❌'} {label}: {got}" + ("" if ok else f"  期望 {want}"))


def main() -> int:
    remote = "--remote" in sys.argv
    base = "http://121.196.152.24:8443" if remote else "http://127.0.0.1:8443"
    call = make_call(base)

    try:
        raw = read_creds_ssh() if remote else read_creds_local()
    except FileNotFoundError:
        print(f"❌ 找不到 {CREDS_PATH} —— 先跑 deploy_funnel.sh --mk-users，"
              "或该清单已按规范删除（那就手工传一份进来）")
        return 2
    except RuntimeError as e:
        print(f"❌ {e}")
        return 2

    creds = parse_creds(raw)
    need = ("admin", "hangzhou", "ningbo")
    missing = [n for n in need if n not in creds]
    if missing:
        print(f"❌ 凭证清单里缺 {missing}（可能只重置了部分账号）")
        return 2
    ADMIN, HZ, NB = (creds[n] for n in need)
    print(f"凭证已读取（{len(creds)} 个账号），密码不打印。目标 {base}\n")

    PROV, HZ_C, NB_C = "浙江//", "浙江/杭州市/", "浙江/宁波市/"

    print("── 鉴权已启用 ──")
    ck("无凭证 /healthz 401", call(None, "/healthz")[0], 401)
    ck("错密码 401", call(("hangzhou", "wrong-pw"), "/healthz")[0], 401)
    ck("不存在的账号 401", call(("nobody", "x"), "/healthz")[0], 401)
    st, txt = call(ADMIN, "/healthz")
    ck("admin /healthz 200", st, 200)
    ck("healthz auth=true", '"auth":true' in txt, True)

    print("\n── /me 回报自己的范围 ──")
    _, t = call(ADMIN, "/api/funnel/me")
    ck("admin is_admin", json.loads(t)["user"]["is_admin"], True)
    _, t = call(HZ, "/api/funnel/me")
    u = json.loads(t)["user"]
    ck("hangzhou scope", u["scope"], "杭州市")
    ck("hangzhou 非管理员", u["is_admin"], False)

    print("\n── 管理员：全省 + 任意地市 + 区县都通 ──")
    for geo, lbl in ((PROV, "省级汇总"), (HZ_C, "杭州市"), (NB_C, "宁波市"),
                     ("浙江/杭州市/西湖区", "西湖区")):
        ck(f"admin /data {lbl}", call(ADMIN, "/api/funnel/data", geo)[0], 200)
    ck("admin 省级卷积", call(ADMIN, "/api/funnel/target-rollup", PROV)[0], 200)

    print("\n── 杭州账号：本市及其区县通 ──")
    for geo, lbl in ((HZ_C, "杭州市"), ("浙江/杭州市/西湖区", "西湖区"),
                     ("浙江/杭州市/余杭区", "余杭区")):
        ck(f"hangzhou /data {lbl}", call(HZ, "/api/funnel/data", geo)[0], 200)
    for path in ("targets", "target-rollup", "factor-values", "state"):
        ck(f"hangzhou /{path} 本市", call(HZ, f"/api/funnel/{path}", HZ_C)[0], 200)

    print("\n── 杭州账号：省级汇总一律 403（会暴露其它 10 市）──")
    for path in ("data", "state", "targets", "target-rollup", "factor-values"):
        ck(f"hangzhou /{path} 省级", call(HZ, f"/api/funnel/{path}", PROV)[0], 403)

    print("\n── 别人的市一律 403（双向）──")
    for path in ("data", "state", "targets", "target-rollup", "factor-values"):
        ck(f"hangzhou → 宁波 /{path}", call(HZ, f"/api/funnel/{path}", NB_C)[0], 403)
        ck(f"宁波 → 杭州 /{path}", call(NB, f"/api/funnel/{path}", HZ_C)[0], 403)
    ck("hangzhou → 宁波区县", call(HZ, "/api/funnel/data", "浙江/宁波市/海曙区")[0], 403)

    print("\n── 写接口越权 403（只发越权那两笔，不写任何合法切片）──")
    body = {"period_type": "month", "state": {"_probe": "live-auth-check"}}
    for geo, lbl in ((PROV, "省级"), (NB_C, "宁波")):
        ck(f"hangzhou POST /state {lbl}",
           call(HZ, "/api/funnel/state", geo, method="POST", body=body)[0], 403)

    print("\n── /geo 在服务端就裁掉别的市 ──")
    _, t = call(HZ, "/api/funnel/geo")
    g = json.loads(t)
    ck("hangzhou 只见 1 个市", g["geo"]["city_count"], 1)
    ck("就是杭州市", [c["city"] for c in g["geo"]["cities"]], ["杭州市"])
    ck("回带 user.scope", g["user"]["scope"], "杭州市")
    _, t = call(ADMIN, "/api/funnel/geo")
    ck("admin 见 11 个市", json.loads(t)["geo"]["city_count"], 11)

    print("\n── /slices 不传 geo_key 不泄露别人的（旁路检查）──")
    _, t = call(HZ, "/api/funnel/slices")
    ks = {s["geo_key"] for s in json.loads(t)["slices"]}
    ck("hangzhou 只见自己范围内的切片",
       all(k == HZ_C or k.startswith("浙江/杭州市/") for k in ks), True)
    ck("hangzhou 显式传宁波 → 403",
       call(HZ, "/api/funnel/slices?geo_key=" +
            urllib.parse.quote(NB_C, safe=""))[0], 403)

    print("\n── 403 文案说清范围 ──")
    _, t = call(HZ, "/api/funnel/data", PROV)
    d = json.loads(t).get("detail", "")
    ck("提到账号范围", "杭州市" in d, True)
    ck("提到不含省级", "省级" in d, True)

    print("\n" + "=" * 46)
    if FAIL:
        print(f"❌ {FAIL} 项失败 / 共 {PASS + FAIL} 项")
        return 1
    print(f"✅ 全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
