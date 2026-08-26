"""流失客户三因子：跑动 / 挽回 / 跑动挽回。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from api import factors as f
from api import prod_db as p


def _mk(path: Path) -> None:
    c = sqlite3.connect(str(path))
    c.executescript("""
    CREATE TABLE provider_contract(
      客户编码 TEXT, 客户城市 TEXT, 客户区县 TEXT,
      服务商等级 TEXT, 管理标签 TEXT, 签约日期 TEXT, 激活时间 TEXT,
      协议类型 TEXT
    );
    CREATE TABLE install_redpack(
      上线客户编码 TEXT, 上线时间 TEXT, 产品现有分销价 REAL,
      国内产品线二级 TEXT, 产品名称 TEXT, 产品序列号 TEXT
    );
    CREATE TABLE visit_record(
      客户编码 TEXT, 拜访客户城市 TEXT, 拜访客户区县 TEXT,
      打卡人姓名 TEXT, 打卡人所属公司 TEXT,
      活动创建时间 TEXT, 拜访时间 TEXT
    );
    """)
    # A1: 25 有 SO、26 期前无 SO → 类型 A 流失；7/10 拜访；7/15 SO → 挽回+跑动挽回
    # A2: 类型 A 流失；7/10 拜访；无 SO → 只计跑动
    # B1: 26 年 V2，最近 SO 在 3 月（>90 天）→ 类型 B；7/05 拜访；7/20 SO
    # OK1: 26 年有 SO，非流失
    # NC1: 非认证，不算
    c.executemany(
        "INSERT INTO provider_contract VALUES(?,?,?,?,?,?,?,?)",
        [
            ("A1", "杭州市", "西湖区", "v1服务商", "", "2024-01-01", None,
             "认证SMB服务商协议"),
            ("A2", "杭州市", "西湖区", "v0服务商", "", "2024-01-01", None,
             "认证SMB服务商协议"),
            ("B1", "杭州市", "西湖区", "v2服务商", "", "2025-06-01", "2026-02-01",
             "认证SMB服务商协议"),
            ("OK1", "杭州市", "西湖区", "v2服务商", "", "2025-01-01", "2026-01-10",
             "认证SMB服务商协议"),
            ("NC1", "杭州市", "西湖区", "v1服务商", "", "2024-01-01", None,
             "SMB服务商协议"),
        ],
    )
    c.executemany(
        "INSERT INTO install_redpack VALUES(?,?,?,?,?,?)",
        [
            ("A1", "2025-06-01", 1000, "IPC", "摄像", "s1"),
            ("A2", "2025-08-01", 1000, "IPC", "摄像", "s2"),
            ("B1", "2026-03-01", 1000, "IPC", "摄像", "s3"),  # 距 7 月 >90 天
            ("OK1", "2026-07-05", 1000, "IPC", "摄像", "s4"),
            ("OK1", "2026-05-15", 1000, "IPC", "摄像", "s4b"),  # 期前 90 天内有 SO，非流失
            ("NC1", "2025-05-01", 1000, "IPC", "摄像", "s5"),
            # 本期挽回 SO
            ("A1", "2026-07-15", 2000, "IPC", "摄像", "s6"),
            ("B1", "2026-07-20", 2000, "IPC", "摄像", "s7"),
        ],
    )
    c.executemany(
        "INSERT INTO visit_record VALUES(?,?,?,?,?,?,?)",
        [
            ("A1", "杭州市", "西湖区", "张三", "", "2026-07-10 10:00:00", "2026-07-10"),
            ("A2", "杭州市", "西湖区", "李四", "代理A", "2026-07-10 11:00:00", "2026-07-10"),
            ("B1", "杭州市", "西湖区", "王五", "", "2026-07-05 09:00:00", "2026-07-05"),
            ("OK1", "杭州市", "西湖区", "赵六", "", "2026-07-08 09:00:00", "2026-07-08"),
            ("NC1", "杭州市", "西湖区", "钱七", "", "2026-07-10 09:00:00", "2026-07-10"),
        ],
    )
    c.commit()
    c.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "prod.db"
    _mk(tmp)
    p.PROD_DB = tmp
    assert p.available()

    JUL = ("2026-07-01", "2026-07-31")
    v = f.compute("a2t_churn_visit", "杭州市", "西湖区", *JUL)
    # A1+A2+B1 各 1 次；OK1/NC1 不计 → 3
    assert v["value"] == 3, v
    assert v["customers"] == 3, v
    print("✅ 流失客户跑动", v["value"])

    r = f.compute("a2t_churn_recover", "杭州市", "西湖区", *JUL)
    # 期前流失 A1/A2/B1；本期有 SO 的 A1+B1 → 2（A2 无 SO）
    assert r["value"] == 2, r
    print("✅ 流失客户挽回", r["value"], "as_of", r.get("as_of"))

    vr = f.compute("a2t_churn_visit_recover", "杭州市", "西湖区", *JUL)
    # A1 拜访后有 SO、B1 拜访后有 SO；A2 无 → 2
    assert vr["value"] == 2, vr
    print("✅ 流失客户跑动挽回", vr["value"])

    # 空周期
    z = f.compute("a2t_churn_visit", "杭州市", None, "2026-09-01", "2026-09-30")
    assert z["value"] == 0, z
    print("✅ 空周期返回 0")

    print("\n全部校验通过")


if __name__ == "__main__":
    main()
