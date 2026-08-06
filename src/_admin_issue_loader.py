"""Admin 待办工作流 — 数据访问层

3 张表:
  - admin_issue       问题主表
  - admin_issue_task  任务清单
  - admin_issue_log   操作日志(自动追加)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402


STATUSES = ['待调研', '调研中', '思路就绪', '任务定稿', '执行中', '待验证', '已关闭', '已归档']
PRIORITIES = ['P0', 'P1', 'P2']
LABELS = ['口径修正', '数据异常', '新功能', '优化', '数据缺失', '算法升级', '其他']
TASK_STATUSES = ['待办', '进行中', '完成', '取消']
TASK_OWNERS = ['AI', 'Admin', 'AI+Admin']


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _conn():
    return sqlite3.connect(str(DB_PATH))


# ─────────────────────────────────────────────────────────
# Issue CRUD
# ─────────────────────────────────────────────────────────

def list_issues(status: Optional[str] = None, label: Optional[str] = None) -> pd.DataFrame:
    """列表所有 issue,附任务进度"""
    conn = _conn()
    try:
        where = ['1=1']
        params = []
        if status and status != '全部':
            where.append('i.状态 = ?')
            params.append(status)
        if label and label != '全部':
            where.append("i.标签 LIKE '%' || ? || '%'")
            params.append(label)
        sql = f"""
            SELECT i.id, i.标题, i.状态, i.优先级, i.标签,
                   COALESCE(t.总任务, 0) AS 总任务,
                   COALESCE(t.已完成, 0) AS 已完成,
                   i.关联上下文,
                   i.创建人, i.创建时间, i.关闭时间
              FROM admin_issue i
              LEFT JOIN (
                SELECT issue_id,
                       COUNT(*) AS 总任务,
                       SUM(CASE WHEN 状态 = '完成' THEN 1 ELSE 0 END) AS 已完成
                  FROM admin_issue_task GROUP BY issue_id
              ) t ON t.issue_id = i.id
             WHERE {' AND '.join(where)}
             ORDER BY
               CASE WHEN i.状态 = '已关闭' OR i.状态 = '已归档' THEN 1 ELSE 0 END,
               CASE i.优先级 WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
               i.创建时间 DESC
        """
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def get_issue(issue_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM admin_issue WHERE id = ?", (issue_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("PRAGMA table_info(admin_issue)").fetchall()]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(admin_issue)").fetchall()]
        return dict(zip(cols, row))
    finally:
        conn.close()


def create_issue(标题: str, 优先级: str, 标签: str, 背景描述: str,
                 关联上下文: str, 创建人: str) -> int:
    conn = _conn()
    try:
        now = _now()
        cur = conn.execute("""
            INSERT INTO admin_issue
              (标题, 状态, 优先级, 标签, 背景描述, 关联上下文, 创建人, 创建时间, 更新人, 更新时间)
            VALUES (?, '待调研', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (标题, 优先级, 标签, 背景描述, 关联上下文, 创建人, now, 创建人, now))
        issue_id = cur.lastrowid
        conn.execute("""
            INSERT INTO admin_issue_log (issue_id, 时间, 类型, 操作人, 内容)
            VALUES (?, ?, '创建', ?, ?)
        """, (issue_id, now, 创建人, f"创建 issue:{标题}"))
        conn.commit()
        return issue_id
    finally:
        conn.close()


def update_issue(issue_id: int, **fields):
    """更新 issue 字段(状态/优先级/标签/背景描述/数据调研/思路/关联上下文)"""
    conn = _conn()
    try:
        allowed = {'状态', '优先级', '标签', '背景描述', '数据调研', '思路', '关联上下文'}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f'"{k}" = ?')
                params.append(v)
        if not sets:
            return
        now = _now()
        operator = fields.get('_operator', 'Admin')
        sets.extend(['更新人 = ?', '更新时间 = ?'])
        params.extend([operator, now])
        if fields.get('状态') in ('已关闭', '已归档'):
            sets.append('关闭时间 = ?')
            params.append(now)
        params.append(issue_id)
        conn.execute(f"UPDATE admin_issue SET {', '.join(sets)} WHERE id = ?", params)
        # 日志
        for k, v in fields.items():
            if k in allowed:
                conn.execute("""
                    INSERT INTO admin_issue_log (issue_id, 时间, 类型, 操作人, 内容)
                    VALUES (?, ?, ?, ?, ?)
                """, (issue_id, now,
                       '状态变更' if k == '状态' else k,
                       operator,
                       f"{k} → {v[:100] if isinstance(v, str) else v}"))
        conn.commit()
    finally:
        conn.close()


def delete_issue(issue_id: int):
    """删除 issue + 级联删除 task/log(物理删,谨慎)"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM admin_issue_task WHERE issue_id = ?", (issue_id,))
        conn.execute("DELETE FROM admin_issue_log WHERE issue_id = ?", (issue_id,))
        conn.execute("DELETE FROM admin_issue WHERE id = ?", (issue_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Task CRUD
# ─────────────────────────────────────────────────────────

def list_tasks(issue_id: int) -> pd.DataFrame:
    conn = _conn()
    try:
        return pd.read_sql("""
            SELECT id, 序号, 描述, 责任方, 状态, 输出备注, 创建时间, 完成时间
              FROM admin_issue_task WHERE issue_id = ?
             ORDER BY 序号, id
        """, conn, params=(issue_id,))
    finally:
        conn.close()


def create_task(issue_id: int, 描述: str, 责任方: str = 'AI', 序号: int = 0):
    conn = _conn()
    try:
        if 序号 == 0:
            row = conn.execute(
                "SELECT COALESCE(MAX(序号), 0) FROM admin_issue_task WHERE issue_id = ?",
                (issue_id,)
            ).fetchone()
            序号 = (row[0] or 0) + 1
        conn.execute("""
            INSERT INTO admin_issue_task (issue_id, 序号, 描述, 责任方, 状态, 创建时间)
            VALUES (?, ?, ?, ?, '待办', ?)
        """, (issue_id, 序号, 描述, 责任方, _now()))
        conn.commit()
    finally:
        conn.close()


def update_task(task_id: int, **fields):
    """更新 task 字段(描述/责任方/状态/输出备注/序号)"""
    conn = _conn()
    try:
        allowed = {'描述', '责任方', '状态', '输出备注', '序号'}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f'"{k}" = ?')
                params.append(v)
        if not sets:
            return
        if fields.get('状态') == '完成':
            sets.append('完成时间 = ?')
            params.append(_now())
        params.append(task_id)
        conn.execute(f"UPDATE admin_issue_task SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id: int):
    conn = _conn()
    try:
        conn.execute("DELETE FROM admin_issue_task WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Log
# ─────────────────────────────────────────────────────────

def list_logs(issue_id: int, limit: int = 50) -> pd.DataFrame:
    conn = _conn()
    try:
        return pd.read_sql("""
            SELECT 时间, 类型, 操作人, 内容
              FROM admin_issue_log WHERE issue_id = ?
             ORDER BY 时间 DESC LIMIT ?
        """, conn, params=(issue_id, limit))
    finally:
        conn.close()


def append_log(issue_id: int, 类型: str, 内容: str, 操作人: str = 'Admin'):
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO admin_issue_log (issue_id, 时间, 类型, 操作人, 内容)
            VALUES (?, ?, ?, ?, ?)
        """, (issue_id, _now(), 类型, 操作人, 内容))
        conn.commit()
    finally:
        conn.close()
