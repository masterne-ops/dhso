"""
生成 users.json（管理员 + 11 个地市账号），密码随机。

    python3 ops/mk_users.py --out users.json --creds creds.txt

只写哈希进 users.json；明文密码只在 --creds 那份清单里出现一次，用来转达给
各地市负责人，转达完就该删掉。**明文不入 git、不留服务器**。

已有 users.json 时默认保留其中账号的哈希（不改密码），只补缺的账号；
要重置某个账号的密码用 --reset 杭州市，重置全部用 --reset all。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.users import gen_password, hash_password   # noqa: E402

# 11 个地市 —— 与生产库 district_base.城市 完全一致（带「市」）。
# 命名与库对齐是硬要求：scope 要和 geo_key 第 2 段逐字相等才能授权通过。
CITIES = [
    ("hangzhou", "杭州市"), ("ningbo", "宁波市"), ("wenzhou", "温州市"),
    ("jiaxing", "嘉兴市"), ("huzhou", "湖州市"), ("shaoxing", "绍兴市"),
    ("jinhua", "金华市"), ("quzhou", "衢州市"), ("zhoushan", "舟山市"),
    ("taizhou", "台州市"), ("lishui", "丽水市"),
]
ADMIN = ("admin", "*", "省区管理员")


def build(existing: dict, reset: set) -> tuple[list, list]:
    """返回 (users 列表, [(账号, 范围, 明文密码)] 新生成的凭证清单)。"""
    old = {u["name"]: u for u in (existing.get("users") or [])}
    users, creds = [], []

    def add(name: str, scope: str, label: str):
        keep = old.get(name)
        if keep and name not in reset and "all" not in reset \
                and scope not in reset:
            # 保留原哈希 —— 补账号不该顺手把别人的密码改掉
            users.append({**keep, "scope": scope, "label": label})
            return
        pw = gen_password()
        users.append({"name": name, "scope": scope, "label": label,
                      **hash_password(pw)})
        creds.append((name, scope, pw))

    add(*ADMIN)
    for name, city in CITIES:
        add(name, city, city)
    return users, creds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="users.json 输出路径")
    ap.add_argument("--creds", help="明文凭证清单输出路径（转达用，转达后删除）")
    ap.add_argument("--reset", action="append", default=[],
                    help="重置密码：账号名 / 地市名 / all")
    args = ap.parse_args()

    out = Path(args.out)
    existing = {}
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"⚠️  {out} 不是合法 JSON，将整份重建", file=sys.stderr)

    users, creds = build(existing, set(args.reset))
    out.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    out.chmod(0o600)
    print(f"✅ {out} 已写入 {len(users)} 个账号（{len(creds)} 个新/重置密码）")

    if creds:
        lines = ["账号\t范围\t密码"] + [f"{n}\t{s}\t{p}" for n, s, p in creds]
        text = "\n".join(lines) + "\n"
        if args.creds:
            cp = Path(args.creds)
            cp.write_text(text, encoding="utf-8")
            cp.chmod(0o600)
            print(f"🔑 明文凭证 → {cp}（转达后请删除，勿入 git）")
        else:
            print("\n" + text)
    else:
        print("ℹ️  没有新密码（所有账号已存在且未 --reset）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
