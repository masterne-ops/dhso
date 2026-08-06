"""
多账号 + 地区级授权。

背景：原来全站只有一组 Basic 凭证，任何登录用户都能读写**任意**地区的切片
（api/README.md 里一直挂着这条警告）。11 个地市要各自维护目标和待办，就必须
把"你是谁"和"你能碰哪个地区"分开。

## 存储

用户表是一个 JSON 文件，路径由 `SO_FUNNEL_USERS` 指定，默认
`/etc/so-funnel/users.json`（chmod 600，root 拥有）。格式：

    {"users": [
      {"name": "hz", "scope": "杭州市", "label": "杭州市",
       "algo": "pbkdf2_sha256", "iters": 240000,
       "salt": "<hex>", "hash": "<hex>"}
    ]}

**只存哈希，不存明文。** Basic Auth 每次请求都会把明文密码发过来，所以服务端
不需要留明文——留了就等于把 12 个人的密码堆在一个文件里等着泄。
PBKDF2-HMAC-SHA256，迭代 24 万次（够慢以对抗离线爆破，又不至于让每个请求都卡）。

⚠️ 每次请求都要做一次 KDF，这是 Basic Auth 的固有代价（没有会话/token）。
24 万次迭代在服务器上约 60~100ms。页面加载会并发好几个接口，所以加了
**per-connection 校验缓存**（见 auth.py 的 `_VERIFY_CACHE`），同一组凭证在
缓存有效期内不重复算 KDF。

## 权限模型

    scope = "*"        管理员：全省 + 所有地市 + 所有区县
    scope = "杭州市"    地市账号：本市 + 本市下属区县，**不含省级汇总**

地市账号看不到省级汇总是有意的：省级视图会暴露全省和其它 10 个地市的数字。
判定只认 geo_key 的第 2 段（城市），第 3 段（区县）必须属于该市——但区县归属
不在这里查库校验，因为 geo_key 是前端传的，写进切片的键就是它本身；越权的
关键在于"不能碰别人市的键"，而 `浙江/杭州市/不存在区` 这种键只会污染自己市的
命名空间，读出来也是空，不构成越权。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

USERS_PATH = Path(os.environ.get("SO_FUNNEL_USERS", "/etc/so-funnel/users.json"))

ALGO = "pbkdf2_sha256"
ITERS = 240_000
# 生成密码用的字母表：去掉了 0/O/o/1/l/I 这些抄错率高的字符 —— 密码要靠人手
# 转达给 11 个地市负责人，少一个歧义字符就少一次"登不上"的电话
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_password(n: int = 14) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def hash_password(password: str, *, salt: Optional[bytes] = None,
                  iters: int = ITERS) -> Dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return {"algo": ALGO, "iters": iters,
            "salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, rec: Dict[str, Any]) -> bool:
    """常数时间比对。算法/字段不认就直接 False，不抛异常。"""
    if rec.get("algo") != ALGO:
        return False
    try:
        salt = bytes.fromhex(rec["salt"])
        expect = bytes.fromhex(rec["hash"])
        iters = int(rec.get("iters") or ITERS)
    except (KeyError, ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(dk, expect)


class User:
    __slots__ = ("name", "scope", "label", "rec")

    def __init__(self, name: str, scope: str, label: str, rec: Dict[str, Any]):
        self.name = name
        self.scope = scope or "*"
        self.label = label or name
        self.rec = rec

    @property
    def is_admin(self) -> bool:
        return self.scope == "*"

    def city(self) -> Optional[str]:
        return None if self.is_admin else self.scope

    def as_public(self) -> Dict[str, Any]:
        """给前端的自我描述，**不含任何密码材料**。"""
        return {"name": self.name, "label": self.label,
                "scope": self.scope, "is_admin": self.is_admin}


def load_users() -> Dict[str, User]:
    """
    读用户表。文件不存在/坏掉时返回空 dict —— 调用方据此回落到单账号模式，
    而不是把服务打挂（改坏了 users.json 不该让所有人都登不上）。
    """
    try:
        blob = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: Dict[str, User] = {}
    for u in (blob.get("users") or []):
        name = (u.get("name") or "").strip()
        if not name:
            continue
        out[name] = User(name, u.get("scope") or "*", u.get("label") or name, u)
    return out


def authorize_geo(user: Optional[User], geo_key: str) -> bool:
    """
    该用户能不能碰这个 geo_key。

    admin 全通。地市账号只允许第 2 段等于自己的市，且**不允许省级汇总**
    （geo_key 形如 '浙江//'，第 2 段为空）—— 省级视图会暴露其它地市的数字。
    """
    if user is None or user.is_admin:
        return True
    parts = (geo_key or "").split("/")
    city = parts[1] if len(parts) > 1 else ""
    return bool(city) and city == user.scope


def scoped_geo_tree(user: Optional[User],
                    tree: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    把地区树裁到该用户可见的范围，前端下拉框据此只列本市。
    在服务端裁而不是让前端自己藏：前端藏起来的东西改改 JS 就能点出来。
    """
    if tree is None or user is None or user.is_admin:
        return tree
    cities = [c for c in tree.get("cities", []) if c["city"] == user.scope]
    return {**tree, "cities": cities, "city_count": len(cities),
            "district_count": sum(len(c["districts"]) for c in cities)}
