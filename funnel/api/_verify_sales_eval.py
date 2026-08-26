"""合成库校验：拜访目的映射、7 日 SO 关联、分档规定动作。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from api import db as funnel_db
from api import prod_db, sales_eval


def _mk_prod(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    # 列名必须与 sales_eval 取数 SQL 一字不差
    conn.executescript("""
    CREATE TABLE visit_record(
      客户编码 TEXT, 拜访客户城市 TEXT, 拜访客户区县 TEXT,
      打卡人姓名 TEXT, 打卡人所属公司 TEXT, 拜访目的 TEXT,
      渠道客户类型 TEXT, 拜访类型 TEXT,
      活动创建时间 TEXT, 拜访时间 TEXT,
      距离偏离_米 REAL, 打卡异常类型 TEXT
    );
    CREATE TABLE provider_contract(
      客户编码 TEXT, 客户城市 TEXT, 客户区县 TEXT,
      签约日期 TEXT, 激活时间 TEXT, 服务商等级 TEXT, 管理标签 TEXT
    );
    CREATE TABLE install_redpack(
      上线客户编码 TEXT, 上线时间 TEXT, 产品现有分销价 REAL,
      国内产品线二级 TEXT, 产品名称 TEXT, 产品序列号 TEXT
    );
    CREATE TABLE distribution_info(
      客户编码_下级 TEXT, 客户所在城市_下级 TEXT, 客户所在区县_下级 TEXT,
      提交铺货时间 TEXT
    );
    CREATE TABLE promotion_meeting(
      参会客户编码 TEXT, 活动开始时间 TEXT
    );
    """)
    conn.executemany(
        "INSERT INTO visit_record VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # C1 意向 + 电话筛选 + 当日签约现场 + 当日铺货
            ("C1", "杭州市", "西湖区", "张三", "代理商A", "新签", "意向服务商", "电话沟通",
             "2026-07-08 10:00:00", "2026-07-08", 0, ""),
            ("C1", "杭州市", "西湖区", "张三", "代理商A", "新签", "意向服务商", "现场沟通",
             "2026-07-10 10:00:00", "2026-07-10", 0, ""),
            ("C2", "杭州市", "西湖区", "张三", "代理商A", "行销", "认证SMB服务商", "现场沟通",
             "2026-07-11 10:00:00", "2026-07-11", 0, ""),
            # C2 SO(7/12) 后 3 日内回访 + 30 日内再访
            ("C2", "杭州市", "西湖区", "张三", "代理商A", "激活", "认证SMB服务商", "现场沟通",
             "2026-07-13 10:00:00", "2026-07-13", 0, ""),
            ("C2", "杭州市", "西湖区", "张三", "代理商A", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-25 10:00:00", "2026-07-25", 0, ""),
            ("C3", "杭州市", "西湖区", "张三", "代理商A", None, "意向服务商", "微信沟通",
             "2026-07-12 10:00:00", "2026-07-12", 0, ""),
            ("C4", "杭州市", "西湖区", "张三", "代理商A", "激活", "认证SMB服务商", "现场沟通",
             "2026-07-05 10:00:00", "2026-07-05", 0, ""),
            ("C4", "杭州市", "西湖区", "张三", "代理商A", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-22 10:00:00", "2026-07-22", 0, ""),
            ("C5", "杭州市", "滨江区", "李四", "代理商B", "新签", "意向服务商", "现场沟通",
             "2026-07-15 10:00:00", "2026-07-15", 0, ""),
            ("C9", "杭州市", "西湖区", "王五", "", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-20 10:00:00", "2026-07-20", 0, ""),
            # C6：签约后跑 3 次无 SO → 滞留
            ("C6", "杭州市", "西湖区", "张三", "代理商A", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-02 10:00:00", "2026-07-02", 0, ""),
            ("C6", "杭州市", "西湖区", "张三", "代理商A", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-08 10:00:00", "2026-07-08", 0, ""),
            ("C6", "杭州市", "西湖区", "张三", "代理商A", "复购", "认证SMB服务商", "现场沟通",
             "2026-07-18 10:00:00", "2026-07-18", 0, ""),
        ],
    )
    conn.executemany(
        "INSERT INTO provider_contract VALUES(?,?,?,?,?,?,?)",
        [
            ("C1", "杭州市", "西湖区", "2026-07-10", None, "v0服务商", ""),
            ("C2", "杭州市", "西湖区", "2026-01-01", "2026-02-01", "v2服务商", ""),
            ("C4", "杭州市", "西湖区", "2026-06-01", "2026-06-15", "v1服务商", ""),
            ("C5", "杭州市", "滨江区", "2026-07-15", None, "v0服务商", ""),
            ("C6", "杭州市", "西湖区", "2026-03-01", "2026-04-01", "v0服务商", ""),
        ],
    )
    conn.executemany(
        "INSERT INTO install_redpack VALUES(?,?,?,?,?,?)",
        [
            ("C2", "2026-07-12", 5000, "IPC", "摄像A", "SN1"),
            ("C4", "2026-07-02", 3000, "IPC", "摄像B", "SN2"),
            ("C4", "2026-07-02", 8000, "IPC", "摄像C", "SN3"),
        ],
    )
    conn.execute(
        "INSERT INTO distribution_info VALUES(?,?,?,?)",
        ("C1", "杭州市", "西湖区", "2026-07-10"),
    )
    conn.execute(
        "INSERT INTO promotion_meeting VALUES(?,?)",
        ("C2", "2026-07-20"),
    )
    conn.commit()
    conn.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    prod = tmp / "prod.db"
    fdb = tmp / "funnel.db"
    _mk_prod(prod)

    funnel_db.DB_PATH = fdb
    funnel_db.init_schema()
    prod_db.PROD_DB = prod
    assert prod_db.available(), "合成生产库不可用"

    pmap = funnel_db.load_visit_purpose_map()
    assert "新签" in pmap and pmap["新签"]["necessary"] is True
    assert pmap["行销"]["necessary"] is False
    print("✅ 目的映射种子")

    d = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A", person="张三")
    k = d["detail"]["kpis"]
    assert k["visits"] >= 7, k
    assert k["sign_count"] >= 1, k
    assert d["org"] == "代理商A", d["org"]
    assert d["person"] == "张三", d["person"]
    print("✅ 个人 KPI / 映射 / SO 关联")

    connect = next(r for r in d["detail"]["matrix"] if r["label_id"] == "connect")
    assert connect["visits"] >= 2, connect
    assert connect["roi_text"] and "/(" in connect["roi_text"], connect
    dist = next(r for r in d["detail"]["matrix"] if r["label_id"] == "distribute")
    assert dist["visits"] >= 1, dist
    order = [r["label_id"] for r in d["detail"]["matrix"]]
    assert order[:2] == ["connect", "distribute"], order
    print("✅ 目的矩阵：建联/铺货口径 + ROI")

    sop = d["detail"]["sop"]
    ids = [i["id"] for i in sop["items"]]
    assert ids == [
        "prospect_screen", "prospect_visit7",
        "v0_dense", "v0_stuck",
        "v1_revisit_3d", "v1_revisit_30",
        "v2_face_q", "v2_wechat_m",
        "v3_meeting_q", "v3_wechat_w",
    ], ids
    assert all(i["status"] != "pending" for i in sop["items"]), sop["items"]
    stuck = next(i for i in sop["items"] if i["id"] == "v0_stuck")
    assert stuck["status"] == "metric" and stuck["value"] >= 1, stuck
    assert stuck["in_rate"] is False
    v1a = next(i for i in sop["items"] if i["id"] == "v1_revisit_3d")
    assert v1a["status"] == "auto" and v1a["pass"] is True, v1a
    v1b = next(i for i in sop["items"] if i["id"] == "v1_revisit_30")
    assert v1b["status"] == "auto" and v1b["pass"] is True, v1b
    print("✅ 分档规定动作十条 / V1 回访链 / 滞留监测")

    # 归属=全部 → 默认组织「全部」，标题口径 org_label=全部
    d_all = sales_eval.build_sales_eval("浙江/杭州市/", "2026-07", side="all")
    org_keys = [o["key"] for o in d_all["orgs"]]
    assert org_keys[0] == "all", org_keys
    assert "dahua" in org_keys and "代理商A" in org_keys, org_keys
    assert d_all["org"] == "all", d_all["org"]
    assert d_all["org_label"] == "全部", d_all["org_label"]
    assert d_all["person"] is None
    assert d_all["detail"]["name"] == "全部", d_all["detail"]
    assert d_all["detail"]["kpis"]["visits"] >= 10, d_all["detail"]["kpis"]
    assert {"张三", "李四", "王五"} <= set(d_all["detail"]["people"]), d_all["detail"]["people"]
    print("✅ 归属全部 → 组织默认「全部」+ 全市汇总")

    # 选具体代理组织
    d2 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A")
    assert d2["org"] == "代理商A"
    assert d2["person"] is None
    assert d2["detail"]["kpis"]["visits"] >= 7
    assert "张三" in d2["detail"]["people"]
    print("✅ 选具体代理商 → 整组织")

    # 归属=代理 → 默认「全部代理商」；个人列表含所有代理业务员
    d_dl = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", side="dealer")
    assert d_dl["org"] == "all_dealers", d_dl["org"]
    assert d_dl["orgs"][0]["key"] == "all_dealers"
    assert d_dl["org_label"] == "全部代理商"
    assert d_dl["person"] is None
    people_keys = {p["key"] for p in d_dl["people"]}
    assert "张三" in people_keys and "李四" in people_keys, people_keys
    assert "王五" not in people_keys, people_keys  # 大华不在
    assert d_dl["detail"]["kpis"]["visits"] >= 8, d_dl["detail"]["kpis"]
    print("✅ 归属代理 → 全部代理商 + 所有代理业务员")

    # side=dahua → 仅大华
    d3 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", side="dahua")
    assert d3["org"] == "dahua" and d3["org_label"] == "大华"
    assert d3["person"] is None
    assert "王五" in [p["key"] for p in d3["people"]]
    d3b = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", side="dahua",
        subject="dahua:王五")
    assert d3b["person"] == "王五"
    print("✅ side=dahua / 旧前缀兼容个人")

    d4 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="dahua", person="王五")
    assert d4["detail"]["kpis"]["visits"] >= 1
    print("✅ org=dahua + person")

    d5 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A", person="不存在的人")
    assert d5["person"] is None
    d6 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", side="dealer", org="dahua")
    assert d6["org"] == "all_dealers", d6["org"]  # dahua 非法，回落汇总
    print("✅ 非法 key 回落")

    d7 = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", mode="dealer", subject="代理商A")
    assert d7["org"] == "代理商A" and d7["person"] is None
    print("✅ 旧 mode/subject 兼容")

    # 评估库：阈值按对象落盘，切片/小结可取回
    from api import sales_eval_store as store
    store.save_preset("浙江/杭州市/", "2026-07", "all", "all", None,
                      "prospect_visit7", 5)
    d_g = sales_eval.build_sales_eval("浙江/杭州市/", "2026-07", side="all")
    vis7 = next(i for i in d_g["detail"]["sop"]["items"]
                if i["id"] == "prospect_visit7")
    assert vis7["preset_value"] == 5, vis7
    assert vis7["preset_source"] == "group", vis7
    # 群体阈值被具体组织继承
    d_a = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A")
    vis7a = next(i for i in d_a["detail"]["sop"]["items"]
                 if i["id"] == "prospect_visit7")
    assert vis7a["preset_value"] == 5 and vis7a["preset_source"] == "group", vis7a

    store.save_preset("浙江/杭州市/", "2026-07", "all", "代理商A", "张三",
                      "v0_dense", 2)
    d_p = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A", person="张三")
    dense = next(i for i in d_p["detail"]["sop"]["items"] if i["id"] == "v0_dense")
    assert dense["preset_value"] == 2 and dense["preset_source"] == "person", dense
    # 组织层未覆盖，不受张三影响
    d_org = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A")
    dense_org = next(i for i in d_org["detail"]["sop"]["items"]
                     if i["id"] == "v0_dense")
    assert dense_org["preset_source"] != "person", dense_org

    sales_eval.patch_sales_eval(
        "浙江/杭州市/", "2026-07", side="all", org="代理商A", person="张三",
        note="张三本月重点盯密联", rebuild=False)
    d_n = sales_eval.build_sales_eval(
        "浙江/杭州市/", "2026-07", org="代理商A", person="张三")
    assert d_n["saved"]["note"] == "张三本月重点盯密联", d_n["saved"]
    assert d_n["saved"]["updated_at"], d_n["saved"]
    print("✅ 评估库：阈值继承 + 小结/切片回读")

    print("\n全部校验通过")


if __name__ == "__main__":
    main()
