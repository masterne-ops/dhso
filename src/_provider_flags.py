#!/usr/bin/env python3
"""服务商价值标记 — 管理标签口径的中央过滤器。

业务口径(金总 2026-07-13 定):
  provider_contract.管理标签 = '授牌服务商' → **无价值客户**。
  除非去掉该标签,否则不应出现在任何重点任务分配中,业务员也不应跑动这些客户。
  (2026-07 全省 9,767 家签约服务商中约 2,490 家为授牌,占 ~26%)

应用点(新增任务/名单生成处都应调用):
  - _gtm.generate_candidates(GTM 推荐名单,SQL 内直接排除)
  - _task_manager.generate_tasks(月度跑动任务)
  - page02 服务商智能分类(派单清单)
  - _pending_activation.get_pending_board(待激活跟进看板)
标签来源 = 服务商签约明细表(周更全量替换,管理标签列 2026-07-13 起入库)。
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "product_flow.db"
WORTHLESS_TAG = '授牌服务商'


def worthless_codes(conn: sqlite3.Connection = None) -> set:
    """管理标签=授牌服务商 的 客户编码 集合(str)。conn 不传则自建。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT 客户编码 FROM provider_contract "
            "WHERE 管理标签 = ? AND 客户编码 IS NOT NULL", (WORTHLESS_TAG,)).fetchall()
        return set(str(r[0]) for r in rows)
    except sqlite3.Error:
        return set()   # 列不存在等异常时不拦截(宁可多派不静默丢功能)
    finally:
        if own:
            conn.close()


def worthless_names(conn: sqlite3.Connection = None) -> set:
    """管理标签=授牌服务商 的 客户名称 集合(str),供只有名称的场景匹配。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT 客户名称 FROM provider_contract "
            "WHERE 管理标签 = ? AND 客户名称 IS NOT NULL", (WORTHLESS_TAG,)).fetchall()
        return set(str(r[0]).strip() for r in rows)
    except sqlite3.Error:
        return set()
    finally:
        if own:
            conn.close()
