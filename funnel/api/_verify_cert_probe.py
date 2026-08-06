"""只读探查：确认「认证/非认证」该用哪一列、怎么判定。

只跑 SELECT，连接串带 mode=ro，不可能写生产库。
跑法（服务器上）：/opt/so-funnel/venv/bin/python /opt/so-funnel/api/_verify_cert_probe.py
"""
import sqlite3

DB = "/opt/so-data-analytics/db/product_flow.db"
NP = "COALESCE(管理标签,'') <> '授牌服务商'"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for col in ("渠道客户类型", "协议类型", "分销商认证"):
    print(f"\n{'='*60}\n{col}  取值分布（已排除授牌）")
    try:
        rows = conn.execute(
            f"SELECT COALESCE({col},'<NULL>') v, COUNT(*) n, "
            f"SUM(CASE WHEN lower(COALESCE(服务商等级,'')) LIKE 'v_%' "
            f"    AND CAST(substr(lower(服务商等级),2,1) AS INTEGER) >= 2 "
            f"    THEN 1 ELSE 0 END) act "
            f"FROM provider_contract WHERE {NP} GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            print(f"  {r['v']:<28} {r['n']:>6}  其中V2+ {r['act']:>5}")
        print(f"  —— 共 {len(rows)} 种取值")
    except sqlite3.Error as e:
        print("  查询失败:", e)

# 用 LIKE '%认证%' 切分，看三列各自切出来的两侧规模
print(f"\n{'='*60}\nLIKE '%认证%' 切分对比")
for col in ("渠道客户类型", "协议类型", "分销商认证"):
    try:
        r = conn.execute(
            f"SELECT SUM(CASE WHEN COALESCE({col},'') LIKE '%认证%' THEN 1 ELSE 0 END) cert, "
            f"SUM(CASE WHEN COALESCE({col},'') NOT LIKE '%认证%' THEN 1 ELSE 0 END) nocert, "
            f"SUM(CASE WHEN COALESCE({col},'')='' THEN 1 ELSE 0 END) empty, "
            f"COUNT(*) total FROM provider_contract WHERE {NP}"
        ).fetchone()
        print(f"  {col:<12} 认证 {r['cert']:>6} | 非认证 {r['nocert']:>6} "
              f"| 其中空值 {r['empty']:>5} | 合计 {r['total']}")
    except sqlite3.Error as e:
        print(f"  {col}: 查询失败 {e}")

# 三列一致性：认证判定是否互相吻合
print(f"\n{'='*60}\n三列认证判定交叉表（渠道客户类型 × 协议类型）")
try:
    for r in conn.execute(
        f"SELECT COALESCE(渠道客户类型,'<NULL>') a, COALESCE(协议类型,'<NULL>') b, COUNT(*) n "
        f"FROM provider_contract WHERE {NP} GROUP BY 1,2 ORDER BY n DESC LIMIT 15"
    ):
        print(f"  {r['a']:<24} × {r['b']:<26} {r['n']:>6}")
except sqlite3.Error as e:
    print("  查询失败:", e)

conn.close()
