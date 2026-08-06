#!/usr/bin/env python3
"""GTM 管理数据层 — 月度产品攻坚名单 + 动作跟踪 + SO 成效 + 考核

三条客户任务线之三(RFM派单=活跃度 / 待激活=破V2 / GTM=产品画像产单):
  - GTM 对象 = 可能产单、能卖 夜视王/无线/场景化 的服务商(product_focus 物料号白名单口径)
  - 名单:系统推荐(机会+培育画像) + 手动调整,入库 gtm_target,UNIQUE(任务年月,客户编码)
  - 完成:任务月内有效跑动 + 拜访后 7 天内 ≥1 个动作(推广会/铺货/红包/上线)
  - 成效:完成对象自首访日起 90 天 SO(install_redpack 服务商级口径,产品现有分销价)
  - 全部动作/成效从事实表实时算,不写状态字段(永不过期,仿 page34 范式)

重复 GTM 政策(金先生 2026-06 定):
  - 毕业:专项 >5 台自动升核心,退出推荐(画像天然实现)
  - 冷却:GTM 完成但至今无窗口 SO → 近 2 个任务月内不再自动推荐,之后回池
  - 剔除:手动剔除(gtm_exclude 表)= 永久不推荐,可恢复
  - 顺延:上月未跑动 → 自动滚入下月名单标「上月顺延」,不占推荐 Top N 名额

口径备忘:
  - 客户→分销经理 = provider_contract.客户所有者;名册 = sales_org_structure 职位='分销经理'
  - 剔马甲 vest_account;拜访排除 _打卡异常无效;台数 COUNT(DISTINCT 产品序列号)
  - 服务商级 SO 用 install_redpack(product_flow 无线 66% 无法归属服务商,不用)
"""
from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402

GTM_FOCUS = ['夜视王', '无线', '场景化']
ACTION_WINDOW_DAYS = 7     # 拜访后动作观察窗
SO_WINDOW_DAYS = 90        # GTM 完成后 SO 观察窗
ACTIVE_DAYS = 90           # 活跃判定:近 90 天有上线或有效拜访
FOCUS_LOOKBACK_DAYS = 180  # 专项画像回看期
NURTURE_MAX_UNITS = 5      # 该专项 1~5 台=培育,0 台=机会,>5 台=核心(不进名单)


def _conn():
    return sqlite3.connect(str(DB_PATH))


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gtm_target (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            任务年月   TEXT NOT NULL,
            分销经理   TEXT NOT NULL,
            客户编码   TEXT NOT NULL,
            客户名称   TEXT, 城市 TEXT, 区县 TEXT, 所属代理商 TEXT,
            推荐产品类 TEXT,              -- csv: 夜视王,无线,场景化(至少一类)
            推荐依据   TEXT,
            来源       TEXT DEFAULT '系统推荐',   -- 系统推荐 / 手动添加
            分配时间   TEXT,
            UNIQUE(任务年月, 客户编码)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gtm_month ON gtm_target(任务年月)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gtm_exclude (
            客户编码 TEXT PRIMARY KEY,
            客户名称 TEXT, 原因 TEXT, 操作人 TEXT, 剔除时间 TEXT
        )""")
    conn.commit()


def list_managers(conn=None) -> list[str]:
    """分销经理名册(20人),来自组织架构表。"""
    own = conn or _conn()
    try:
        return [r[0] for r in own.execute(
            "SELECT 姓名 FROM sales_org_structure WHERE 职位='分销经理' ORDER BY 姓名")]
    except sqlite3.Error:
        return []
    finally:
        if conn is None:
            own.close()


# ══════════════════ 剔除 / 冷却 / 顺延 ══════════════════

def exclude_customer(客户编码: str, 客户名称='', 原因='', 操作人=''):
    """手动剔除 = 永久不再自动推荐(可恢复)。"""
    conn = _conn()
    try:
        _ensure_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO gtm_exclude (客户编码,客户名称,原因,操作人,剔除时间) "
            "VALUES (?,?,?,?,?)",
            (str(客户编码), 客户名称, 原因, 操作人,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()


def unexclude_customer(客户编码: str):
    conn = _conn()
    try:
        _ensure_table(conn)
        conn.execute("DELETE FROM gtm_exclude WHERE 客户编码=?", (str(客户编码),))
        conn.commit()
    finally:
        conn.close()


def list_excluded() -> pd.DataFrame:
    conn = _conn()
    try:
        _ensure_table(conn)
        df = pd.read_sql("SELECT * FROM gtm_exclude ORDER BY 剔除时间 DESC", conn)
        if not df.empty:
            df['客户编码'] = df['客户编码'].astype(str)
        return df
    finally:
        conn.close()


def _prev_months(year_month: str, n: int = 2) -> list[str]:
    return [str(pd.Period(year_month, freq='M') - i) for i in range(1, n + 1)]


def cooling_codes(year_month: str) -> set[str]:
    """冷却池:近 2 个任务月内 GTM 完成、但至今窗口内无 SO 的客户 → 不自动推荐。"""
    cool = set()
    for pm in _prev_months(year_month, 2):
        board = get_gtm_board(pm)
        if board.empty:
            continue
        done = board[board['GTM完成']]
        if done.empty:
            continue
        summary, _ = get_so_effect(pm)
        has_so = set(summary['客户编码'].astype(str)) if not summary.empty else set()
        cool |= set(done['客户编码'].astype(str)) - has_so
    return cool


def get_rollover_candidates(year_month: str) -> pd.DataFrame:
    """上月未跑动的对象 → 顺延到本月(标「上月顺延」,不占推荐名额)。已剔除/已在本月的不顺延。"""
    pm = _prev_months(year_month, 1)[0]
    board = get_gtm_board(pm)
    if board.empty:
        return pd.DataFrame()
    miss = board[~board['跑动完成']].copy()
    if miss.empty:
        return pd.DataFrame()
    excl = set(list_excluded()['客户编码']) if not list_excluded().empty else set()
    cur = set(get_targets(year_month)['客户编码']) if not get_targets(year_month).empty else set()
    miss = miss[~miss['客户编码'].isin(excl | cur)]
    if miss.empty:
        return pd.DataFrame()
    out = miss[['分销经理', '客户编码', '客户名称', '城市', '区县', '所属代理商',
                '推荐产品类']].copy()
    out['推荐依据'] = f'{pm} 未跑动顺延'
    out['来源'] = '上月顺延'
    return out.reset_index(drop=True)


# ══════════════════ 名单生成(系统推荐) ══════════════════

def generate_candidates(year_month: str, per_limit: int = 10) -> pd.DataFrame:
    """按"机会+培育"画像推荐每个分销经理 top N 客户。

    候选 = 签约服务商(剔马甲) ∩ 客户所有者∈分销经理名册 ∩ 近90天活跃(有上线或有效拜访)
    对每个专项:近180天上线 0 台=机会 / 1~5 台=培育 / >5 台=核心(该专项不推荐)
    推荐产品类 = 机会∪培育 命中的专项(至少 1 个才入选);排序=近90天上线货值 desc。
    """
    conn = _conn()
    try:
        _ensure_table(conn)
        managers = list_managers(conn)
        if not managers:
            return pd.DataFrame()
        ph = ','.join('?' * len(managers))

        base = pd.read_sql(f"""
            SELECT pc.客户编码, pc.客户名称, pc.客户城市 AS 城市, pc.客户区县 AS 区县,
                   pc.上级分销商名称 AS 所属代理商, pc.客户所有者 AS 分销经理,
                   pc.渠道客户类型
              FROM provider_contract pc
              LEFT JOIN vest_account va ON va.服务商客户编码 = pc.客户编码
             WHERE va.服务商客户编码 IS NULL
               AND pc.客户编码 IS NOT NULL
               AND pc.客户所有者 IN ({ph})
               -- 授牌服务商 = 无价值客户(管理标签口径,见 _provider_flags),不进任何重点任务
               AND COALESCE(pc.管理标签,'') != '授牌服务商'
        """, conn, params=managers)
        base['客户编码'] = base['客户编码'].astype(str)

        # 近90天上线(货值/台数) — 活跃信号 + 排序依据
        act = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码,
                   ROUND(SUM(产品现有分销价)/10000.0, 2) AS 近90天货值_万,
                   COUNT(DISTINCT 产品序列号) AS 近90天台数
              FROM install_redpack_v
             WHERE 上线日期 >= date('now', '-{ACTIVE_DAYS} day')
             GROUP BY 1
        """, conn)
        act['客户编码'] = act['客户编码'].astype(str)

        # 近90天有效拜访(排除已知地址异常 + 可疑假打卡,口径同 _task_manager)
        vis = pd.read_sql(f"""
            SELECT 客户编码, COUNT(*) AS 近90天拜访
              FROM visit_record_v
             WHERE 拜访日期 >= date('now', '-{ACTIVE_DAYS} day')
               AND COALESCE(_打卡异常无效, 0) = 0 AND COALESCE(_真异常打卡, 0) = 0
             GROUP BY 1
        """, conn)
        vis['客户编码'] = vis['客户编码'].astype(str)

        # 各专项近180天上线台数
        foc = pd.read_sql(f"""
            SELECT ir.上线客户编码 AS 客户编码, fc.专项,
                   COUNT(DISTINCT ir.产品序列号) AS 台数
              FROM install_redpack_v ir
              JOIN product_focus fc ON fc.物料号 = ir.物料号
             WHERE ir.上线日期 >= date('now', '-{FOCUS_LOOKBACK_DAYS} day')
             GROUP BY 1, 2
        """, conn)
        foc['客户编码'] = foc['客户编码'].astype(str)
        foc_p = (foc.pivot_table(index='客户编码', columns='专项', values='台数',
                                 aggfunc='sum', fill_value=0)
                 .reindex(columns=GTM_FOCUS, fill_value=0))

        df = (base.merge(act, on='客户编码', how='left')
                  .merge(vis, on='客户编码', how='left')
                  .merge(foc_p, on='客户编码', how='left'))
        for c in ['近90天货值_万', '近90天台数', '近90天拜访', *GTM_FOCUS]:
            df[c] = df[c].fillna(0)

        # 活跃 gate
        df = df[(df['近90天台数'] > 0) | (df['近90天拜访'] > 0)]
        if df.empty:  # 候选滤光(如数据缺失/名册对不上)时安全返回,避免下方 apply 崩溃
            return pd.DataFrame()

        # 逐专项分类 → 推荐产品类 + 依据
        def classify(row):
            rec, why = [], []
            for f in GTM_FOCUS:
                n = int(row[f])
                if n == 0:
                    rec.append(f); why.append(f"{f}:机会(0台)")
                elif n <= NURTURE_MAX_UNITS:
                    rec.append(f); why.append(f"{f}:培育({n}台)")
            if not rec:
                return None, None
            head = f"90天货值{row['近90天货值_万']}万/{int(row['近90天台数'])}台"
            return ','.join(rec), head + ' · ' + ' · '.join(why)

        cls = df.apply(classify, axis=1, result_type='expand')
        df['推荐产品类'], df['推荐依据'] = cls[0], cls[1]
        df = df[df['推荐产品类'].notna()]

        # 排除:本月已入库 + 手动剔除(永久) + 冷却池(近2月GTM完成但无产出)
        exist = set(pd.read_sql(
            "SELECT 客户编码 FROM gtm_target WHERE 任务年月=?",
            conn, params=[year_month])['客户编码'].astype(str))
        excl = set(str(r[0]) for r in conn.execute("SELECT 客户编码 FROM gtm_exclude"))
        cool = cooling_codes(year_month)
        # 冷却期(金总 2026-07-13 定):近 2 个任务月内已在名单的客户(含「任务推广列表」
        # 历史导入的前期跑动客户)一律不重复推荐,先落在已跑过的范围里
        recent = set()
        for pm in _prev_months(year_month, 2):
            recent |= set(str(r[0]) for r in conn.execute(
                "SELECT 客户编码 FROM gtm_target WHERE 任务年月=?", (pm,)))
        drop = exist | excl | cool | recent
        if drop:
            df = df[~df['客户编码'].isin(drop)]

        # 近30天大华侧有效拜访过 → 降优先级排到最后(优先覆盖未跑过的客户,金总 2026-07-13 定;
        # 不排除:名额有剩才轮到它们,活跃 gate 不受影响)
        recent_visit = set(pd.read_sql("""
            SELECT DISTINCT 客户编码 FROM visit_record_v
             WHERE 拜访日期 >= date('now', '-30 day')
               AND COALESCE(_打卡异常无效, 0) = 0 AND COALESCE(_真异常打卡, 0) = 0
               AND COALESCE(_是否大华, 0) = 1
        """, conn)['客户编码'].astype(str))
        df['近30天已拜访'] = df['客户编码'].astype(str).isin(recent_visit)

        # 每经理 top N:未拜访优先,同组内按货值
        df = (df.sort_values(['近30天已拜访', '近90天货值_万'], ascending=[True, False])
                .groupby('分销经理', group_keys=False).head(per_limit))
        df = df.copy()
        df['来源'] = '系统推荐'
        cols = ['分销经理', '客户编码', '客户名称', '城市', '区县', '所属代理商',
                '推荐产品类', '推荐依据', '来源', '近90天货值_万', '近90天台数', '近90天拜访',
                '近30天已拜访']
        return df[cols].sort_values(['分销经理', '近30天已拜访', '近90天货值_万'],
                                    ascending=[True, True, False]).reset_index(drop=True)
    finally:
        conn.close()


# ══════════════════ 名单读写 ══════════════════

def get_targets(year_month: str) -> pd.DataFrame:
    conn = _conn()
    try:
        _ensure_table(conn)
        df = pd.read_sql(
            "SELECT * FROM gtm_target WHERE 任务年月=? ORDER BY 分销经理, id",
            conn, params=[year_month])
        if not df.empty:
            df['客户编码'] = df['客户编码'].astype(str)
        return df
    finally:
        conn.close()


def list_target_months() -> list[str]:
    conn = _conn()
    try:
        _ensure_table(conn)
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT 任务年月 FROM gtm_target ORDER BY 任务年月 DESC")]
    finally:
        conn.close()


def bulk_insert_targets(year_month: str, rows: list[dict], 来源='系统推荐') -> int:
    """rows: [{客户编码, 客户名称, 城市, 区县, 所属代理商, 分销经理, 推荐产品类, 推荐依据}]"""
    conn = _conn()
    try:
        _ensure_table(conn)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        n = 0
        for r in rows:
            try:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO gtm_target
                      (任务年月, 分销经理, 客户编码, 客户名称, 城市, 区县, 所属代理商,
                       推荐产品类, 推荐依据, 来源, 分配时间)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (year_month, r.get('分销经理', ''), str(r['客户编码']),
                      r.get('客户名称', ''), r.get('城市', ''), r.get('区县', ''),
                      r.get('所属代理商', ''), r.get('推荐产品类', ''),
                      r.get('推荐依据', ''), r.get('来源', 来源), now))
                n += cur.rowcount
            except sqlite3.Error:
                continue
        conn.commit()
        return n
    finally:
        conn.close()


def delete_target(row_id: int):
    conn = _conn()
    try:
        _ensure_table(conn)
        conn.execute("DELETE FROM gtm_target WHERE id=?", (int(row_id),))
        conn.commit()
    finally:
        conn.close()


def clear_month(year_month: str) -> int:
    conn = _conn()
    try:
        _ensure_table(conn)
        cur = conn.execute("DELETE FROM gtm_target WHERE 任务年月=?", (year_month,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def search_providers(kw: str, limit=20) -> pd.DataFrame:
    """手动添加用:按名称模糊搜签约服务商(剔马甲,客户所有者限分销经理名册——考核口径一致)。"""
    conn = _conn()
    try:
        managers = list_managers(conn)
        if not managers:
            return pd.DataFrame()
        mph = ','.join('?' * len(managers))
        df = pd.read_sql(f"""
            SELECT pc.客户编码, pc.客户名称, pc.客户城市 AS 城市, pc.客户区县 AS 区县,
                   pc.上级分销商名称 AS 所属代理商, pc.客户所有者 AS 分销经理
              FROM provider_contract pc
              LEFT JOIN vest_account va ON va.服务商客户编码 = pc.客户编码
             WHERE va.服务商客户编码 IS NULL
               AND pc.客户所有者 IN ({mph})
               AND pc.客户名称 LIKE ? LIMIT ?
        """, conn, params=[*managers, f'%{kw}%', limit])
        df['客户编码'] = df['客户编码'].astype(str)
        return df
    finally:
        conn.close()


# ══════════════════ 动作跟踪(实时算) ══════════════════

def get_gtm_board(year_month: str) -> pd.DataFrame:
    """名单 + 跑动 + 4 动作 + GTM 完成状态。全部事实表实时算。

    首访日 D = 任务月内、**名单分配之后** 的首次大华侧有效拜访
    (排除已知地址异常 + 可疑假打卡;代理商业务员拜访不算分销经理跑动);
    动作窗 = D ~ D+7;GTM完成 = 有 D 且 ≥1 动作(推广会签到/铺货/中红包/上线设备)。
    """
    targets = get_targets(year_month)
    if targets.empty:
        return targets
    conn = _conn()
    try:
        codes = targets['客户编码'].tolist()
        ph = ','.join('?' * len(codes))
        m_start, m_end = f'{year_month}-01', f'{year_month}-31'

        # 任务月内全部大华侧有效拜访(明细),再按每行"分配日"过滤取首访
        vis = pd.read_sql(f"""
            SELECT 客户编码, 拜访日期
              FROM visit_record_v
             WHERE 客户编码 IN ({ph}) AND 拜访日期 BETWEEN ? AND ?
               AND COALESCE(_打卡异常无效, 0) = 0 AND COALESCE(_真异常打卡, 0) = 0
               AND _是否大华 = 1
        """, conn, params=[*codes, m_start, m_end])
        vis['客户编码'] = vis['客户编码'].astype(str)
        df = targets.copy()
        # 首访日 = max(月初, 分配日) 起的首次拜访 —— 入库前的例行拜访不算 GTM 成果(含当天)
        assign_d = df.set_index('客户编码')['分配时间'].astype(str).str[:10].to_dict()

        def _first_visit(code):
            sub = vis[vis['客户编码'] == code]
            if sub.empty:
                return None
            lo = assign_d.get(code) or m_start
            sub = sub[sub['拜访日期'] >= max(m_start, lo)]
            return sub['拜访日期'].min() if not sub.empty else None

        df['首访日'] = df['客户编码'].map(_first_visit)

        # 各动作事实行:拉[月初, 月末+窗口]区间,pandas 按各自 D~D+7 过滤
        span_end = (pd.Timestamp(m_end.replace('-31', '-01')) + pd.offsets.MonthEnd(0)
                    + pd.Timedelta(days=ACTION_WINDOW_DAYS)).strftime('%Y-%m-%d')

        mtg = pd.read_sql(f"""
            SELECT 参会客户编码 AS 客户编码, date(签到时间) AS d
              FROM promotion_meeting
             WHERE 参会客户编码 IN ({ph}) AND 签到时间 IS NOT NULL
               AND date(签到时间) BETWEEN ? AND ?
        """, conn, params=[*codes, m_start, span_end])
        dist = pd.read_sql(f"""
            SELECT 客户编码_下级 AS 客户编码, substr(提交铺货时间,1,10) AS d,
                   序列号
              FROM distribution_info
             WHERE 客户编码_下级 IN ({ph})
               AND substr(提交铺货时间,1,10) BETWEEN ? AND ?
        """, conn, params=[*codes, m_start, span_end])
        rp = pd.read_sql(f"""
            SELECT 上线客户编码 AS 客户编码, 上线日期 AS d, 产品序列号,
                   COALESCE(中奖金额, 0) AS 中奖金额
              FROM install_redpack_v
             WHERE 上线客户编码 IN ({ph}) AND 上线日期 BETWEEN ? AND ?
        """, conn, params=[*codes, m_start, span_end])
        for x in (mtg, dist, rp):
            if not x.empty:
                x['客户编码'] = x['客户编码'].astype(str)

        def in_window(sub_df, code, d0, d1):
            if sub_df.empty:
                return sub_df
            m = (sub_df['客户编码'] == code) & (sub_df['d'] >= d0) & (sub_df['d'] <= d1)
            return sub_df[m]

        out_rows = []
        for _, r in df.iterrows():
            d = r.get('首访日')
            row = {'推广会': False, '铺货台数': 0, '红包台数': 0, '上线台数': 0}
            if pd.notna(d):
                d1 = (pd.Timestamp(d) + pd.Timedelta(days=ACTION_WINDOW_DAYS)
                      ).strftime('%Y-%m-%d')
                row['推广会'] = not in_window(mtg, r['客户编码'], d, d1).empty
                row['铺货台数'] = in_window(dist, r['客户编码'], d, d1)['序列号'].nunique() \
                    if not dist.empty else 0
                w_rp = in_window(rp, r['客户编码'], d, d1)
                row['上线台数'] = w_rp['产品序列号'].nunique() if not w_rp.empty else 0
                row['红包台数'] = w_rp[w_rp['中奖金额'] > 0]['产品序列号'].nunique() \
                    if not w_rp.empty else 0
            out_rows.append(row)
        acts = pd.DataFrame(out_rows, index=df.index)
        df = pd.concat([df, acts], axis=1)

        df['跑动完成'] = df['首访日'].notna()
        df['动作数'] = (df['推广会'].astype(int) + (df['铺货台数'] > 0).astype(int)
                       + (df['红包台数'] > 0).astype(int) + (df['上线台数'] > 0).astype(int))
        df['GTM完成'] = df['跑动完成'] & (df['动作数'] >= 1)
        df['状态'] = df.apply(
            lambda r: '✅ GTM完成' if r['GTM完成']
            else ('🚶 已跑动·待动作' if r['跑动完成'] else '⏳ 待跑动'), axis=1)
        return df
    finally:
        conn.close()


# ══════════════════ SO 成效透视 ══════════════════

def get_so_effect(year_month: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """GTM 完成对象自首访日起 90 天 SO。

    返回 (汇总df: 客户级 30/60/90分段+专项拆分, 明细df: 序列号级)。
    """
    board = get_gtm_board(year_month)
    if board.empty:
        return pd.DataFrame(), pd.DataFrame()
    done = board[board['GTM完成']]
    if done.empty:
        return pd.DataFrame(), pd.DataFrame()
    conn = _conn()
    try:
        codes = done['客户编码'].tolist()
        ph = ','.join('?' * len(codes))
        d_min = done['首访日'].min()
        d_max = (pd.Timestamp(done['首访日'].max())
                 + pd.Timedelta(days=SO_WINDOW_DAYS)).strftime('%Y-%m-%d')

        so = pd.read_sql(f"""
            SELECT ir.上线客户编码 AS 客户编码, ir.上线日期, ir.产品序列号,
                   ir.产品名称, ir.外部型号,
                   COALESCE(fc.专项, '其他') AS 专项,
                   COALESCE(ir.产品现有分销价, 0) AS 金额
              FROM install_redpack_v ir
              LEFT JOIN product_focus fc ON fc.物料号 = ir.物料号
             WHERE ir.上线客户编码 IN ({ph}) AND ir.上线日期 BETWEEN ? AND ?
        """, conn, params=[*codes, d_min, d_max])
        if so.empty:
            return pd.DataFrame(), pd.DataFrame()
        so['客户编码'] = so['客户编码'].astype(str)

        anchor = done.set_index('客户编码')['首访日'].to_dict()
        meta = done.set_index('客户编码')[['客户名称', '分销经理', '城市', '推荐产品类']]
        so['首访日'] = so['客户编码'].map(anchor)
        so = so[so['首访日'].notna()]
        so['天数'] = (pd.to_datetime(so['上线日期'])
                     - pd.to_datetime(so['首访日'])).dt.days
        so = so[(so['天数'] >= 0) & (so['天数'] <= SO_WINDOW_DAYS)]
        if so.empty:
            return pd.DataFrame(), pd.DataFrame()
        so = so.drop_duplicates(subset=['客户编码', '产品序列号'])

        rows = []
        for code, grp in so.groupby('客户编码'):
            row = {'客户编码': code}
            for w in (30, 60, 90):
                sub = grp[grp['天数'] <= w]
                row[f'SO{w}_万'] = round(sub['金额'].sum() / 10000, 2)
                row[f'台数{w}'] = sub['产品序列号'].nunique()
            for f in GTM_FOCUS:
                sub = grp[grp['专项'] == f]
                row[f'{f}_万'] = round(sub['金额'].sum() / 10000, 2)
                row[f'{f}_台'] = sub['产品序列号'].nunique()
            rows.append(row)
        summary = pd.DataFrame(rows).merge(
            meta.reset_index(), on='客户编码', how='left')
        cols = ['分销经理', '客户编码', '客户名称', '城市', '推荐产品类',
                'SO30_万', 'SO60_万', 'SO90_万', '台数90',
                *[f'{f}_万' for f in GTM_FOCUS], *[f'{f}_台' for f in GTM_FOCUS]]
        cols = [c for c in cols if c in summary.columns]
        detail = so.merge(meta.reset_index()[['客户编码', '客户名称', '分销经理']],
                          on='客户编码', how='left')
        detail = detail[['分销经理', '客户名称', '客户编码', '上线日期', '天数',
                         '专项', '产品名称', '外部型号', '产品序列号', '金额']] \
            .sort_values(['分销经理', '客户名称', '上线日期'])
        return (summary[cols].sort_values('SO90_万', ascending=False)
                .reset_index(drop=True), detail.reset_index(drop=True))
    finally:
        conn.close()


# ══════════════════ 考核 ══════════════════

def get_assessment(year_month: str) -> pd.DataFrame:
    """分销经理级考核:名单→跑动→动作→GTM完成→90天SO。"""
    board = get_gtm_board(year_month)
    if board.empty:
        return pd.DataFrame()
    summary, _ = get_so_effect(year_month)
    so_by_mgr = (summary.groupby('分销经理')
                 .agg(SO90_万=('SO90_万', 'sum'),
                      夜视王_万=('夜视王_万', 'sum'),
                      无线_万=('无线_万', 'sum'),
                      场景化_万=('场景化_万', 'sum'))
                 if not summary.empty else pd.DataFrame())

    rows = []
    for mgr, g in board.groupby('分销经理'):
        n = len(g)
        n_visit = int(g['跑动完成'].sum())
        n_done = int(g['GTM完成'].sum())
        row = {
            '分销经理': mgr, '名单数': n,
            '已跑动': n_visit, '跑动率': round(n_visit / n, 3) if n else 0,
            '推广会': int(g['推广会'].sum()),
            '铺货户': int((g['铺货台数'] > 0).sum()),
            '红包户': int((g['红包台数'] > 0).sum()),
            '上线户': int((g['上线台数'] > 0).sum()),
            'GTM完成': n_done, '完成率': round(n_done / n, 3) if n else 0,
        }
        if not so_by_mgr.empty and mgr in so_by_mgr.index:
            s = so_by_mgr.loc[mgr]
            row.update({'SO90_万': round(s['SO90_万'], 2),
                        '夜视王_万': round(s['夜视王_万'], 2),
                        '无线_万': round(s['无线_万'], 2),
                        '场景化_万': round(s['场景化_万'], 2)})
        else:
            row.update({'SO90_万': 0, '夜视王_万': 0, '无线_万': 0, '场景化_万': 0})
        rows.append(row)
    return (pd.DataFrame(rows)
            .sort_values(['完成率', 'SO90_万'], ascending=False)
            .reset_index(drop=True))
