"""LLM 工具集 — 给 AI Agent 调用的"接口"

每个工具：
  - schema: OpenAI Function Calling 格式（给 LLM 看的"使用说明书"）
  - handler: 实际的 Python 函数，接收参数 dict，返回 JSON 可序列化结果

设计原则：
  - 工具粒度适中（不要太细碎也不要太复杂）
  - 返回 JSON-friendly 的结构（dict/list/标量）
  - 限制返回大小（避免 LLM context 爆炸）
  - 错误时返回 {"error": "..."}（不抛异常，给 LLM 看错误）
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

# 复用现有 cache loaders
from _loaders import (
    DB_PATH,
    load_main_shared,
    load_redpack_shared,
    load_visit_shared,
    load_profile_shared,
    load_contract_shared,
    load_vest_shared,
    load_provider_tier_shared,
    load_provider_master_shared,
    compute_rfm_shared,
)


# ══════════════════════════════════════════════
# 公共工具
# ══════════════════════════════════════════════

MAX_RESULT_ROWS = 100  # 单次工具返回的最多行数（再多 LLM 也消化不了）


def _truncate_records(records: list, limit: int = MAX_RESULT_ROWS) -> dict:
    """统一截断 + 标注"""
    total = len(records)
    truncated = records[:limit]
    out = {'rows': truncated, 'total_rows': total}
    if total > limit:
        out['note'] = f'结果共 {total} 行，仅返回前 {limit} 行。如需更多请提高 limit 参数。'
    return out


def _err(msg: str) -> dict:
    return {'error': msg}


def _normalize_code(code: str) -> str:
    return str(code).strip()


# ══════════════════════════════════════════════
# A. 实体查询类
# ══════════════════════════════════════════════

def lookup_provider(args: dict) -> dict:
    """模糊搜索服务商（按公司名/客户编码/老板姓名）"""
    query = (args.get('query') or '').strip()
    if not query:
        return _err("query 参数不能为空")
    limit = int(args.get('limit', 10))

    matches = []  # (code, name, source, extra)

    # 1) 红包表（最权威的"有上线"数据）
    rp = load_redpack_shared()
    if not rp.empty:
        m = rp[
            rp['上线客户名称'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
            | rp['上线客户编码'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
        ][['上线客户编码', '上线客户名称']].drop_duplicates(subset='上线客户编码').head(limit * 3)
        for _, r in m.iterrows():
            matches.append({
                'code': str(r['上线客户编码']).strip(),
                'name': r['上线客户名称'],
                'source': 'redpack',
            })

    # 2) 沙盘表（含老板信息）
    profile = load_profile_shared()
    if not profile.empty:
        m = profile[
            profile['公司名称'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
            | profile['客户编码'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
            | profile['老板姓名'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
        ][['客户编码', '公司名称', '老板姓名', '老板电话', '地市']].head(limit * 3)
        for _, r in m.iterrows():
            matches.append({
                'code': str(r['客户编码']).strip(),
                'name': r['公司名称'],
                'boss': r['老板姓名'],
                'phone': r['老板电话'],
                'city': r['地市'],
                'source': 'profile',
            })

    # 3) 签约表
    contract = load_contract_shared()
    if not contract.empty:
        m = contract[
            contract['客户名称'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
            | contract['客户编码'].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)
        ][['客户编码', '客户名称']].head(limit * 3)
        for _, r in m.iterrows():
            matches.append({
                'code': str(r['客户编码']).strip(),
                'name': r['客户名称'],
                'source': 'contract',
            })

    # 去重（同一个 code 取信息最全的）
    by_code = {}
    for m in matches:
        c = m['code']
        if c not in by_code:
            by_code[c] = m
        else:
            # 合并：用更全的覆盖
            for k, v in m.items():
                if v and not by_code[c].get(k):
                    by_code[c][k] = v

    result = list(by_code.values())[:limit]
    return _truncate_records(result, limit)


LOOKUP_PROVIDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_provider",
        "description": "模糊搜索服务商。可按公司名片段、客户编码、老板姓名搜索。返回匹配的服务商列表（含编码、公司名、老板信息）。当用户提到某个具体服务商但只给了部分名字时，先用这个工具找到准确的客户编码。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词（公司名片段 / 客户编码 / 老板姓名）"},
                "limit": {"type": "integer", "description": "最多返回多少条，默认 10", "default": 10},
            },
            "required": ["query"]
        }
    }
}


def get_provider_profile(args: dict) -> dict:
    """获取服务商完整画像（沙盘 + 签约 + KPI 等级 + RFM + 是否马甲）"""
    code = _normalize_code(args.get('code', ''))
    if not code:
        return _err("code 参数不能为空")

    out = {'客户编码': code}

    # 合并视图（沙盘+签约）
    master = load_provider_master_shared()
    if not master.empty:
        m = master[master['客户编码'].astype(str) == code]
        if not m.empty:
            r = m.iloc[0]
            for c in ['公司名称', '老板姓名', '老板电话', '联系人', '联系电话',
                      '地址', '地市', '区县', '上级分销商名称',
                      '签约日期', '是否新签', '是否激活', '是否复购',
                      '客户分类', '主营品牌', '对大华品牌认可度']:
                v = r.get(c)
                if pd.notna(v) and str(v).strip():
                    out[c] = str(v) if not isinstance(v, (int, float)) else v

    # KPI 等级
    tier_df = load_provider_tier_shared()
    if not tier_df.empty:
        t = tier_df[tier_df['客户编码'] == code]
        if not t.empty:
            out['KPI等级'] = t.iloc[0]['服务商等级']
            out['累计上线金额'] = float(t.iloc[0]['上线累计'])
            out['累计中奖金额'] = float(t.iloc[0]['红包累计'])

    # RFM
    rfm_df = compute_rfm_shared(window_months=12)
    if not rfm_df.empty:
        r = rfm_df[rfm_df['客户编码'] == code]
        if not r.empty:
            out['RFM类别'] = r.iloc[0]['RFM类别']
            out['R(最近上线天数)'] = int(r.iloc[0]['R'])
            out['F(上线日期数)'] = int(r.iloc[0]['F'])
            out['M(上线总额)'] = float(r.iloc[0]['M'])

    # 是否马甲
    vest = load_vest_shared()
    if not vest.empty:
        out['是否马甲账号'] = code in set(vest['服务商客户编码'].astype(str).str.strip())

    if len(out) == 1:
        return _err(f"未找到客户编码 {code}")
    return out


GET_PROVIDER_PROFILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_provider_profile",
        "description": "拿某服务商的完整画像，包括基本信息（公司/老板/地址/电话）、签约状态、KPI 等级（v0-v4）、RFM 类别、累计上线/红包金额、是否马甲。需要客户编码。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "客户编码（如 1@10180642704）"}
            },
            "required": ["code"]
        }
    }
}


def lookup_dealer(args: dict) -> dict:
    """模糊搜代理商（一级出货客户）"""
    query = (args.get('query') or '').strip()
    if not query:
        return _err("query 参数不能为空")
    limit = int(args.get('limit', 10))

    rp = load_redpack_shared()
    if rp.empty:
        return _err("红包表为空")

    # 出货客户名称 + 所属一级客户 都搜
    dealers = set()
    for col in ['出货客户名称', '所属一级客户']:
        if col in rp.columns:
            m = rp[rp[col].fillna('').astype(str).str.contains(query, case=False, na=False, regex=False)]
            dealers |= set(m[col].dropna().astype(str).str.strip().unique())
    dealers.discard('')
    return _truncate_records(sorted(dealers)[:limit], limit)


LOOKUP_DEALER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_dealer",
        "description": "模糊搜代理商（一级出货客户）名称。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "代理商名片段"},
                "limit": {"type": "integer", "default": 10}
            },
            "required": ["query"]
        }
    }
}


def lookup_visitor(args: dict) -> dict:
    """搜业务员（按姓名）"""
    name = (args.get('name') or '').strip()
    if not name:
        return _err("name 参数不能为空")
    limit = int(args.get('limit', 20))

    visits = load_visit_shared()
    if visits.empty:
        return _err("拜访表为空")

    m = visits[
        visits['打卡人姓名'].fillna('').astype(str).str.contains(name, case=False, na=False, regex=False)
    ]
    agg = m.groupby(['打卡人姓名', '_打卡方', '打卡人所属公司']).agg(
        拜访次数=('活动编号', 'count'),
        覆盖服务商数=('客户编码', lambda x: x.nunique()),
    ).reset_index()
    return _truncate_records(agg.to_dict('records'), limit)


LOOKUP_VISITOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_visitor",
        "description": "搜业务员（按姓名片段），返回业务员列表 + 各自的拜访次数和覆盖服务商数。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "业务员姓名（可部分）"},
                "limit": {"type": "integer", "default": 20}
            },
            "required": ["name"]
        }
    }
}


# ══════════════════════════════════════════════
# B. 业务数据查询类
# ══════════════════════════════════════════════

def query_online_records(args: dict) -> dict:
    """查询服务商上线记录（按时间/型号/地址过滤+分组）"""
    code = _normalize_code(args.get('code', ''))
    if not code:
        return _err("code 参数不能为空")
    start = args.get('start_date')
    end = args.get('end_date')
    group_by = args.get('group_by')  # 日期 / 月份 / 内部型号 / 产品系列 / 安装区县 / 安装GPS详细地址 / 出货客户名称
    limit = int(args.get('limit', 50))

    rp = load_redpack_shared()
    if rp.empty:
        return _err("红包表为空")

    df = rp[rp['上线客户编码'].astype(str).str.strip() == code].copy()
    if df.empty:
        return {'rows': [], 'total_rows': 0, 'note': f'客户 {code} 无上线记录'}

    if start:
        df = df[df['上线时间'] >= pd.to_datetime(start)]
    if end:
        df = df[df['上线时间'] <= pd.to_datetime(end)]

    if not group_by:
        # 不分组 → 返回设备明细（截断）
        cols = ['上线时间', '产品序列号', '内部型号', '产品系列', '产品现有分销价',
                '中奖金额', '出货客户名称', '安装城市', '安装区县', '安装GPS详细地址']
        cols = [c for c in cols if c in df.columns]
        sub = df[cols].sort_values('上线时间').head(limit)
        sub['上线时间'] = sub['上线时间'].astype(str)
        return _truncate_records(sub.to_dict('records'), limit)

    # 分组
    if group_by == '日期':
        df['_g'] = df['上线日期'].astype(str)
    elif group_by == '月份':
        df['_g'] = df['上线年月']
    elif group_by in df.columns:
        df['_g'] = df[group_by].astype(str)
    else:
        return _err(f"不支持的 group_by: {group_by}")

    g = df.groupby('_g').agg(
        台数=('产品序列号', 'count'),
        金额=('产品现有分销价', 'sum'),
        红包=('中奖金额', lambda x: x[x > 0].sum()),
    ).reset_index().rename(columns={'_g': group_by})
    g = g.sort_values('台数', ascending=False).head(limit)
    return _truncate_records(g.to_dict('records'), limit)


QUERY_ONLINE_RECORDS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_online_records",
        "description": "查询某服务商的上线设备记录。可按时间过滤，可按维度分组（日期/月份/内部型号/产品系列/安装区县/安装GPS详细地址/出货客户名称）。不分组时返回设备明细。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "客户编码"},
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "截止日期 YYYY-MM-DD"},
                "group_by": {
                    "type": "string",
                    "enum": ["日期", "月份", "内部型号", "产品系列", "安装区县", "安装GPS详细地址", "出货客户名称"],
                    "description": "分组维度，不传则返回设备明细"
                },
                "limit": {"type": "integer", "default": 50}
            },
            "required": ["code"]
        }
    }
}


def query_visit_records(args: dict) -> dict:
    """查询拜访记录（按客户编码 或 业务员姓名）"""
    code = (args.get('code') or '').strip()
    visitor = (args.get('visitor_name') or '').strip()
    start = args.get('start_date')
    end = args.get('end_date')
    limit = int(args.get('limit', 30))

    if not code and not visitor:
        return _err("必须提供 code 或 visitor_name 至少一个")

    visits = load_visit_shared()
    if visits.empty:
        return _err("拜访表为空")

    df = visits.copy()
    if code:
        df = df[df['客户编码'].astype(str).str.strip() == code]
    if visitor:
        df = df[df['打卡人姓名'].fillna('').astype(str).str.contains(visitor, case=False, na=False, regex=False)]
    if start:
        df = df[df['拜访时间'] >= pd.to_datetime(start)]
    if end:
        df = df[df['拜访时间'] <= pd.to_datetime(end)]

    if df.empty:
        return {'rows': [], 'total_rows': 0}

    cols = ['拜访时间', '客户编码', '拜访客户', '打卡人姓名', '_打卡方',
            '打卡人所属公司', '拜访目的', '达成结果', '后期计划',
            '距离偏离_米', '打卡异常类型']
    cols = [c for c in cols if c in df.columns]
    sub = df[cols].sort_values('拜访时间', ascending=False).head(limit)
    sub['拜访时间'] = sub['拜访时间'].astype(str)
    if '距离偏离_米' in sub.columns:
        sub['距离偏离_米'] = pd.to_numeric(sub['距离偏离_米'], errors='coerce').round(0)
    return _truncate_records(sub.to_dict('records'), limit)


QUERY_VISIT_RECORDS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_visit_records",
        "description": "查询拜访记录。可按服务商客户编码 或 业务员姓名 查询，返回拜访明细（含拜访目的/达成结果/打卡方）。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "服务商客户编码（可选）"},
                "visitor_name": {"type": "string", "description": "业务员姓名（可选）"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "default": 30}
            }
        }
    }
}


def query_redpack_records(args: dict) -> dict:
    """查询红包/中奖记录"""
    code = _normalize_code(args.get('code', ''))
    start = args.get('start_date')
    end = args.get('end_date')
    only_won = args.get('only_won', True)  # 默认只看中奖
    limit = int(args.get('limit', 50))

    if not code:
        return _err("code 参数不能为空")

    rp = load_redpack_shared()
    df = rp[rp['上线客户编码'].astype(str).str.strip() == code].copy()
    if start:
        df = df[df['上线时间'] >= pd.to_datetime(start)]
    if end:
        df = df[df['上线时间'] <= pd.to_datetime(end)]
    if only_won:
        df = df[pd.to_numeric(df['中奖金额'], errors='coerce').fillna(0) > 0]

    if df.empty:
        return {'rows': [], 'total_rows': 0}

    cols = ['上线时间', '产品序列号', '内部型号', '中奖金额', '产品现有分销价',
            '出货客户名称', '安装区县']
    cols = [c for c in cols if c in df.columns]
    sub = df[cols].sort_values('上线时间', ascending=False).head(limit)
    sub['上线时间'] = sub['上线时间'].astype(str)
    return _truncate_records(sub.to_dict('records'), limit)


QUERY_REDPACK_RECORDS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_redpack_records",
        "description": "查询某服务商的红包/中奖记录。默认只返回中奖（中奖金额>0），可改 only_won=false 看全部抽奖。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "客户编码"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "only_won": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 50}
            },
            "required": ["code"]
        }
    }
}


def query_provider_kpi(args: dict) -> dict:
    """查服务商等级 / RFM / 健康度（带行动建议特征）"""
    code = _normalize_code(args.get('code', ''))
    if not code:
        return _err("code 参数不能为空")

    out = {'客户编码': code}

    tier = load_provider_tier_shared()
    if not tier.empty:
        t = tier[tier['客户编码'] == code]
        if not t.empty:
            out['服务商等级'] = t.iloc[0]['服务商等级']
            out['上线累计金额'] = float(t.iloc[0]['上线累计'])
            out['红包累计金额'] = float(t.iloc[0]['红包累计'])

    rfm = compute_rfm_shared(window_months=12)
    if not rfm.empty:
        r = rfm[rfm['客户编码'] == code]
        if not r.empty:
            out['RFM类别'] = r.iloc[0]['RFM类别']
            out['最近上线天数(R)'] = int(r.iloc[0]['R'])
            out['上线日期数(F)'] = int(r.iloc[0]['F'])

    if len(out) == 1:
        return _err(f"未找到 {code} 的 KPI 数据")
    return out


QUERY_PROVIDER_KPI_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_provider_kpi",
        "description": "查服务商当前 KPI：等级（v0/已激活/v2/v3/v4，按上线金额累计判定）、RFM 类别、红包累计。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"}
            },
            "required": ["code"]
        }
    }
}


# ══════════════════════════════════════════════
# C. 排行 / 聚合类
# ══════════════════════════════════════════════

def top_n(args: dict) -> dict:
    """通用 Top N（服务商/代理商/区县/产品系列/型号/业务员）"""
    dim = args.get('dim', '').strip()  # 服务商/代理商/区县/产品系列/型号/业务员
    metric = args.get('metric', '台数')  # 台数/金额/红包
    scope_city = args.get('scope_city')  # 限定地市
    scope_dealer = args.get('scope_dealer')  # 限定签约代理商
    start = args.get('start_date')
    end = args.get('end_date')
    n = int(args.get('n', 10))

    if dim in ('服务商', '代理商', '区县', '产品系列', '型号'):
        rp = load_redpack_shared()
        if rp.empty:
            return _err("红包表为空")
        df = rp.copy()
        if scope_city and '安装城市' in df.columns:
            df = df[df['安装城市'].fillna('').astype(str) == scope_city]
        if scope_dealer and '所属一级客户' in df.columns:
            df = df[df['所属一级客户'].fillna('').astype(str) == scope_dealer]
        if start:
            df = df[df['上线时间'] >= pd.to_datetime(start)]
        if end:
            df = df[df['上线时间'] <= pd.to_datetime(end)]
        if df.empty:
            return {'rows': []}

        if dim == '服务商':
            g = df.groupby(['上线客户编码', '上线客户名称']).agg(
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
                红包=('中奖金额', lambda x: x[x > 0].sum()),
            ).reset_index()
        elif dim == '代理商':
            g = df.groupby('所属一级客户').agg(
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
                红包=('中奖金额', lambda x: x[x > 0].sum()),
            ).reset_index()
        elif dim == '区县':
            g = df.groupby(['安装城市', '安装区县']).agg(
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
                红包=('中奖金额', lambda x: x[x > 0].sum()),
            ).reset_index()
        elif dim == '产品系列':
            g = df.groupby('产品系列').agg(
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
                红包=('中奖金额', lambda x: x[x > 0].sum()),
            ).reset_index()
        elif dim == '型号':
            g = df.groupby('内部型号').agg(
                台数=('产品序列号', 'count'),
                金额=('产品现有分销价', 'sum'),
            ).reset_index()
        else:
            return _err(f"不支持的 dim: {dim}")

        sort_col = {'台数': '台数', '金额': '金额', '红包': '红包'}.get(metric, '台数')
        if sort_col not in g.columns:
            sort_col = '台数'
        g = g.sort_values(sort_col, ascending=False).head(n)
        return _truncate_records(g.to_dict('records'), n)

    elif dim == '业务员':
        v = load_visit_shared()
        if v.empty:
            return _err("拜访表为空")
        df = v.copy()
        if start:
            df = df[df['拜访时间'] >= pd.to_datetime(start)]
        if end:
            df = df[df['拜访时间'] <= pd.to_datetime(end)]
        g = df.groupby(['打卡人姓名', '_打卡方']).agg(
            拜访次数=('活动编号', 'count'),
            覆盖服务商数=('客户编码', lambda x: x.nunique()),
        ).reset_index().sort_values('拜访次数', ascending=False).head(n)
        return _truncate_records(g.to_dict('records'), n)

    return _err(f"不支持的 dim: {dim}")


TOP_N_SCHEMA = {
    "type": "function",
    "function": {
        "name": "top_n",
        "description": "通用 Top N 排行。可按维度（服务商/代理商/区县/产品系列/型号/业务员）+ 指标（台数/金额/红包）+ 范围（地市/签约代理商/时间段）排行。",
        "parameters": {
            "type": "object",
            "properties": {
                "dim": {
                    "type": "string",
                    "enum": ["服务商", "代理商", "区县", "产品系列", "型号", "业务员"]
                },
                "metric": {
                    "type": "string",
                    "enum": ["台数", "金额", "红包"],
                    "default": "台数"
                },
                "scope_city": {"type": "string", "description": "限定地市，如 '杭州市'"},
                "scope_dealer": {"type": "string", "description": "限定签约代理商名"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "n": {"type": "integer", "default": 10}
            },
            "required": ["dim"]
        }
    }
}


def compare_periods(args: dict) -> dict:
    """两个时间段对比（按维度）"""
    period1_start = args.get('period1_start')
    period1_end = args.get('period1_end')
    period2_start = args.get('period2_start')
    period2_end = args.get('period2_end')
    dim = args.get('dim', '区县')
    n = int(args.get('n', 10))

    if not all([period1_start, period1_end, period2_start, period2_end]):
        return _err("需要 period1_start/end 和 period2_start/end 全部参数")

    rp = load_redpack_shared()
    if rp.empty:
        return _err("红包表为空")

    p1 = rp[(rp['上线时间'] >= pd.to_datetime(period1_start)) & (rp['上线时间'] <= pd.to_datetime(period1_end))]
    p2 = rp[(rp['上线时间'] >= pd.to_datetime(period2_start)) & (rp['上线时间'] <= pd.to_datetime(period2_end))]

    dim_col = {'区县': '安装区县', '代理商': '所属一级客户', '产品系列': '产品系列',
               '型号': '内部型号'}.get(dim)
    if not dim_col or dim_col not in rp.columns:
        return _err(f"不支持的 dim: {dim}")

    g1 = p1.groupby(dim_col).agg(期1台数=('产品序列号', 'count'),
                                   期1金额=('产品现有分销价', 'sum')).reset_index()
    g2 = p2.groupby(dim_col).agg(期2台数=('产品序列号', 'count'),
                                   期2金额=('产品现有分销价', 'sum')).reset_index()
    merged = pd.merge(g1, g2, on=dim_col, how='outer').fillna(0)
    merged['台数变化'] = merged['期2台数'] - merged['期1台数']
    merged['金额变化'] = merged['期2金额'] - merged['期1金额']
    merged = merged.sort_values('台数变化', ascending=False).head(n)
    return _truncate_records(merged.to_dict('records'), n)


COMPARE_PERIODS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_periods",
        "description": "两个时间段对比（按维度）。如 2025-04 vs 2026-04 同比、2026-03 vs 2026-04 环比。",
        "parameters": {
            "type": "object",
            "properties": {
                "period1_start": {"type": "string", "description": "期 1 开始日 YYYY-MM-DD"},
                "period1_end": {"type": "string"},
                "period2_start": {"type": "string"},
                "period2_end": {"type": "string"},
                "dim": {"type": "string", "enum": ["区县", "代理商", "产品系列", "型号"], "default": "区县"},
                "n": {"type": "integer", "default": 10}
            },
            "required": ["period1_start", "period1_end", "period2_start", "period2_end"]
        }
    }
}


# ══════════════════════════════════════════════
# D. 关系挖掘类
# ══════════════════════════════════════════════

def is_fake_provider(args: dict) -> dict:
    """判断某服务商是否假商（4 类）"""
    code = _normalize_code(args.get('code', ''))
    if not code:
        return _err("code 参数不能为空")

    out = {'客户编码': code, 'tags': []}

    # 1) 已确认马甲
    vest = load_vest_shared()
    if not vest.empty and code in set(vest['服务商客户编码'].astype(str).str.strip()):
        out['tags'].append('🎭 已确认马甲')
        out['马甲背后代理商'] = (
            vest.loc[vest['服务商客户编码'].astype(str).str.strip() == code, '对应一级'].iloc[0]
            if not vest.loc[vest['服务商客户编码'].astype(str).str.strip() == code].empty else None
        )

    # 2) 假签约：签约 60+ 天但上线 ≤ 2 台
    contract = load_contract_shared()
    rp = load_redpack_shared()
    if not contract.empty and not rp.empty:
        c = contract[contract['客户编码'].astype(str) == code]
        if not c.empty:
            sign_date = pd.to_datetime(c.iloc[0].get('签约日期'), errors='coerce')
            anchor = rp['上线时间'].max()
            if pd.notna(sign_date) and pd.notna(anchor):
                days_signed = (anchor - sign_date).days
                n_台 = (rp['上线客户编码'].astype(str).str.strip() == code).sum()
                if days_signed >= 60 and n_台 <= 2:
                    out['tags'].append('❌ 假签约')
                    out['签约天数'] = int(days_signed)
                    out['上线台数'] = int(n_台)

    out['是否假商'] = bool(out['tags'])
    return out


IS_FAKE_PROVIDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "is_fake_provider",
        "description": "判断某服务商是否被识别为假商。返回标签：🎭已确认马甲 / ❌假签约 / ☂️伞形 / 🔄套上线（可多个）。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"}
            },
            "required": ["code"]
        }
    }
}


# ══════════════════════════════════════════════
# E. 兜底
# ══════════════════════════════════════════════

DB_SCHEMA_DESC = """\
SQLite 数据库 product_flow.db 的关键表：

1. **install_redpack**（安装红包/上线表，每行 = 一台设备）
   - 产品序列号 (PK)
   - 上线时间, 上线日期, 上线年月, 上线年份, 上线月份
   - 上线客户编码, 上线客户名称（这是服务商）
   - 所属一级客户（签约代理商）
   - 出货客户名称, 出货客户城市（实际出货代理商）
   - 安装省份, 安装城市, 安装区县, 安装GPS详细地址
   - 产品现有分销价（每台的货值，单位元）
   - 中奖金额（抽奖中奖，可为 0）
   - 产品系列, 产品子系列, 产品子系列-新, 内部型号
   - 是否抽奖, 抽奖机会发放时间

2. **provider_profile**（服务商沙盘）
   - 客户编码 (PK), 公司名称
   - 老板姓名, 老板电话, 老板年龄, 对大华品牌认可度
   - 省份, 地市, 区县, 乡镇, 地址
   - 上级分销商名称, 客户分类, 主营品牌
   - 公司总人数, 安防销售人员数量

3. **provider_contract**（签约明细）
   - 客户编码 (PK), 客户名称
   - 客户省区, 客户城市, 客户区县, 详细地址
   - 上级分销商名称, 服务商等级
   - 签约日期, 是否新签, 是否激活, 激活时间
   - 联系人, 联系电话

4. **visit_record**（业务员拜访打卡）
   - 活动编号 (PK)
   - 客户编码, 拜访客户
   - 拜访时间（已用「活动创建时间」替换原值）
   - 打卡人姓名, 打卡人手机号, 打卡人所属公司
   - _打卡方（"🏢 大华" / "🏪 代理商"，派生字段）
   - 拜访目的, 达成结果, 后期计划, 问题风险_资源诉求
   - 距离偏离_米, 打卡异常类型

5. **product_flow**（主表 - 设备出库到上线流水）
   - ID (PK), 产品序列号
   - 出库时间, 上线时间
   - 出库客户名称, 上线自客户名称, 所属一级客户
   - 上线城市, 上线区县
   - 最新分销价（合同价；激活货值在红包表）
   - KPI金额（派生：优先红包表「产品现有分销价」，fallback 主表「最新分销价」）
   - 数据剔除（电商/4G/其他）
   - 客户行为异常（Y = 异常刷单 ）

6. **vest_account**（已确认马甲名单）
   - 服务商客户编码 (PK)
   - 服务商客户名称, 城市, 对应一级（背后代理商）

⚠️ 安全：generic_sql 仅允许 SELECT，禁 DROP/INSERT/UPDATE/DELETE/ALTER。
"""


def describe_schema(args: dict) -> dict:
    """返回数据库表结构说明"""
    table = (args.get('table') or '').strip()
    if not table:
        return {'schema': DB_SCHEMA_DESC}

    if not DB_PATH.exists():
        return _err("数据库不存在")
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{table}")')
        rows = cur.fetchall()
        if not rows:
            return _err(f"表 {table} 不存在")
        cols = [{'name': r[1], 'type': r[2], 'notnull': r[3], 'pk': r[5]} for r in rows]
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        cnt = cur.fetchone()[0]
        return {'table': table, 'row_count': cnt, 'columns': cols}
    finally:
        conn.close()


DESCRIBE_SCHEMA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "describe_schema",
        "description": "查看数据库表结构。不传 table 时返回所有表的字段说明（推荐先用这个了解数据 schema）。传 table 名返回该表详细字段。",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "表名（可选）"}
            }
        }
    }
}


# 危险关键字（强制禁用）
SQL_BLACKLIST = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|REPLACE|GRANT|ATTACH|DETACH|PRAGMA\s+\w+\s*=|VACUUM)\b',
    re.IGNORECASE,
)


def generic_sql(args: dict) -> dict:
    """执行 SELECT SQL（白名单防护）"""
    sql = (args.get('sql') or '').strip()
    if not sql:
        return _err("sql 参数不能为空")
    # 必须 SELECT 开头
    if not re.match(r'^\s*(SELECT|WITH)\b', sql, re.IGNORECASE):
        return _err("仅允许 SELECT 或 WITH ... SELECT 查询")
    # 禁危险操作
    if SQL_BLACKLIST.search(sql):
        return _err("SQL 含禁用关键字（DROP/DELETE/UPDATE/INSERT/ALTER 等不允许）")
    # 加 LIMIT 兜底
    if 'LIMIT' not in sql.upper():
        sql = sql.rstrip(' ;\n') + ' LIMIT 200'

    if not DB_PATH.exists():
        return _err("数据库不存在")

    try:
        # 使用只读模式连接
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        try:
            df = pd.read_sql(sql, conn)
        finally:
            conn.close()
        if df.empty:
            return {'rows': [], 'total_rows': 0}
        # 时间字段转字符串
        for c in df.columns:
            if df[c].dtype.kind == 'M':
                df[c] = df[c].astype(str)
        return _truncate_records(df.head(MAX_RESULT_ROWS).to_dict('records'), MAX_RESULT_ROWS)
    except Exception as e:
        return _err(f"SQL 执行失败：{e}")


GENERIC_SQL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generic_sql",
        "description": "兜底工具：直接执行 SELECT SQL 查询（只读，含白名单防护）。当其他工具不能满足需求时使用。建议先用 describe_schema 了解表结构。",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT 或 WITH ... SELECT SQL 语句"}
            },
            "required": ["sql"]
        }
    }
}


# ══════════════════════════════════════════════
# 工具注册表
# ══════════════════════════════════════════════

TOOLS = {
    'lookup_provider': {'schema': LOOKUP_PROVIDER_SCHEMA, 'handler': lookup_provider},
    'get_provider_profile': {'schema': GET_PROVIDER_PROFILE_SCHEMA, 'handler': get_provider_profile},
    'lookup_dealer': {'schema': LOOKUP_DEALER_SCHEMA, 'handler': lookup_dealer},
    'lookup_visitor': {'schema': LOOKUP_VISITOR_SCHEMA, 'handler': lookup_visitor},
    'query_online_records': {'schema': QUERY_ONLINE_RECORDS_SCHEMA, 'handler': query_online_records},
    'query_visit_records': {'schema': QUERY_VISIT_RECORDS_SCHEMA, 'handler': query_visit_records},
    'query_redpack_records': {'schema': QUERY_REDPACK_RECORDS_SCHEMA, 'handler': query_redpack_records},
    'query_provider_kpi': {'schema': QUERY_PROVIDER_KPI_SCHEMA, 'handler': query_provider_kpi},
    'top_n': {'schema': TOP_N_SCHEMA, 'handler': top_n},
    'compare_periods': {'schema': COMPARE_PERIODS_SCHEMA, 'handler': compare_periods},
    'is_fake_provider': {'schema': IS_FAKE_PROVIDER_SCHEMA, 'handler': is_fake_provider},
    'describe_schema': {'schema': DESCRIBE_SCHEMA_SCHEMA, 'handler': describe_schema},
    'generic_sql': {'schema': GENERIC_SQL_SCHEMA, 'handler': generic_sql},
}


def get_tool_schemas() -> list:
    """返回所有工具的 OpenAI Function Calling schema 列表"""
    return [t['schema'] for t in TOOLS.values()]


def execute_tool(name: str, args: dict) -> Any:
    """执行工具调用"""
    if name not in TOOLS:
        return _err(f"未知工具: {name}")
    try:
        return TOOLS[name]['handler'](args)
    except Exception as e:
        return _err(f"工具执行异常：{e}")
