#!/usr/bin/env python3
"""
🕵️ 关联服务商挖掘（团伙识别）
- 独立 page：避免和实验室其他 tab 互相影响
- 通过同地址 / 同电话 / 同步上线找团伙
- 叠加跑动数据识别业务员作假
- 流式进度：点按钮才开始，每步显示耗时
"""

import io
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import (  # noqa: E402
    load_profile_shared, load_contract_shared,
    load_redpack_shared, load_visit_shared, load_vest_shared,
)
from _ai_log import closed_provider_codes  # noqa: E402

require_auth()
st.markdown("### 🕵️ 关联服务商挖掘（团伙识别）")
st.caption(
    "把表面上不同的服务商，通过**同地址 / 同电话 / 同步上线**等线索关联起来。"
    "再叠加跑动数据：如果同一业务员频繁在团伙内多家服务商打卡 → **作假嫌疑**。"
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.1rem !important; line-height: 1.1; }
[data-testid="stMetricLabel"] { font-size: 0.78rem !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
# 噪音黑名单 + 清洗工具
# ──────────────────────────────────────────

ADDR_NOISE = {'未识别', '未填写', '0', '1', '不详', '无', '未知', 'nan', 'None', ''}
PHONE_NOISE = {'未知', '无', '0', '1', 'nan', 'None', ''}


def norm_addr(v):
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    if not s or s in ADDR_NOISE or len(s) < 8:
        return None
    return s


def norm_phone(v):
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    if not s or s in PHONE_NOISE:
        return None
    digits = ''.join(c for c in s if c.isdigit())
    if len(digits) < 7:
        return None
    return digits


# ──────────────────────────────────────────
# 假阳性过滤：工商批量注册地址 + 同址密度递减
# ──────────────────────────────────────────

# 工商批量注册地址特征：「省 + 市 + 区/县 + 数字号 + 街道」
# 例：浙江省绍兴市越城区329号灵芝街道、浙江省金华市义乌市26号稠城街道
# 这是工商所给小微企业批发用的虚拟注册地，不是真办公场所
COMMERCIAL_REGISTRY_PATTERN = re.compile(
    r'^[一-龥]{2,3}省.*?\d+(?:-\d+)?号[一-龥]+街道$'
)


def is_commercial_registry(addr: str) -> bool:
    """是否是工商批量注册地址"""
    return bool(COMMERCIAL_REGISTRY_PATTERN.match(str(addr)))


def addr_weight_by_density(n_codes: int) -> int:
    """同址家数越多，每对加分越低（避免市场聚集形成大团伙）。
    2  家 → 10 分（强信号，2 人独立巧合极少）
    3-5 家 → 6 分
    6-10 家 → 3 分
    11-30 家 → 1 分
    >30 家 → 0（已被 max_size 跳过）
    """
    if n_codes <= 2:
        return 10
    if n_codes <= 5:
        return 6
    if n_codes <= 10:
        return 3
    if n_codes <= 30:
        return 1
    return 0


# ──────────────────────────────────────────
# 核心算法 cache 函数（参数 _ 前缀让 streamlit 跳过 hash）
# ──────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def build_evidence(_profile, _contract, _redpack, sync_min_qty: int, co_install_min_qty: int = 3, scope_key: str = "ALL"):
    # scope_key 参与 cache 区分（地市筛选不同 → 不同 cache）。本身不在函数内使用
    """构建服务商两两证据矩阵：返回 (pair_score, pair_evidence)"""
    addr_p = defaultdict(set)
    if not _profile.empty and '地址' in _profile.columns:
        for code, addr in zip(_profile['客户编码'].astype(str).str.strip(), _profile['地址']):
            a = norm_addr(addr)
            if a:
                addr_p[a].add(code)

    addr_c = defaultdict(set)
    if not _contract.empty:
        # 签约表地址列：原列名「详细地址」，master 视图里才 rename 成「地址」
        addr_col = '地址' if '地址' in _contract.columns else (
            '详细地址' if '详细地址' in _contract.columns else None
        )
        if addr_col:
            for code, addr in zip(_contract['客户编码'].astype(str).str.strip(), _contract[addr_col]):
                a = norm_addr(addr)
                if a:
                    addr_c[a].add(code)

    phone_p = defaultdict(set)
    if not _profile.empty and '老板电话' in _profile.columns:
        for code, phone in zip(_profile['客户编码'].astype(str).str.strip(), _profile['老板电话']):
            p = norm_phone(phone)
            if p:
                phone_p[p].add(code)

    sync_idx = defaultdict(set)
    co_install_idx = defaultdict(set)  # (上线日期, GPS 安装地址) → {服务商集合}
    if not _redpack.empty:
        # ── 普通同步上线：(日期, 出货代理商) → 服务商集合（单家 ≥ sync_min_qty 台）
        rp = _redpack[['上线日期', '出货客户名称', '上线客户编码']].copy().dropna()
        rp['上线客户编码'] = rp['上线客户编码'].astype(str).str.strip()
        rp['出货客户名称'] = rp['出货客户名称'].astype(str).str.strip()
        cnt = rp.groupby(['上线日期', '出货客户名称', '上线客户编码']).size()
        keep_keys = cnt[cnt >= sync_min_qty].index
        for (d, dealer, code) in keep_keys:
            sync_idx[(d, dealer)].add(code)

        # ── 同址同步上线：(30 分钟时间窗口, 安装 GPS 地址) → 服务商集合
        # 二次筛选时间窗口（半小时内）→ 排除「都来集散市场取货激活」的假阳性
        # 真团伙：多家服务商在 30 分钟内、同一 GPS 地址 → 连续激活操作
        # 假阳性：义乌通信市场全天都有人来激活，半天内拉到 30+ 家是正常市场行为
        if '安装GPS详细地址' in _redpack.columns and '上线时间' in _redpack.columns:
            rp2 = _redpack[['上线时间', '安装GPS详细地址', '上线客户编码']].copy().dropna()
            rp2['上线客户编码'] = rp2['上线客户编码'].astype(str).str.strip()
            rp2['安装GPS详细地址'] = rp2['安装GPS详细地址'].astype(str).str.strip()
            rp2 = rp2[rp2['安装GPS详细地址'].str.len() >= 8]
            rp2 = rp2[~rp2['安装GPS详细地址'].str.contains(r'^\*+$|^未识别|^None$', regex=True, na=False)]
            # 30 分钟窗口分桶（floor 到半小时）
            rp2['_时间窗口'] = pd.to_datetime(rp2['上线时间']).dt.floor('30min')
            rp2 = rp2.dropna(subset=['_时间窗口'])
            cnt2 = rp2.groupby(['_时间窗口', '安装GPS详细地址', '上线客户编码']).size()
            keep2 = cnt2[cnt2 >= co_install_min_qty].index
            for (t_window, gps_addr, code) in keep2:
                co_install_idx[(t_window, gps_addr)].add(code)

    pair_score = Counter()
    pair_evidence = {}

    def get_ev(key):
        ev = pair_evidence.get(key)
        if ev is None:
            ev = {
                'addr_p': set(), 'addr_c': set(), 'phone': set(),
                'sync': 0, 'co_install': 0, 'co_install_examples': [],
            }
            pair_evidence[key] = ev
        return ev

    def add_pair_batch(idx, kind, weight, max_size):
        """通用：固定权重加分。用于 phone / sync / co_install"""
        for k_payload, codes in idx.items():
            codes = list(codes)
            if len(codes) > max_size or len(codes) < 2:
                continue
            for i in range(len(codes)):
                for j in range(i + 1, len(codes)):
                    a, b = codes[i], codes[j]
                    if a == b:
                        continue
                    key = (a, b) if a < b else (b, a)
                    pair_score[key] += weight
                    ev = get_ev(key)
                    if kind == 'phone':
                        ev['phone'].add(k_payload)
                    elif kind == 'sync':
                        ev['sync'] += 1
                    elif kind == 'co_install':
                        ev['co_install'] += 1
                        if len(ev['co_install_examples']) < 3:
                            ev['co_install_examples'].append(k_payload)

    def add_addr_pairs(idx, kind):
        """同址加分：① 工商批量注册地址 + ≥10 家直接跳过 ② 按家数密度递减加分"""
        for addr, codes in idx.items():
            codes = list(codes)
            n = len(codes)
            if n < 2:
                continue
            # 工商批量注册地址 + 家数 ≥10 → 这是市场假阳性，跳过整个地址
            if n >= 10 and is_commercial_registry(addr):
                continue
            if n > 30:
                continue
            weight = addr_weight_by_density(n)
            if weight == 0:
                continue
            for i in range(n):
                for j in range(i + 1, n):
                    a, b = codes[i], codes[j]
                    if a == b:
                        continue
                    key = (a, b) if a < b else (b, a)
                    pair_score[key] += weight
                    ev = get_ev(key)
                    if kind == 'addr_p':
                        ev['addr_p'].add(addr)
                    else:
                        ev['addr_c'].add(addr)

    add_addr_pairs(addr_p, 'addr_p')
    add_addr_pairs(addr_c, 'addr_c')
    add_pair_batch(phone_p, 'phone', 15, 10)
    add_pair_batch(sync_idx, 'sync', 1, 30)
    # 同址同步上线（强信号）：每对 +5 分；max 限 30 家避免大集散点
    add_pair_batch(co_install_idx, 'co_install', 5, 30)

    for key, ev in pair_evidence.items():
        # 累计同步上线额外加分
        if ev['sync'] >= 10:
            pair_score[key] += 10
        elif ev['sync'] >= 5:
            pair_score[key] += 5
        # 累计同址同步上线额外加分（更强信号）
        if ev['co_install'] >= 5:
            pair_score[key] += 15
        elif ev['co_install'] >= 3:
            pair_score[key] += 8

    return pair_score, pair_evidence


@st.cache_data(ttl=600, show_spinner=False)
def find_groups(_pair_score: dict, threshold: int, scope_key: str = "ALL"):
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for (a, b), s in _pair_score.items():
        if s >= threshold:
            if a not in parent:
                parent[a] = a
            if b not in parent:
                parent[b] = b
            union(a, b)

    groups = {}
    for node in parent:
        root = find(node)
        groups.setdefault(root, set()).add(node)
    return [s for s in groups.values() if len(s) >= 2]


@st.cache_data(ttl=600, show_spinner=False)
def build_namemap(_profile, _contract, _redpack, scope_key: str = "ALL"):
    nm = {}
    if not _profile.empty:
        p = _profile[['客户编码', '公司名称', '地市']].drop_duplicates(subset='客户编码')
        for code, name, city in zip(
            p['客户编码'].astype(str).str.strip(),
            p['公司名称'].fillna('').astype(str).str.strip(),
            p['地市'].fillna('').astype(str).str.strip(),
        ):
            nm[code] = (name or '—', city or '—')
    if not _contract.empty:
        # 签约表的公司名列：原列名是「客户名称」，master 视图里才会 rename 成「公司名称」
        name_col = '公司名称' if '公司名称' in _contract.columns else (
            '客户名称' if '客户名称' in _contract.columns else None
        )
        if name_col:
            c = _contract[['客户编码', name_col]].drop_duplicates(subset='客户编码')
            for code, name in zip(
                c['客户编码'].astype(str).str.strip(),
                c[name_col].fillna('').astype(str).str.strip(),
            ):
                if code not in nm:
                    nm[code] = (name or '—', '—')
    if not _redpack.empty:
        r = (_redpack[['上线客户编码', '上线客户名称']]
             .dropna(subset=['上线客户编码']).drop_duplicates(subset='上线客户编码'))
        for code, rname in zip(
            r['上线客户编码'].astype(str).str.strip(),
            r['上线客户名称'].fillna('').astype(str).str.strip(),
        ):
            if not rname:
                continue
            if code not in nm or nm[code][0] == '—':
                existing_city = nm.get(code, ('—', '—'))[1]
                nm[code] = (rname, existing_city)
    return nm


@st.cache_data(ttl=600, show_spinner=False)
def build_rpagg(_redpack, scope_key: str = "ALL"):
    if _redpack.empty:
        return {}, {}
    r = _redpack[['上线客户编码', '产品现有分销价', '中奖金额', '所属一级客户']].copy()
    r['_code'] = r['上线客户编码'].astype(str).str.strip()
    r['_red'] = r['中奖金额'].where(r['中奖金额'] > 0, 0)
    rp_agg = r.groupby('_code').agg(
        台数=('上线客户编码', 'count'),
        金额=('产品现有分销价', 'sum'),
        红包=('_red', 'sum'),
    ).to_dict('index')
    rp_dealers = (r.dropna(subset=['所属一级客户'])
                  .groupby('_code')['所属一级客户']
                  .agg(lambda x: set(str(v).strip() for v in x if str(v).strip()))
                  .to_dict())
    return rp_agg, rp_dealers


# ──────────────────────────────────────────
# 数据状态卡片
# ──────────────────────────────────────────

profile = load_profile_shared()
contract = load_contract_shared()
redpack = load_redpack_shared()
visits = load_visit_shared()
vest = load_vest_shared()  # 已确认马甲账号名单
vest_codes = set(vest['服务商客户编码'].astype(str).str.strip().tolist()) if not vest.empty else set()

# ── 🌍 地市筛选（在数据状态之前，影响所有下游计算）──
st.markdown("#### 🌍 地市筛选")
all_cities = set()
if not profile.empty and '地市' in profile.columns:
    all_cities |= set(profile['地市'].dropna().astype(str).str.strip().tolist())
if not contract.empty and '客户城市' in contract.columns:
    all_cities |= set(contract['客户城市'].dropna().astype(str).str.strip().tolist())
all_cities.discard('')
city_options = sorted(all_cities)

selected_cities = st.multiselect(
    "选地市（不选 = 全省）",
    options=city_options,
    default=[],
    key="cliq_city_filter",
    help="只挖掘选中地市的服务商关联。每个地市单独建立独立 cache（切换地市秒级响应）",
)

# 🔒 scope_key 必须包含 (用户 scope, 选中地市) — 避免不同用户的 cache 串味
from _auth import current_user, get_current_scope, is_admin  # noqa: E402
import hashlib  # noqa: E402

def _build_scope_key() -> str:
    """构造 cache 隔离 key：admin 用户走 'admin'，其它用户用 scope hash 隔离"""
    if is_admin():
        user_part = 'admin'
    else:
        scope = get_current_scope() or {}
        # 把 scope 序列化（按 type 排序，按 value 排序）— 一致性 hash
        canonical = '|'.join(
            f"{k}={','.join(sorted(v))}"
            for k, v in sorted(scope.items())
        )
        user_part = hashlib.md5(canonical.encode('utf-8')).hexdigest()[:10]
    city_part = "_".join(sorted(selected_cities)) if selected_cities else "ALL"
    return f"{user_part}__{city_part}"

scope_key = _build_scope_key()

# 应用地市过滤（用 profile + contract 的并集口径定位有效服务商）
if selected_cities:
    valid_codes = set()
    if not profile.empty and '地市' in profile.columns:
        valid_codes |= set(
            profile[profile['地市'].astype(str).str.strip().isin(selected_cities)]
            ['客户编码'].astype(str).str.strip().tolist()
        )
    if not contract.empty and '客户城市' in contract.columns:
        valid_codes |= set(
            contract[contract['客户城市'].astype(str).str.strip().isin(selected_cities)]
            ['客户编码'].astype(str).str.strip().tolist()
        )

    if not profile.empty:
        profile = profile[profile['客户编码'].astype(str).str.strip().isin(valid_codes)].copy()
    if not contract.empty:
        contract = contract[contract['客户编码'].astype(str).str.strip().isin(valid_codes)].copy()
    if not redpack.empty:
        redpack = redpack[redpack['上线客户编码'].astype(str).str.strip().isin(valid_codes)].copy()
    if not visits.empty:
        visits = visits[visits['客户编码'].astype(str).str.strip().isin(valid_codes)].copy()

st.markdown("#### 📦 数据状态")
if selected_cities:
    st.caption(f"🌍 已筛选地市：**{', '.join(selected_cities)}** · 仅展示这些地市的服务商及其关联记录")
ds = st.columns(5)
ds[0].metric("沙盘", f"{len(profile):,}", help="provider_profile")
ds[1].metric("签约", f"{len(contract):,}", help="provider_contract")
ds[2].metric("红包表", f"{len(redpack):,}", help="install_redpack")
ds[3].metric("拜访记录", f"{len(visits):,}", help="visit_record")
ds[4].metric("🎭 已确认马甲", f"{len(vest_codes):,}",
              help="vest_account 表 — 已确认的代理商自有服务商账号。"
                   "团伙含马甲 → 自动升 🔴 高置信。"
                   "可在主页『📥 数据导入』选「🎭 马甲账号名单」更新")

if profile.empty and contract.empty:
    if selected_cities:
        st.warning(f"📦 选中地市「{', '.join(selected_cities)}」下沙盘和签约都为空，请换其他地市或不选筛选。")
    else:
        st.warning("📦 沙盘和签约数据均为空。请先到主页『📥 数据导入』上传。")
    st.stop()


# ──────────────────────────────────────────
# 参数表单
# ──────────────────────────────────────────

st.markdown("#### ⚙️ 参数")
with st.form("clique_form", border=True):
    cf = st.columns(4)
    with cf[0]:
        p_sync_min = st.number_input(
            "同步上线门槛（台/家/日）", value=5, min_value=1, max_value=50,
            help="单家服务商在同一天 ≥ N 台才算「批量上线」",
        )
    with cf[1]:
        p_score_th = st.number_input(
            "团伙关联评分阈值", value=20, min_value=1, max_value=100,
            help="评分 ≥ 此阈值的对建边并 Union-Find 合并团伙。"
                 "默认 20 = 至少需要 1 个强证据（同电话 / 同址同步 ≥3 次 / 跨地市同址等）。"
                 "调到 10 会引入大量弱边，导致不同小团伙因「亿群下日常批发」等弱关联被粘连。",
        )
    with cf[2]:
        p_min_size = st.number_input(
            "团伙最小成员数", value=2, min_value=2, max_value=10,
        )
    with cf[3]:
        p_top_n = st.number_input(
            "结果显示 Top N", value=200, min_value=10, max_value=2000,
        )

    st.caption(
        "💡 **评分规则**："
        "同老板电话 +15 · "
        "同址加分按密度递减（2 家 +10 / 3-5 家 +6 / 6-10 家 +3 / 11-30 家 +1 / >30 家跳过） · "
        "每次同步上线 +1（≥5 次额外 +5 · ≥10 次额外 +10） · "
        "**🎯 同址同步上线 +5/次**（≥3 次额外 +8 · ≥5 次额外 +15）— **同一 30 分钟窗口 + 同 GPS 地址** = 强项目级团伙信号。"
        "🛡️ **过滤**：「省+市+号+街道」格式且 ≥10 家的工商批量注册地址自动屏蔽；"
        "敬称姓名、噪音电话、双 11 大日（同步 >30 家）、集散安装地址（>30 家）已过滤。"
        "⏱️ **30 分钟时间窗口**：排除集散市场（如义乌通信市场）「全天来取货激活」假阳性 — 仅认半小时内连续激活。"
    )

    run_btn = st.form_submit_button("🚀 开始挖掘", type="primary", use_container_width=True)

cur_params = (int(p_sync_min), int(p_score_th), int(p_min_size))
if run_btn:
    st.session_state["cliq_run"] = True
    st.session_state["cliq_params"] = cur_params

# 没点过按钮 → 显示提示并停在这（独立 page，stop 不影响别人）
if not st.session_state.get("cliq_run"):
    st.info("👆 点上方「🚀 开始挖掘」按钮启动分析。首次约 5~10 秒，结果会缓存，切换控件秒级响应。")
    st.stop()

# 参数变化清缓存
if st.session_state.get("cliq_params") != cur_params:
    for k in list(st.session_state.keys()):
        if str(k).startswith("cliq_data_"):
            del st.session_state[k]
    st.session_state["cliq_run"] = False
    st.rerun()


# ──────────────────────────────────────────
# 流式计算
# ──────────────────────────────────────────

cache_key = f"cliq_data_v12_{scope_key}_" + "_".join(str(x) for x in cur_params)
if cache_key not in st.session_state:
    with st.status("🔄 团伙挖掘中…", expanded=True) as status:
        t0 = time.time()

        st.write("**步骤 1/6**：构建倒排索引（地址 / 电话 / 同步上线）…")
        t = time.time()
        pair_score, pair_evidence = build_evidence(profile, contract, redpack, cur_params[0], scope_key=scope_key)
        st.write(f"&nbsp;&nbsp;✅ {len(pair_score):,} 对服务商有共同证据 · {time.time() - t:.1f}s")

        st.write("**步骤 2/6**：评分聚类（Union-Find）…")
        t = time.time()
        groups = find_groups(pair_score, cur_params[1], scope_key=scope_key)
        groups = [g for g in groups if len(g) >= cur_params[2]]
        n_big = sum(1 for g in groups if len(g) >= 3)
        st.write(f"&nbsp;&nbsp;✅ {len(groups):,} 个团伙（≥3 人重大团伙 {n_big} 个） · {time.time() - t:.1f}s")

        st.write("**步骤 3/6**：构建服务商名字典…")
        t = time.time()
        name_map = build_namemap(profile, contract, redpack, scope_key=scope_key)
        st.write(f"&nbsp;&nbsp;✅ {len(name_map):,} 个服务商建立索引 · {time.time() - t:.1f}s")

        st.write("**步骤 4/6**：聚合上线/红包/代理商…")
        t = time.time()
        rp_agg, rp_dealers = build_rpagg(redpack, scope_key=scope_key)
        st.write(f"&nbsp;&nbsp;✅ {len(rp_agg):,} 个服务商有上线记录 · {time.time() - t:.1f}s")

        st.write("**步骤 5/6**：团伙画像 + 置信度分级…")
        t = time.time()
        group_rows = []
        for gid, members in enumerate(sorted(groups, key=len, reverse=True), 1):
            members = sorted(members)
            n_so = amt_so = amt_rp = 0
            dealers = set()
            for code in members:
                info = rp_agg.get(code)
                if info:
                    n_so += info["台数"]
                    amt_so += info["金额"]
                    amt_rp += info["红包"]
                ds_set = rp_dealers.get(code)
                if ds_set:
                    dealers |= ds_set
            dealers -= {""}
            cities = {name_map.get(c, ("—", "—"))[1] for c in members} - {"—"}
            n_cities = len(cities)
            addr_set, phone_set = set(), set()
            sync_total = 0
            co_install_total = 0  # 同址同步上线累计次数
            # 关键派生：是否有「跨地市同址」证据
            has_cross_city_addr = False
            max_pair_sync = 0
            max_pair_co_install = 0
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    key = (members[i], members[j]) if members[i] < members[j] else (members[j], members[i])
                    ev = pair_evidence.get(key)
                    if not ev:
                        continue
                    addr_set |= ev["addr_p"] | ev["addr_c"]
                    phone_set |= ev["phone"]
                    sync_total += ev["sync"]
                    co_install_total += ev.get("co_install", 0)
                    max_pair_sync = max(max_pair_sync, ev["sync"])
                    max_pair_co_install = max(max_pair_co_install, ev.get("co_install", 0))
                    # 跨地市同址判定
                    if (ev["addr_p"] or ev["addr_c"]):
                        ci = name_map.get(members[i], ("—", "—"))[1]
                        cj = name_map.get(members[j], ("—", "—"))[1]
                        if ci and cj and ci != "—" and cj != "—" and ci != cj:
                            has_cross_city_addr = True

            has_phone = len(phone_set) > 0
            has_addr = len(addr_set) > 0
            has_strong_co_install = max_pair_co_install >= 3

            # 🎭 团伙内含已确认马甲账号
            vest_in_group = set(members) & vest_codes
            n_vest = len(vest_in_group)
            has_vest = n_vest > 0

            # ── 置信度分级 ──
            # 🔴 高置信（铁证）：有同电话 OR 跨地市同址 OR 同址同步 ≥3 次 OR 含已确认马甲
            # 🟡 中置信：单对同步 ≥10 OR 同址同步 ≥1 次 OR (有同址 AND 跨多地市) OR (单对同步 ≥5 AND 同址证据)
            # 🟢 低置信（疑似市场聚集）
            if has_phone or has_cross_city_addr or has_strong_co_install or has_vest:
                conf = "🔴 高"
                conf_reason = []
                if has_vest:
                    conf_reason.append(f"🎭 含 {n_vest} 个已确认马甲")
                if has_phone:
                    conf_reason.append(f"同电话 {len(phone_set)} 个")
                if has_cross_city_addr:
                    conf_reason.append("跨地市同址")
                if has_strong_co_install:
                    conf_reason.append(f"同址同步上线 {max_pair_co_install} 次")
            elif (max_pair_co_install >= 1) or (max_pair_sync >= 10) or (has_addr and n_cities >= 2) or (max_pair_sync >= 5 and has_addr):
                conf = "🟡 中"
                bits = []
                if max_pair_co_install >= 1:
                    bits.append(f"同址同步({max_pair_co_install}次)")
                if max_pair_sync >= 10:
                    bits.append(f"高频同步({max_pair_sync}次)")
                if max_pair_sync >= 5 and has_addr:
                    bits.append("同步+同址")
                if has_addr and n_cities >= 2:
                    bits.append(f"跨{n_cities}地市同址")
                conf_reason = bits
            else:
                conf = "🟢 低"
                conf_reason = ["疑似市场聚集" if (len(members) >= 5 and has_addr) else "单一弱证据"]

            group_rows.append({
                "团伙ID": f"G{gid:03d}",
                "置信度": conf,
                "判定依据": " / ".join(conf_reason) if conf_reason else "—",
                "成员数": len(members),
                "🎭 含马甲数": n_vest,
                "成员服务商": " / ".join(
                    ("🎭 " if c in vest_codes else "") + name_map.get(c, ("—", "—"))[0]
                    for c in members[:5]
                ) + ("…" if len(members) > 5 else ""),
                "客户编码": ", ".join(members[:5]) + ("…" if len(members) > 5 else ""),
                "上线台数": int(n_so),
                "上线金额": int(amt_so),
                "红包总额": int(amt_rp),
                "跨地市": n_cities,
                "跨签约代理商": len(dealers),
                "同址证据": len(addr_set),
                "同电话证据": len(phone_set),
                "同步次数": int(sync_total),
                "同址同步": int(co_install_total),
                "_members": members,
                "_vest_members": vest_in_group,
                "_conf_rank": {"🔴 高": 0, "🟡 中": 1, "🟢 低": 2}[conf],
            })

        # 按 (置信度, 上线金额) 排序：先红再黄再绿，同色按金额降序
        group_rows.sort(key=lambda x: (x["_conf_rank"], -x["上线金额"]))
        n_red = sum(1 for g in group_rows if g["置信度"] == "🔴 高")
        n_yel = sum(1 for g in group_rows if g["置信度"] == "🟡 中")
        n_grn = sum(1 for g in group_rows if g["置信度"] == "🟢 低")
        st.write(
            f"&nbsp;&nbsp;✅ {len(group_rows):,} 个团伙画像 · "
            f"🔴 {n_red} · 🟡 {n_yel} · 🟢 {n_grn} · {time.time() - t:.1f}s"
        )

        st.write("**步骤 6/6**：业务员作假识别（跑动 × 团伙交叉）…")
        t = time.time()
        code_to_group = {}
        for gr in group_rows:
            for code in gr["_members"]:
                code_to_group[code] = gr["团伙ID"]
        # ── 业务员"知情"识别：在同一个团伙内打卡 ≥2 家成员 = 知情该团伙
        # 若大华业务员 + 代理商业务员都对同一团伙知情 → 双重知情，强烈作假嫌疑
        fake_agg = pd.DataFrame()
        if not visits.empty and code_to_group:
            v = visits.copy()
            v["客户编码"] = v["客户编码"].astype(str).str.strip()
            v_in = v[v["客户编码"].isin(code_to_group.keys())].copy()
            if not v_in.empty:
                v_in["团伙ID"] = v_in["客户编码"].map(code_to_group)
                # 第一步：(业务员, 团伙) 维度，看每个组合下打了几家、几次
                # dropna=False 防御性：大华员工某些字段可能 NaN
                vg = v_in.groupby([
                    "打卡人姓名", "打卡人手机号", "_打卡方", "打卡人所属公司", "团伙ID",
                ], dropna=False).agg(
                    服务商数=("客户编码", lambda x: x.nunique()),
                    打卡次数=("活动编号", "count"),
                ).reset_index()
                # 第二步：筛 ≥2 家 = "知情"
                informed = vg[vg["服务商数"] >= 2]
                if not informed.empty:
                    # 第三步：聚合到业务员维度
                    fake_agg = informed.groupby([
                        "打卡人姓名", "打卡人手机号", "_打卡方", "打卡人所属公司",
                    ], dropna=False).agg(
                        知情团伙数=("团伙ID", "nunique"),
                        总知情服务商数=("服务商数", "sum"),
                        总知情打卡次数=("打卡次数", "sum"),
                        覆盖团伙=("团伙ID", lambda x: ", ".join(sorted(x.unique())[:8])),
                    ).reset_index()
        st.write(f"&nbsp;&nbsp;✅ {len(fake_agg):,} 名业务员对至少一个团伙知情（≥2 家成员打卡） · {time.time() - t:.1f}s")

        status.update(
            label=f"✅ 完成 · 总耗时 {time.time() - t0:.1f}s · 发现 {len(groups):,} 个团伙",
            state="complete",
            expanded=False,
        )

    st.session_state[cache_key] = {
        "group_rows": group_rows,
        "pair_score": pair_score,
        "pair_evidence": pair_evidence,
        "name_map": name_map,
        "rp_agg": rp_agg,
        "rp_dealers": rp_dealers,
        "fake_agg": fake_agg,
        "code_to_group": code_to_group,
    }


# ──────────────────────────────────────────
# 渲染结果
# ──────────────────────────────────────────

R = st.session_state[cache_key]
group_rows = R["group_rows"]
pair_score = R["pair_score"]
pair_evidence = R["pair_evidence"]
name_map = R["name_map"]
rp_agg = R["rp_agg"]
rp_dealers = R["rp_dealers"]
fake_agg = R["fake_agg"]


def _name(c):
    return name_map.get(c, ("—", "—"))[0]


def _city(c):
    return name_map.get(c, ("—", "—"))[1]


if not group_rows:
    st.info("当前阈值下未发现团伙。建议降低评分阈值或同步上线门槛后重新挖掘。")
    st.stop()

nodes_in_groups = set()
for gr in group_rows:
    nodes_in_groups |= set(gr["_members"])

n_red = sum(1 for g in group_rows if g["置信度"] == "🔴 高")
n_yel = sum(1 for g in group_rows if g["置信度"] == "🟡 中")
n_grn = sum(1 for g in group_rows if g["置信度"] == "🟢 低")

st.markdown("#### 📊 总览（按置信度分级）")
kc = st.columns(4)
kc[0].metric(
    "🔴 高置信团伙",
    f"{n_red:,}",
    help="有同电话 OR 跨地市同址 — 几乎确定是真团伙，立即人工核查",
)
kc[1].metric(
    "🟡 中置信团伙",
    f"{n_yel:,}",
    help="高频同步上线 OR 跨地市同址 — 值得调查",
)
kc[2].metric(
    "🟢 低置信团伙",
    f"{n_grn:,}",
    help="仅同址或低频同步 — 多数是市场聚集（产业园 / 通信市场）",
)
kc[3].metric("涉及服务商数", f"{len(nodes_in_groups):,}")

if n_red > 0:
    st.error(
        f"🚨 **{n_red} 个高置信团伙需要立即核查**：包含 "
        f"{sum(g['成员数'] for g in group_rows if g['置信度'] == '🔴 高'):,} 家服务商，"
        f"累计上线 ¥{sum(g['上线金额'] for g in group_rows if g['置信度'] == '🔴 高'):,.0f}。"
    )

st.divider()


# ── A. 团伙清单（按置信度筛选） ──
st.markdown("#### 🔗 团伙清单")

filt_col = st.columns([1.5, 1.5, 1, 3])
with filt_col[0]:
    conf_filter = st.multiselect(
        "置信度筛选",
        options=["🔴 高", "🟡 中", "🟢 低"],
        default=["🔴 高", "🟡 中"],  # 默认隐藏 🟢（多数是市场假阳性）
        key="conf_filter",
    )
with filt_col[1]:
    size_filter = st.radio(
        "团伙规模",
        options=["全部", "小团伙（2-3 人）", "中团伙（4-9 人）", "大团伙（≥10 人，疑似市场聚集）"],
        index=0,
        key="size_filter",
        horizontal=False,
    )
with filt_col[2]:
    sort_by = st.selectbox(
        "排序",
        options=["置信度+金额", "上线金额", "成员数", "同步次数"],
        key="cliq_sort",
    )

# 应用筛选
group_df_full = pd.DataFrame(group_rows)
group_df = group_df_full.copy()
if conf_filter:
    group_df = group_df[group_df["置信度"].isin(conf_filter)]
if size_filter == "小团伙（2-3 人）":
    group_df = group_df[group_df["成员数"].between(2, 3)]
elif size_filter == "中团伙（4-9 人）":
    group_df = group_df[group_df["成员数"].between(4, 9)]
elif size_filter == "大团伙（≥10 人，疑似市场聚集）":
    group_df = group_df[group_df["成员数"] >= 10]

if sort_by == "上线金额":
    group_df = group_df.sort_values("上线金额", ascending=False)
elif sort_by == "成员数":
    group_df = group_df.sort_values("成员数", ascending=False)
elif sort_by == "同步次数":
    group_df = group_df.sort_values("同步次数", ascending=False)
# else: 默认已按 置信度+金额 排序

st.caption(f"筛选后：**{len(group_df):,}** 个团伙（共 {len(group_df_full):,} 个）")

display_cols = [c for c in group_df.columns if not c.startswith("_")]
st.dataframe(
    group_df[display_cols].head(int(p_top_n)),
    use_container_width=True, hide_index=True,
    height=min(60 + len(group_df) * 36, 500),
)

buf_g = io.BytesIO()
with pd.ExcelWriter(buf_g, engine="openpyxl") as w:
    group_df[display_cols].to_excel(w, sheet_name="团伙清单", index=False)
stamp_g = datetime.now().strftime("%Y%m%d")
st.download_button(
    f"📥 导出筛选后团伙清单（{len(group_df):,} 个）",
    data=buf_g.getvalue(),
    file_name=f"关联服务商_团伙_{stamp_g}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="cliq_dl_groups",
)

st.divider()


# ── B. 业务员作假识别 ──
st.markdown("#### 🚨 业务员作假嫌疑（团伙内频繁打卡）")
st.caption(
    "若同一业务员在团伙内 ≥ 2 家服务商有打卡记录 → 作假嫌疑。"
    "**跨多个团伙打卡的业务员 = 真实控制人或共谋者嫌疑。**"
)

if fake_agg.empty:
    st.info("跑动表里没有任何业务员对团伙内 ≥2 家成员打过卡。")
else:
    ck = st.columns(3)
    with ck[0]:
        fake_min_groups = st.number_input(
            "知情团伙数 ≥", value=1, min_value=1, max_value=10,
            key="cliq_fake_groups",
            help="该业务员对几个不同团伙都有 ≥2 家成员的打卡。"
                 "≥2 = 跨多个团伙都知情 = 高风险",
        )
    with ck[1]:
        fake_min_visits = st.number_input(
            "总知情打卡次数 ≥", value=3, min_value=1, max_value=50,
            key="cliq_fake_visits",
        )
    with ck[2]:
        fake_show_party = st.selectbox(
            "查看：", options=["全部", "🏢 大华业务员", "🏪 代理商业务员"],
            key="cliq_fake_party",
        )

    susp = fake_agg[
        (fake_agg["知情团伙数"] >= fake_min_groups)
        & (fake_agg["总知情打卡次数"] >= fake_min_visits)
    ].copy()
    if fake_show_party == "🏢 大华业务员":
        susp = susp[susp["_打卡方"] == "🏢 大华"]
    elif fake_show_party == "🏪 代理商业务员":
        susp = susp[susp["_打卡方"] == "🏪 代理商"]
    susp = susp.sort_values(
        ["知情团伙数", "总知情服务商数", "总知情打卡次数"], ascending=False,
    )

    # 关键派生：哪些团伙同时被「大华业务员」和「代理商业务员」知情 → 双重知情
    # 每个团伙 → {大华知情人 set, 代理商知情人 set}
    informed_by_group_dh = defaultdict(set)
    informed_by_group_dl = defaultdict(set)
    for _, r in fake_agg.iterrows():
        gids = str(r["覆盖团伙"]).split(", ")
        for gid in gids:
            if r["_打卡方"] == "🏢 大华":
                informed_by_group_dh[gid].add(r["打卡人姓名"])
            elif r["_打卡方"] == "🏪 代理商":
                informed_by_group_dl[gid].add(r["打卡人姓名"])
    double_informed_groups = [
        gid for gid in (informed_by_group_dh.keys() | informed_by_group_dl.keys())
        if informed_by_group_dh.get(gid) and informed_by_group_dl.get(gid)
    ]

    if susp.empty:
        st.success("✅ 当前阈值下未发现可疑业务员")
    else:
        n_dahua = int((susp["_打卡方"] == "🏢 大华").sum())
        n_dealer = int((susp["_打卡方"] == "🏪 代理商").sum())
        n_cross = int((susp["知情团伙数"] >= 2).sum())
        ww = st.columns(4)
        ww[0].metric("知情业务员", f"{len(susp):,}")
        ww[1].metric("🏢 大华 / 🏪 代理商", f"{n_dahua} / {n_dealer}")
        ww[2].metric("跨 ≥2 团伙知情", f"{n_cross:,}",
                      help="同一业务员对多个团伙都知情 = 极高风险")
        ww[3].metric("🔴 双重知情团伙", f"{len(double_informed_groups):,}",
                      help="同时被「大华业务员」和「代理商业务员」知情 = 两端都知道但都没上报")

        if double_informed_groups:
            st.error(
                f"🚨 **{len(double_informed_groups)} 个团伙被双重知情**："
                f"既有大华业务员又有代理商业务员对其 ≥2 家成员打过卡。"
                f"两端业务员都「知道」这些是关联服务商但都没标记 → 强烈作假嫌疑！"
            )
            sample = double_informed_groups[:5]
            st.caption(f"双重知情团伙样例：{', '.join(sample)}{'…' if len(double_informed_groups) > 5 else ''}")

        if n_cross > 0:
            st.warning(
                f"⚠️ {n_cross} 名业务员跨 ≥2 个团伙都知情，可能是真实控制人或共谋者"
            )

        susp_show = susp.rename(columns={"_打卡方": "身份"}).copy()
        order_cols = [c for c in [
            "身份", "打卡人姓名", "打卡人所属公司",
            "知情团伙数", "总知情服务商数", "总知情打卡次数", "覆盖团伙",
        ] if c in susp_show.columns]
        st.dataframe(
            susp_show[order_cols].head(200),
            use_container_width=True, hide_index=True,
            height=min(60 + len(susp_show) * 36, 500),
        )

        buf_f = io.BytesIO()
        with pd.ExcelWriter(buf_f, engine="openpyxl") as w:
            susp_show.to_excel(w, sheet_name="知情业务员", index=False)
        st.download_button(
            f"📥 导出知情业务员（{len(susp):,} 名）",
            data=buf_f.getvalue(),
            file_name=f"关联服务商_知情业务员_{stamp_g}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="cliq_dl_fake",
        )

st.divider()


# ── B2. 🎭 疑似新马甲建议（含马甲团伙里的非马甲成员 + 跟马甲直接强关联的服务商）──
st.markdown("#### 🎭 疑似新马甲建议名单")
st.caption(
    "**逻辑**：跟已确认马甲账号在同一团伙里、或跟马甲有强关联（评分 ≥ 阈值）但**自身尚未被标为马甲**的服务商。"
    "建议交给区域 BD 实地核查后追加到马甲表（主页 🎭 马甲账号名单 上传更新）。"
)

if not vest_codes:
    st.info("📦 尚未导入马甲名单，无法生成建议。请到主页『📥 数据导入』选「🎭 马甲账号名单」上传。")
elif not group_rows:
    st.info("当前阈值下未发现团伙，无法生成扩散嫌疑。")
else:
    # 收集嫌疑：含马甲团伙里的非马甲成员
    suspect = {}  # code -> {vests, groups, max_pair_score}
    for gr in group_rows:
        vests_in_g = gr.get("_vest_members") or set()
        if not vests_in_g:
            continue
        for code in gr["_members"]:
            if code in vest_codes:
                continue
            if code not in suspect:
                suspect[code] = {"vests": set(), "groups": set(), "max_pair_score": 0}
            # 收集该非马甲跟团伙内每个马甲的关联评分
            for v in vests_in_g:
                key = (code, v) if code < v else (v, code)
                s = pair_score.get(key, 0)
                if s > 0:  # 只记真正有边的（pair_score 有值）
                    suspect[code]["vests"].add(v)
                    suspect[code]["max_pair_score"] = max(suspect[code]["max_pair_score"], s)
            suspect[code]["groups"].add(gr["团伙ID"])

    # 也包含：跟马甲有边但不在团伙里（评分 < 20 = union-find 阈值，但仍有连接）
    # 已经被团伙覆盖的就不重复，这里只补充孤立连接
    for (a, b), s in pair_score.items():
        if s < 10:  # 太弱的不算嫌疑
            continue
        x_vest = a in vest_codes
        y_vest = b in vest_codes
        if x_vest == y_vest:
            continue
        # 一个是马甲一个不是
        non = b if x_vest else a
        ves = a if x_vest else b
        if non in suspect:
            suspect[non]["vests"].add(ves)
            suspect[non]["max_pair_score"] = max(suspect[non]["max_pair_score"], s)
        else:
            # 孤立的弱连接也加入（以便审查）
            suspect[non] = {
                "vests": {ves},
                "groups": set(),
                "max_pair_score": s,
            }

    # 生成嫌疑清单
    suspect_rows = []
    # 取马甲对应一级（代理商名）字典
    vest_dealer_map = {}
    if not vest.empty and '对应一级' in vest.columns:
        for code, dealer in zip(
            vest['服务商客户编码'].astype(str).str.strip(),
            vest['对应一级'].fillna('').astype(str),
        ):
            if dealer:
                vest_dealer_map[code] = dealer

    for code, info in suspect.items():
        n_v = len(info["vests"])
        max_s = info["max_pair_score"]
        # 嫌疑等级
        if n_v >= 3 or (n_v >= 1 and max_s >= 30):
            level = "⚫ 极高"
            level_rank = 0
        elif n_v >= 2 or max_s >= 20:
            level = "🔴 高"
            level_rank = 1
        else:
            level = "🟡 中"
            level_rank = 2

        nm, city = name_map.get(code, ("—", "—"))
        rp_info = rp_agg.get(code, {})
        # 关联马甲样例 + 它们的"对应一级"
        sample_vests = sorted(info["vests"])[:5]
        vest_sample_str = ", ".join(name_map.get(v, ("—", "—"))[0] for v in sample_vests) + ("…" if n_v > 5 else "")
        # 推断：关联马甲背后的代理商
        related_dealers = set()
        for v in info["vests"]:
            d = vest_dealer_map.get(v)
            if d:
                related_dealers.add(d)
        # 该服务商自己的签约代理商（如有）
        own_dealers = rp_dealers.get(code, set()) - {""}
        # 判断关联马甲代理商和自己签约代理商是否一致 — 一致 = 高度像同一控制人
        same_dealer = bool(own_dealers & related_dealers)

        suspect_rows.append({
            "嫌疑等级": level,
            "_rank": level_rank,
            "客户编码": code,
            "公司名称": nm,
            "地市": city,
            "关联马甲数": n_v,
            "最高关联评分": int(max_s),
            "关联马甲样例": vest_sample_str,
            "马甲背后代理商": ", ".join(sorted(related_dealers)[:3]),
            "自身签约代理商": ", ".join(sorted(own_dealers)[:3]),
            "同代理商": "✅" if same_dealer else "—",
            "所在团伙": ", ".join(sorted(info["groups"])) if info["groups"] else "—（弱连接，未成团）",
            "上线台数": int(rp_info.get("台数", 0)),
            "上线金额": int(rp_info.get("金额", 0)),
            "红包总额": int(rp_info.get("红包", 0)),
        })

    suspect_df = pd.DataFrame(suspect_rows).sort_values(
        ["_rank", "关联马甲数", "最高关联评分"],
        ascending=[True, False, False],
    ).drop(columns=["_rank"])

    # 总览 + 筛选
    n_extreme = (suspect_df["嫌疑等级"] == "⚫ 极高").sum()
    n_high = (suspect_df["嫌疑等级"] == "🔴 高").sum()
    n_mid = (suspect_df["嫌疑等级"] == "🟡 中").sum()
    n_same_dealer = (suspect_df["同代理商"] == "✅").sum()

    sk = st.columns(4)
    sk[0].metric("总嫌疑数", f"{len(suspect_df):,}")
    sk[1].metric("⚫ 极高 / 🔴 高 / 🟡 中", f"{n_extreme} / {n_high} / {n_mid}")
    sk[2].metric("✅ 与马甲同代理商", f"{n_same_dealer:,}",
                  help="自身签约代理商与关联马甲背后代理商一致 — 几乎确定是同一控制人")
    sk[3].metric("总上线金额", f"¥{suspect_df['上线金额'].sum():,.0f}",
                  help="这些嫌疑账号累计的上线金额")

    if n_same_dealer > 0:
        st.error(
            f"🚨 **{n_same_dealer} 个嫌疑账号与已知马甲挂在同一代理商下** —— "
            f"基本可以认定为新马甲，建议优先核查后追加到马甲名单。"
        )

    # 筛选
    sf = st.columns([1, 1, 3])
    with sf[0]:
        level_filter = st.multiselect(
            "嫌疑等级筛选",
            options=["⚫ 极高", "🔴 高", "🟡 中"],
            default=["⚫ 极高", "🔴 高"],
            key="vest_level_filter",
        )
    with sf[1]:
        only_same_dealer = st.checkbox(
            "仅看「同代理商」",
            value=False, key="vest_same_dealer",
            help="勾选 = 只看跟马甲共上级代理商的（最高置信）",
        )

    show_df = suspect_df.copy()
    if level_filter:
        show_df = show_df[show_df["嫌疑等级"].isin(level_filter)]
    if only_same_dealer:
        show_df = show_df[show_df["同代理商"] == "✅"]

    st.caption(f"筛选后：**{len(show_df):,}** 个嫌疑账号（共 {len(suspect_df):,}）")

    st.dataframe(
        show_df,
        use_container_width=True, hide_index=True,
        height=min(60 + len(show_df) * 36, 500),
    )

    # 导出
    buf_v = io.BytesIO()
    with pd.ExcelWriter(buf_v, engine="openpyxl") as w:
        show_df.to_excel(w, sheet_name="疑似新马甲建议", index=False)
        # 同时输出"全量"sheet 方便比较
        suspect_df.to_excel(w, sheet_name="全量嫌疑", index=False)
    st.download_button(
        f"📥 导出疑似新马甲建议（{len(show_df):,} 个，含全量 sheet）",
        data=buf_v.getvalue(),
        file_name=f"疑似新马甲建议_{stamp_g}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="cliq_dl_vest_suspect",
    )

    st.caption(
        "💡 **追加到马甲表的方法**：人工核查导出的 Excel → 把确认的新马甲行（含「客户编码」「公司名称」「城市」「对应一级」）"
        "拼到 `data/马甲客户名单.xlsx` → 主页『📥 数据导入』选「🎭 马甲账号名单」重新上传 → 重新点 🚀 开始挖掘。"
    )

st.divider()


# ── B3. 📐 挤水分名单（主账号 ⊃ 伞形账号）──
st.markdown("#### 📐 挤水分名单（主账号 ⊃ 伞形账号）")
st.caption(
    "**业务逻辑**：业务员为完成 KPI 用个人姓名新建了一批「伞形账号」挂在某个真实服务商下。"
    "这些账号实际归属同一公司，应合并为一个服务商。"
    "**用途**：导出后给管理层用于约束业务员的开户行为。"
)

# 识别"公司型账号" vs "个人型账号"
COMPANY_KW = [
    '有限公司', '股份', '集团',
    '商行', '商贸', '科技', '电子', '网络', '智能',
    '工程', '建设', '装饰', '设备', '咨询',
    '经营部', '专卖店', '工作室', '中心', '门市部',
    '安防', '通信', '信息', '服务部', '电脑',
    '维修', '办公', '广告', '传媒', '数码',
]


def _is_company(nm: str) -> bool:
    """是否像公司名"""
    if not nm or nm == '—' or nm == '':
        return False
    s = str(nm).strip()
    for kw in COMPANY_KW:
        if kw in s:
            return True
    return False


def _is_personal(nm: str) -> bool:
    """是否像个人名（无公司关键词 + 长度 ≤4 字）"""
    if not nm or nm == '—' or nm == '':
        return False
    s = str(nm).replace(' ', '').strip()
    if _is_company(s):
        return False
    return len(s) <= 4


if not group_rows:
    st.info("当前阈值下未发现团伙，无法生成挤水分名单。")
else:
    umbrella_rows = []
    for gr in group_rows:
        members = gr["_members"]
        # 给每个成员打公司/个人/未知标签 + 上线量
        member_info = []
        for code in members:
            nm = name_map.get(code, ("—", "—"))[0]
            info = rp_agg.get(code, {})
            member_info.append({
                "code": code,
                "name": nm,
                "is_company": _is_company(nm),
                "is_personal": _is_personal(nm),
                "is_vest": code in vest_codes,
                "n_so": info.get("台数", 0),
                "amt": info.get("金额", 0),
            })

        # 主账号选择优先级：
        # 1. 上线量最大的"公司型账号"
        # 2. 没公司账号 → 上线量最大的成员（即使是个人户）
        companies = [m for m in member_info if m["is_company"]]
        if companies:
            main = max(companies, key=lambda m: m["amt"])
            main_type = "✅ 公司型"
        else:
            main = max(member_info, key=lambda m: m["amt"])
            main_type = "⚠️ 全是个人户（无明显主账号）"

        # 伞形账号 = 其他所有成员
        umbrella = [m for m in member_info if m["code"] != main["code"]]
        # 个人型伞形（典型业务员"水分"账号）
        umbrella_personal = [m for m in umbrella if m["is_personal"]]
        umbrella_other = [m for m in umbrella if not m["is_personal"]]

        # 上级代理商（从主账号的签约信息或 master 视图取）
        own_dealers = rp_dealers.get(main["code"], set()) - {""}
        # 备用：拼整个团伙涉及的代理商
        all_dealers = set()
        for m in member_info:
            ds = rp_dealers.get(m["code"], set()) - {""}
            all_dealers |= ds
        # 主账号自身签约代理商作为"上级一级"，团伙内其他代理商可能不同（涉嫌跨渠道）
        main_dealer = ", ".join(sorted(own_dealers)[:2]) if own_dealers else "—"
        cross_dealer_n = len(all_dealers)

        # 累计金额
        umb_amt = sum(m["amt"] for m in umbrella)
        umb_台 = sum(m["n_so"] for m in umbrella)
        umb_personal_amt = sum(m["amt"] for m in umbrella_personal)
        umb_personal_台 = sum(m["n_so"] for m in umbrella_personal)

        umbrella_rows.append({
            "团伙ID": gr["团伙ID"],
            "置信度": gr["置信度"],
            "主账号类型": main_type,
            "主账号公司": main["name"] + (" 🎭" if main["is_vest"] else ""),
            "主账号编码": main["code"],
            "主账号上线台数": int(main["n_so"]),
            "主账号上线金额": int(main["amt"]),
            "伞形账号数": len(umbrella),
            "其中个人户": len(umbrella_personal),
            "其中含马甲": sum(1 for m in umbrella if m["is_vest"]),
            "伞形累计台数": int(umb_台),
            "伞形累计金额": int(umb_amt),
            "（其中）个人户累计金额": int(umb_personal_amt),
            "伞形清单（个人户优先）": " / ".join(
                ("🎭 " if m["is_vest"] else "") + m["name"]
                for m in (umbrella_personal + umbrella_other)[:8]
            ) + ("…" if len(umbrella) > 8 else ""),
            "上级一级代理商": main_dealer,
            "团伙跨代理商数": cross_dealer_n,
            "判定依据": gr["判定依据"],
            "_members": members,
            "_main_code": main["code"],
            "_umb_codes": [m["code"] for m in umbrella],
            "_n_personal": len(umbrella_personal),
            "_main_is_company": bool(companies),
        })

    umb_df = pd.DataFrame(umbrella_rows)

    # 总览
    n_total = len(umb_df)
    n_with_main_co = sum(1 for r in umbrella_rows if r["_main_is_company"])
    n_have_personal = sum(1 for r in umbrella_rows if r["_n_personal"] > 0)
    total_personal_amt = sum(r["（其中）个人户累计金额"] for r in umbrella_rows)
    total_umb_amt = umb_df["伞形累计金额"].sum()

    uk = st.columns(4)
    uk[0].metric("待挤水分团伙数", f"{n_total:,}")
    uk[1].metric("✅ 有明显公司主账号", f"{n_with_main_co:,}",
                  help="伞形账号下挂在真公司账号 — 业务侧应合并这些账号")
    uk[2].metric("含个人户的团伙", f"{n_have_personal:,}",
                  help="伞形里有个人姓名账号 — 典型 KPI 注水")
    uk[3].metric("个人户累计上线金额", f"¥{total_personal_amt:,.0f}",
                  help="如果合并掉这些个人户，主账号会增加这么多业绩")

    if total_personal_amt > 0:
        st.warning(
            f"💧 **可挤水分**：合并掉所有个人户伞形账号后，**¥{total_personal_amt:,.0f}**"
            f" 的上线金额会归到真实服务商账号下。这部分原本被分散到 {sum(r['_n_personal'] for r in umbrella_rows):,} "
            f"个个人户账号里，造成 KPI 虚增。"
        )

    # 筛选
    fc = st.columns([1, 1, 3])
    with fc[0]:
        umb_only_with_co = st.checkbox(
            "仅看有公司主账号的", value=True, key="umb_only_co",
            help="只看「主账号是真公司」的团伙 — 这部分最适合直接合并"
        )
    with fc[1]:
        umb_min_personal = st.number_input(
            "伞形个人户数 ≥",
            value=1, min_value=0, max_value=20,
            key="umb_min_personal",
        )

    show_umb = umb_df.copy()
    if umb_only_with_co:
        show_umb = show_umb[show_umb["_main_is_company"] == True]
    if umb_min_personal > 0:
        show_umb = show_umb[show_umb["其中个人户"] >= umb_min_personal]
    show_umb = show_umb.sort_values(
        ["其中个人户", "伞形累计金额"], ascending=[False, False],
    )

    display_cols = [c for c in show_umb.columns if not c.startswith("_")]
    st.caption(f"筛选后：**{len(show_umb):,}** 个团伙待挤水分（共 {len(umb_df):,}）")
    st.dataframe(
        show_umb[display_cols],
        use_container_width=True, hide_index=True,
        height=min(60 + len(show_umb) * 36, 500),
    )

    # 导出 — 含两个 sheet：汇总表 + 明细表（每个伞形账号单独一行，便于复制粘贴到管理系统）
    buf_u = io.BytesIO()
    with pd.ExcelWriter(buf_u, engine="openpyxl") as w:
        # Sheet 1: 汇总（每团伙一行）
        umb_df[display_cols].to_excel(w, sheet_name="挤水分汇总", index=False)
        show_umb[display_cols].to_excel(w, sheet_name="筛选后", index=False)

        # Sheet 3: 主账号-伞形账号 一对多明细（便于直接合并操作）
        detail_rows = []
        for r in umbrella_rows:
            for code in r["_umb_codes"]:
                nm = name_map.get(code, ("—", "—"))[0]
                info = rp_agg.get(code, {})
                detail_rows.append({
                    "团伙ID": r["团伙ID"],
                    "主账号公司": r["主账号公司"],
                    "主账号编码": r["主账号编码"],
                    "伞形账号公司": ("🎭 " if code in vest_codes else "") + nm,
                    "伞形账号编码": code,
                    "伞形账号类型": "🎭 已确认马甲" if code in vest_codes
                                else ("👤 个人户" if _is_personal(nm) else "🏢 公司"),
                    "伞形上线台数": int(info.get("台数", 0)),
                    "伞形上线金额": int(info.get("金额", 0)),
                    "上级一级代理商": r["上级一级代理商"],
                })
        if detail_rows:
            pd.DataFrame(detail_rows).to_excel(w, sheet_name="主-伞 一对多明细", index=False)

    st.download_button(
        f"📥 导出挤水分名单（汇总 + 筛选后 + 一对多明细，共 3 张表）",
        data=buf_u.getvalue(),
        file_name=f"挤水分名单_主账号_伞形_{stamp_g}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="cliq_dl_umbrella",
    )

    st.caption(
        "💡 **管理用途**："
        "① 把「主-伞 一对多明细」表交给业务总监 → 强制业务员把伞形账号挂回主账号；"
        "② 把含「公司主账号 + 个人户伞形」的明细给区域经理 → 这部分是最纯粹的 KPI 注水；"
        "③ 后续在开户系统中加一道审核：相同地址/电话/同步上线已存在的服务商 → 禁止新建账号。"
    )

st.divider()


# ── B4. 📊 KPI 影响量化（伞形拆账 vs 合并视图）──
st.markdown("#### 📊 KPI 影响量化（伞形拆账造成的等级虚增）")
st.caption(
    "**公司 KPI 等级**（按累计安装红包金额）：**激活 ≥¥1,000** · **V2 ¥1k-1w** · **V3 ¥1w-3w** · **V4 ≥¥3w**。"
    "伞形拆账会让一家真服务商的额度被分散到多个小户上，导致 V2/V3/V4 被虚增、V4 被虚减。"
    "下表对比 **拆分视图（现状）** vs **合并视图（应有）** 的差异。"
)


def _kpi_split(amounts):
    """单服务商列表 → 各等级数量（拆分视图）"""
    return {
        "激活": sum(1 for a in amounts if a >= 1000),
        "V2": sum(1 for a in amounts if 1000 <= a < 10000),
        "V3": sum(1 for a in amounts if 10000 <= a < 30000),
        "V4": sum(1 for a in amounts if a >= 30000),
    }


def _kpi_merged(total):
    """合并金额 → 各等级数量（合并视图，每个团伙最多 1 个）"""
    return {
        "激活": 1 if total >= 1000 else 0,
        "V2": 1 if 1000 <= total < 10000 else 0,
        "V3": 1 if 10000 <= total < 30000 else 0,
        "V4": 1 if total >= 30000 else 0,
    }


if not group_rows:
    st.info("当前阈值下未发现团伙。")
elif not rp_agg:
    st.info("红包数据为空，无法量化 KPI 影响。")
else:
    kpi_rows = []
    for gr in group_rows:
        members = gr["_members"]
        amounts = [rp_agg.get(c, {}).get("红包", 0) for c in members]
        # 上线金额（不是红包金额）作为参考
        amounts_so = sum(rp_agg.get(c, {}).get("金额", 0) for c in members)

        sp = _kpi_split(amounts)
        total_red = sum(amounts)
        mg = _kpi_merged(total_red)

        # 虚增（拆分 - 合并）
        delta = {k: sp[k] - mg[k] for k in ["激活", "V2", "V3", "V4"]}

        # 主账号信息（沿用挤水分逻辑）
        member_info = []
        for code in members:
            nm = name_map.get(code, ("—", "—"))[0]
            info = rp_agg.get(code, {})
            member_info.append({"code": code, "name": nm, "is_company": _is_company(nm), "amt": info.get("金额", 0)})
        companies = [m for m in member_info if m["is_company"]]
        if companies:
            main = max(companies, key=lambda m: m["amt"])
        else:
            main = max(member_info, key=lambda m: m["amt"])

        kpi_rows.append({
            "团伙ID": gr["团伙ID"],
            "置信度": gr["置信度"],
            "主账号": main["name"],
            "成员数": len(members),
            "拆分_激活": sp["激活"],
            "拆分_V2": sp["V2"],
            "拆分_V3": sp["V3"],
            "拆分_V4": sp["V4"],
            "合并红包总额": int(total_red),
            "合并_激活": mg["激活"],
            "合并_V2": mg["V2"],
            "合并_V3": mg["V3"],
            "合并_V4": mg["V4"],
            "虚增_激活": delta["激活"],
            "虚增_V2": delta["V2"],
            "虚增_V3": delta["V3"],
            "虚增_V4": delta["V4"],
            "_total_red": total_red,
            "_total_so": amounts_so,
        })

    kpi_df = pd.DataFrame(kpi_rows)

    # 全省汇总
    sum_split = {k: int(kpi_df[f"拆分_{k}"].sum()) for k in ["激活", "V2", "V3", "V4"]}
    sum_merged = {k: int(kpi_df[f"合并_{k}"].sum()) for k in ["激活", "V2", "V3", "V4"]}
    sum_delta = {k: sum_split[k] - sum_merged[k] for k in ["激活", "V2", "V3", "V4"]}

    st.markdown("##### 全省汇总：拆分视图 vs 合并视图")
    summary_df = pd.DataFrame([
        {
            "等级": "✅ 激活（≥¥1k）",
            "拆分视图（现状KPI）": sum_split["激活"],
            "合并视图（应有）": sum_merged["激活"],
            "虚增数": sum_delta["激活"],
            "虚增率": f"{sum_delta['激活'] / max(sum_merged['激活'], 1) * 100:+.0f}%"
                if sum_merged["激活"] else "—",
        },
        {
            "等级": "🥉 V2（¥1k-1w）",
            "拆分视图（现状KPI）": sum_split["V2"],
            "合并视图（应有）": sum_merged["V2"],
            "虚增数": sum_delta["V2"],
            "虚增率": f"{sum_delta['V2'] / max(sum_merged['V2'], 1) * 100:+.0f}%"
                if sum_merged["V2"] else "—",
        },
        {
            "等级": "🥈 V3（¥1w-3w）",
            "拆分视图（现状KPI）": sum_split["V3"],
            "合并视图（应有）": sum_merged["V3"],
            "虚增数": sum_delta["V3"],
            "虚增率": f"{sum_delta['V3'] / max(sum_merged['V3'], 1) * 100:+.0f}%"
                if sum_merged["V3"] else "—",
        },
        {
            "等级": "🥇 V4（≥¥3w）",
            "拆分视图（现状KPI）": sum_split["V4"],
            "合并视图（应有）": sum_merged["V4"],
            "虚增数": sum_delta["V4"],
            "虚增率": f"{sum_delta['V4'] / max(sum_merged['V4'], 1) * 100:+.0f}%"
                if sum_merged["V4"] else "—",
        },
    ])
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # 关键洞察
    n_v4_lost = sum_merged["V4"] - sum_split["V4"]  # 合并多于拆分 = V4 被拆分稀释了
    if n_v4_lost > 0 or sum_delta["V2"] > 0:
        msg_parts = []
        if n_v4_lost > 0:
            msg_parts.append(f"💎 **{n_v4_lost} 个被稀释的 V4 服务商**：合并后能多出这么多 V4")
        if sum_delta["V2"] > 0:
            msg_parts.append(f"💧 **V2 虚增 {sum_delta['V2']} 个**：本应是 V3/V4 的服务商被拆成多个 V2")
        if sum_delta["V3"] != 0:
            sign = "虚增" if sum_delta["V3"] > 0 else "虚减"
            msg_parts.append(f"🔄 **V3 {sign} {abs(sum_delta['V3'])} 个**")
        st.warning(" · ".join(msg_parts))

    # 顶部 4 个 metric
    st.markdown("##### 关键指标")
    mk = st.columns(4)
    mk[0].metric("激活 虚增",
                  f"{sum_delta['激活']:+d}",
                  delta=f"{sum_split['激活']:,} → {sum_merged['激活']:,}")
    mk[1].metric("V2 虚增",
                  f"{sum_delta['V2']:+d}",
                  delta=f"{sum_split['V2']:,} → {sum_merged['V2']:,}")
    mk[2].metric("V3 虚增",
                  f"{sum_delta['V3']:+d}",
                  delta=f"{sum_split['V3']:,} → {sum_merged['V3']:,}")
    mk[3].metric("V4 虚增",
                  f"{sum_delta['V4']:+d}",
                  delta=f"{sum_split['V4']:,} → {sum_merged['V4']:,}",
                  help="V4 多为负数 — 因为 V2/V3 拆分后稀释了 V4 数量")

    # 团伙级别明细
    st.markdown("##### 团伙级别 KPI 影响明细")
    kpi_filter = st.columns([1, 1, 3])
    with kpi_filter[0]:
        only_v4_diluted = st.checkbox(
            "仅看「V4 被稀释」", value=False, key="kpi_v4_dilute",
            help="勾选 = 只看合并金额 ≥3 万但拆分后 V4=0 的团伙",
        )
    with kpi_filter[1]:
        kpi_min_delta = st.number_input(
            "总虚增数 ≥",
            value=1, min_value=0, max_value=20,
            key="kpi_min_delta",
            help="拆分vs合并的等级总差异",
        )

    show_kpi = kpi_df.copy()
    show_kpi["_total_delta"] = show_kpi[["虚增_激活", "虚增_V2", "虚增_V3"]].abs().sum(axis=1) + show_kpi["虚增_V4"].abs()
    if only_v4_diluted:
        show_kpi = show_kpi[(show_kpi["合并红包总额"] >= 30000) & (show_kpi["拆分_V4"] == 0)]
    if kpi_min_delta > 0:
        show_kpi = show_kpi[show_kpi["_total_delta"] >= kpi_min_delta]
    show_kpi = show_kpi.sort_values("合并红包总额", ascending=False)

    display_cols = [c for c in show_kpi.columns if not c.startswith("_")]
    st.caption(f"筛选后：**{len(show_kpi):,}** 个团伙（共 {len(kpi_df):,}）")
    st.dataframe(
        show_kpi[display_cols],
        use_container_width=True, hide_index=True,
        height=min(60 + len(show_kpi) * 36, 500),
    )

    # 导出
    buf_k = io.BytesIO()
    with pd.ExcelWriter(buf_k, engine="openpyxl") as w:
        summary_df.to_excel(w, sheet_name="全省KPI对比", index=False)
        kpi_df[display_cols].to_excel(w, sheet_name="团伙KPI明细", index=False)
        show_kpi[display_cols].to_excel(w, sheet_name="筛选后", index=False)
    st.download_button(
        f"📥 导出 KPI 影响量化（{len(kpi_df):,} 个团伙）",
        data=buf_k.getvalue(),
        file_name=f"KPI虚增量化_伞形拆账_{stamp_g}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="cliq_dl_kpi",
    )

    st.caption(
        "💡 **管理用途**：① 把全省汇总给销售总监 — 看 V2/V3 虚高有多严重；"
        "② 重点查「V4 被稀释」团伙 — 这部分本应是高级别服务商，因被拆账反而落到 V2/V3；"
        "③ 跟伞形合并方案配合：合并后会立即看到 V4 数量回升 + V2 数量下降。"
    )

st.divider()


# ── B5. 📋 低效服务商管理总表（5 类汇总 + 无效跑动 + 红包套取）──
st.markdown("#### 📋 低效服务商管理总表")
st.caption(
    "**5 类低效服务商**：🎭 已确认马甲 · ☂️ 伞形账号 · "
    "❌ 低效签约（签约 60+ 天但上线 ≤2 台）· 🔄 套上线（团伙内同址同步上线）· "
    "🚫 **明确无采购意向**（业务方人工标，最高置信）。"
    "**用途**：① 标记业务员对其的无效跑动 → 要求真实跑动；② 审计这些账号是否套取了大红包。"
)

# ── 1. 集合每类低效服务商 ──
fake_codes_by_type = {
    "🎭 马甲": set(vest_codes),
    "☂️ 伞形": set(),
    "❌ 低效签约": set(),
    "🔄 套上线": set(),
    "🚫 明确无采购意向": closed_provider_codes(),  # 业务方人工标注（wx123.xlsx）
}

# ☂️ 伞形：含马甲团伙的非马甲成员 + 全个人户团伙的非主账号成员（与 umbrella_rows 同口径）
for gr in group_rows:
    members = gr["_members"]
    member_info = []
    for code in members:
        nm = name_map.get(code, ("—", "—"))[0]
        member_info.append({
            "code": code, "name": nm, "is_company": _is_company(nm),
            "amt": rp_agg.get(code, {}).get("金额", 0),
        })
    companies = [m for m in member_info if m["is_company"]]
    main_code = (max(companies, key=lambda m: m["amt"]) if companies
                 else max(member_info, key=lambda m: m["amt"]))["code"]
    for code in members:
        if code != main_code and code not in vest_codes:
            fake_codes_by_type["☂️ 伞形"].add(code)

# ❌ 低效签约：签约 60+ 天但上线 ≤2 台
if not contract.empty and not redpack.empty:
    rp_anchor = pd.to_datetime(redpack["上线时间"], errors="coerce").max()
    if pd.notna(rp_anchor):
        cutoff = rp_anchor - pd.Timedelta(days=60)
        contract_with_dates = contract.copy()
        contract_with_dates["客户编码"] = contract_with_dates["客户编码"].astype(str).str.strip()
        contract_with_dates["_sign_date"] = pd.to_datetime(
            contract_with_dates.get("签约日期"), errors="coerce"
        )
        for _, r in contract_with_dates.iterrows():
            code = r["客户编码"]
            if pd.isna(r["_sign_date"]) or r["_sign_date"] > cutoff:
                continue
            n_台 = rp_agg.get(code, {}).get("台数", 0)
            if n_台 <= 2:
                fake_codes_by_type["❌ 低效签约"].add(code)

# 🔄 套上线：团伙内有 co_install 证据的成员
for gr in group_rows:
    members = gr["_members"]
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            key = (members[i], members[j]) if members[i] < members[j] else (members[j], members[i])
            ev = pair_evidence.get(key)
            if ev and ev.get("co_install", 0) >= 3:
                fake_codes_by_type["🔄 套上线"].add(members[i])
                fake_codes_by_type["🔄 套上线"].add(members[j])

# ── 2. 合并 4 类 → 低效服务商总集合 ──
all_fake_codes = set()
for s in fake_codes_by_type.values():
    all_fake_codes |= s

# ── 3. 主表：每个低效服务商的标签 + 业务员跑动 + 红包套取 ──
# 团伙 ID 反查
code_to_group_id = {}
for gr in group_rows:
    for c in gr["_members"]:
        code_to_group_id[c] = gr["团伙ID"]

# 业务员跑动统计（只对低效服务商成员）
fake_visit_stats = {}  # code -> {大华次数, 代理商次数, 总次数, 大华业务员名, 代理商业务员名}
if not visits.empty:
    v_fake = visits[visits["客户编码"].astype(str).str.strip().isin(all_fake_codes)].copy()
    if not v_fake.empty:
        v_fake["_code"] = v_fake["客户编码"].astype(str).str.strip()
        for code, sub in v_fake.groupby("_code"):
            dh_mask = sub["_打卡方"] == "🏢 大华"
            dl_mask = sub["_打卡方"] == "🏪 代理商"
            fake_visit_stats[code] = {
                "大华次数": int(dh_mask.sum()),
                "代理商次数": int(dl_mask.sum()),
                "总次数": len(sub),
                "大华业务员": ", ".join(sorted(set(sub.loc[dh_mask, "打卡人姓名"].dropna()))[:3]),
                "代理商业务员": ", ".join(sorted(set(sub.loc[dl_mask, "打卡人姓名"].dropna()))[:3]),
            }

# 主表行
fake_rows = []
for code in all_fake_codes:
    types = []
    for t, s in fake_codes_by_type.items():
        if code in s:
            types.append(t)
    nm, city = name_map.get(code, ("—", "—"))
    info = rp_agg.get(code, {})
    n_台 = info.get("台数", 0)
    amt_so = info.get("金额", 0)
    amt_red = info.get("红包", 0)
    visit_st = fake_visit_stats.get(code, {})

    # 签约代理商
    own_dealers = rp_dealers.get(code, set()) - {""}
    fake_rows.append({
        "客户编码": code,
        "公司名称": nm,
        "地市": city,
        "类型": " + ".join(types),
        "类型数": len(types),
        "团伙ID": code_to_group_id.get(code, "—"),
        "上线台数": int(n_台),
        "上线金额": int(amt_so),
        "🎁 中奖金额": int(amt_red),
        "总跑动次数": visit_st.get("总次数", 0),
        "🏢 大华跑动次数": visit_st.get("大华次数", 0),
        "🏪 代理商跑动次数": visit_st.get("代理商次数", 0),
        "大华业务员": visit_st.get("大华业务员", "—"),
        "代理商业务员": visit_st.get("代理商业务员", "—"),
        "签约代理商": ", ".join(sorted(own_dealers)[:2]) if own_dealers else "—",
    })

fake_df = pd.DataFrame(fake_rows)

# ── 4. 顶部 metric ──
n_total_fake = len(fake_df)
n_per_type = {t: len(s) for t, s in fake_codes_by_type.items()}
total_invalid_visits = int(fake_df["总跑动次数"].sum()) if not fake_df.empty else 0
total_red_at_risk = int(fake_df["🎁 中奖金额"].sum()) if not fake_df.empty else 0

st.markdown("##### 总览")
fk = st.columns(4)
fk[0].metric("低效服务商总数", f"{n_total_fake:,}",
              help="去重后 — 一个账号可同时命中多类")
fk[1].metric(
    "🚫 明确无采购意向 / 🎭 马甲",
    f"{n_per_type.get('🚫 明确无采购意向', 0)} / {n_per_type['🎭 马甲']}",
    help="明确无采购意向（人工标注，最高置信）+ 已确认马甲",
)
fk[2].metric(
    "无效跑动次数",
    f"{total_invalid_visits:,}",
    help="业务员对低效服务商的打卡总数 — 这些都是应该被叫停的「水卡」",
)
fk[3].metric(
    "🎁 红包套取总额",
    f"¥{total_red_at_risk:,.0f}",
    help="低效服务商累计获得的红包奖励 — 这部分公司营销资源被套",
)

# 警示
warning_parts = []
if total_invalid_visits > 0:
    warning_parts.append(
        f"📍 业务员对低效服务商累计打卡 **{total_invalid_visits:,}** 次 — 这是无效跑动，应叫停"
    )
if total_red_at_risk > 0:
    warning_parts.append(
        f"💸 低效服务商累计套取红包 **¥{total_red_at_risk:,.0f}** — 应审计"
    )
if warning_parts:
    st.error(" · ".join(warning_parts))

# ── 5. 多维筛选 ──
st.markdown("##### 低效服务商主表")
ff = st.columns([2, 1, 1, 2])
with ff[0]:
    fake_type_filter = st.multiselect(
        "类型筛选（多选）",
        options=["🎭 马甲", "☂️ 伞形", "❌ 低效签约", "🔄 套上线", "🚫 明确无采购意向"],
        default=["🎭 马甲", "☂️ 伞形", "❌ 低效签约", "🔄 套上线", "🚫 明确无采购意向"],
        key="fake_type_filter",
    )
with ff[1]:
    fake_min_red = st.number_input(
        "🎁 中奖金额 ≥",
        value=0, min_value=0, step=100,
        key="fake_min_red",
        help="只看套了红包的",
    )
with ff[2]:
    fake_min_visits = st.number_input(
        "总跑动次数 ≥",
        value=0, min_value=0, step=1,
        key="fake_min_visit",
        help="只看被打过卡的（无效跑动）",
    )
with ff[3]:
    fake_only_dual_visit = st.checkbox(
        "🚨 仅看「大华+代理商都跑过」",
        value=False, key="fake_dual",
        help="勾选 = 两端业务员都对其打卡 → 共谋嫌疑",
    )

show_fake = fake_df.copy()
if fake_type_filter:
    show_fake = show_fake[show_fake["类型"].apply(
        lambda t: any(x in str(t) for x in fake_type_filter)
    )]
if fake_min_red > 0:
    show_fake = show_fake[show_fake["🎁 中奖金额"] >= fake_min_red]
if fake_min_visits > 0:
    show_fake = show_fake[show_fake["总跑动次数"] >= fake_min_visits]
if fake_only_dual_visit:
    show_fake = show_fake[
        (show_fake["🏢 大华跑动次数"] > 0) & (show_fake["🏪 代理商跑动次数"] > 0)
    ]
show_fake = show_fake.sort_values(
    ["类型数", "🎁 中奖金额", "总跑动次数"], ascending=[False, False, False],
)

st.caption(f"筛选后：**{len(show_fake):,}** 个低效服务商（共 {len(fake_df):,}）")
st.dataframe(
    show_fake,
    use_container_width=True, hide_index=True,
    height=min(60 + len(show_fake) * 36, 500),
)

# ── 6. 业务员无效跑动汇总 ──
invalid_visit_rows = []
if not visits.empty:
    v_fake_all = visits[visits["客户编码"].astype(str).str.strip().isin(all_fake_codes)].copy()
    if not v_fake_all.empty:
        v_fake_all["_code"] = v_fake_all["客户编码"].astype(str).str.strip()
        # 业务员维度聚合
        for (姓名, 公司, 打卡方), sub in v_fake_all.groupby(["打卡人姓名", "打卡人所属公司", "_打卡方"], dropna=False):
            invalid_visit_rows.append({
                "身份": 打卡方,
                "打卡人姓名": 姓名,
                "打卡人所属公司": 公司 or "（大华内部）",
                "无效跑动低效服务商数": sub["_code"].nunique(),
                "无效打卡次数": len(sub),
                "覆盖类型": ", ".join(sorted(set(
                    t for code in sub["_code"].unique()
                    for t, s in fake_codes_by_type.items() if code in s
                ))),
            })
invalid_visit_df = pd.DataFrame(invalid_visit_rows)

if not invalid_visit_df.empty:
    st.markdown("##### 业务员无效跑动排行（应叫停）")
    invalid_visit_df = invalid_visit_df.sort_values(
        ["无效打卡次数", "无效跑动低效服务商数"], ascending=False,
    )
    n_dh = (invalid_visit_df["身份"] == "🏢 大华").sum()
    n_dl = (invalid_visit_df["身份"] == "🏪 代理商").sum()
    st.caption(
        f"共 **{len(invalid_visit_df):,}** 名业务员对低效服务商有打卡记录 · "
        f"🏢 大华 {n_dh} / 🏪 代理商 {n_dl}"
    )
    st.dataframe(
        invalid_visit_df.head(100),
        use_container_width=True, hide_index=True,
        height=min(60 + len(invalid_visit_df) * 36, 400),
    )

# ── 7. 红包套取嫌疑详细记录 ──
red_steal_rows = []
if not redpack.empty and all_fake_codes:
    rp_fake = redpack[
        (redpack["上线客户编码"].astype(str).str.strip().isin(all_fake_codes))
        & (pd.to_numeric(redpack["中奖金额"], errors="coerce") > 0)
    ].copy()
    if not rp_fake.empty:
        rp_fake["_code"] = rp_fake["上线客户编码"].astype(str).str.strip()
        rp_fake["_amt"] = pd.to_numeric(rp_fake["中奖金额"], errors="coerce").fillna(0)
        # 按服务商汇总
        for code, sub in rp_fake.groupby("_code"):
            types = [t for t, s in fake_codes_by_type.items() if code in s]
            red_steal_rows.append({
                "类型": " + ".join(types),
                "客户编码": code,
                "公司名称": name_map.get(code, ("—", "—"))[0],
                "中奖次数": len(sub),
                "中奖总额": int(sub["_amt"].sum()),
                "单笔最大": int(sub["_amt"].max()),
                "首次中奖": str(sub["上线时间"].min())[:10] if "上线时间" in sub.columns else "—",
                "末次中奖": str(sub["上线时间"].max())[:10] if "上线时间" in sub.columns else "—",
            })

red_steal_df = pd.DataFrame(red_steal_rows)

if not red_steal_df.empty:
    st.markdown("##### 🎁 红包套取嫌疑（低效服务商收到的红包）")
    red_steal_df = red_steal_df.sort_values("中奖总额", ascending=False)
    big_red = red_steal_df[red_steal_df["单笔最大"] >= 1000]
    st.caption(
        f"共 **{len(red_steal_df):,}** 个低效服务商被发过红包 · "
        f"其中 **{len(big_red):,}** 个收过 ≥¥1k 大红包 · "
        f"累计金额 **¥{red_steal_df['中奖总额'].sum():,.0f}**"
    )
    if len(big_red) > 0:
        st.error(
            f"🚨 **{len(big_red)} 个低效服务商收到过 ≥¥1k 单笔大红包** —— "
            f"营销资源高度可疑被套取，建议立即冻结返点 + 审计"
        )
    st.dataframe(
        red_steal_df.head(200),
        use_container_width=True, hide_index=True,
        height=min(60 + len(red_steal_df) * 36, 500),
    )

# ── 8. 一键导出（多 sheet）──
buf_fake = io.BytesIO()
with pd.ExcelWriter(buf_fake, engine="openpyxl") as w:
    fake_df.to_excel(w, sheet_name="低效服务商总表", index=False)
    show_fake.to_excel(w, sheet_name="筛选后", index=False)
    if not invalid_visit_df.empty:
        invalid_visit_df.to_excel(w, sheet_name="业务员无效跑动", index=False)
    if not red_steal_df.empty:
        red_steal_df.to_excel(w, sheet_name="红包套取嫌疑", index=False)
        big_red_export = red_steal_df[red_steal_df["单笔最大"] >= 1000]
        if not big_red_export.empty:
            big_red_export.to_excel(w, sheet_name="大红包嫌疑Top", index=False)

st.download_button(
    f"📥 一键导出低效服务商管理包（{len(fake_df):,} 个 · "
    f"🎭{n_per_type['🎭 马甲']} + ☂️{n_per_type['☂️ 伞形']} + "
    f"❌{n_per_type['❌ 低效签约']} + 🔄{n_per_type['🔄 套上线']} + "
    f"🚫{n_per_type.get('🚫 明确无采购意向', 0)} 明确无采购意向）",
    data=buf_fake.getvalue(),
    file_name=f"低效服务商管理包_{stamp_g}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="cliq_dl_fake_manage",
)

st.caption(
    "💡 **管理闭环**：① 这份名单交渠道部 → 标记为「无效服务商」，业务员对其打卡不计入 KPI；"
    "② 大红包嫌疑名单交财务/审计 → 追溯红包发放原因，必要时冻结后续返点；"
    "③ 业务员无效跑动排行交销售总监 → 整改或考核扣分；"
    "④ 这些假商列入「黑名单」，未来不再发放营销资源（红包/培训/物料）。"
)

st.divider()


# ── C. 团伙证据卡片（每组一张可展开） ──
st.markdown("#### 🔬 团伙证据卡片")

filtered_ids = set(group_df["团伙ID"]) if not group_df.empty else set()
filtered_rows = [g for g in group_rows if g["团伙ID"] in filtered_ids] or group_rows

# 🔍 搜索框：支持按团伙ID / 服务商名 / 客户编码模糊搜索
search_q = st.text_input(
    "🔍 搜索团伙",
    value="",
    placeholder="输入团伙ID（如 G005）/ 服务商名 / 客户编码 / 老板姓名 — 命中任一即匹配",
    key="cliq_search",
).strip()

if search_q:
    matched_rows = []
    q_lower = search_q.lower()
    # 预构建：搜索范围内涉及成员的老板信息（沙盘+签约表查一次，缓存到 dict）
    all_codes_in_groups = set()
    for gr in filtered_rows:
        all_codes_in_groups.update(gr["_members"])
    boss_search_idx = {}  # code -> "老板|电话|联系人" 拼接字符串
    if not profile.empty:
        p_search = profile[profile["客户编码"].astype(str).str.strip().isin(all_codes_in_groups)]
        for code, boss, phone in zip(
            p_search["客户编码"].astype(str).str.strip(),
            p_search["老板姓名"].fillna("").astype(str),
            p_search["老板电话"].fillna("").astype(str),
        ):
            boss_search_idx[code] = boss_search_idx.get(code, "") + f"|{boss}|{phone}"
    if not contract.empty:
        c_search = contract[contract["客户编码"].astype(str).str.strip().isin(all_codes_in_groups)]
        for code, contact, phone in zip(
            c_search["客户编码"].astype(str).str.strip(),
            c_search["联系人"].fillna("").astype(str),
            c_search["联系电话"].fillna("").astype(str),
        ):
            boss_search_idx[code] = boss_search_idx.get(code, "") + f"|{contact}|{phone}"

    for gr in filtered_rows:
        # 1. 团伙ID 直接匹配
        if q_lower in gr["团伙ID"].lower():
            matched_rows.append(gr)
            continue
        # 2. 客户编码 / 服务商名 / 老板姓名 / 电话
        hit = False
        for code in gr["_members"]:
            if q_lower in code.lower():
                hit = True
                break
            nm = name_map.get(code, ("", ""))[0]
            if nm and q_lower in nm.lower():
                hit = True
                break
            boss_str = boss_search_idx.get(code, "").lower()
            if boss_str and q_lower in boss_str:
                hit = True
                break
        if hit:
            matched_rows.append(gr)
    filtered_rows = matched_rows

ec_col = st.columns([1, 1, 3])
with ec_col[0]:
    card_top_n = st.number_input(
        "显示前 N 个团伙证据卡片",
        value=20, min_value=5, max_value=200,
        key="cliq_card_n",
        help="筛选后的团伙按置信度排序，取前 N 个生成详细证据卡片",
    )
with ec_col[1]:
    expand_all = st.checkbox(
        "默认全部展开", value=bool(search_q),
        key="cliq_expand_all",
        help="搜索时默认展开匹配到的卡片",
    )

if search_q:
    st.caption(
        f"🔍 搜索 **{search_q}** 命中 **{len(filtered_rows):,}** 个团伙，"
        f"下方展示前 {min(int(card_top_n), len(filtered_rows)):,} 个"
    )
else:
    st.caption(
        f"已筛选 **{len(filtered_rows):,}** 个团伙，下方展示前 {min(int(card_top_n), len(filtered_rows)):,} 个 · "
        f"想看更多请增加 N 或调整筛选条件 · 完整数据可上方导出 Excel"
    )

if not filtered_rows:
    st.warning(f"未找到匹配「{search_q}」的团伙。试试其他关键词，或检查上方筛选条件。")

# 预构建：每个团伙成员的「沙盘画像」+「签约画像」+「拜访人」字典（只对涉及的成员）
shown_rows = filtered_rows[:int(card_top_n)]
all_member_codes = set()
for gr in shown_rows:
    all_member_codes.update(gr["_members"])

profile_idx = {}
if not profile.empty:
    p_sub = profile[profile["客户编码"].astype(str).str.strip().isin(all_member_codes)]
    for _, r in p_sub.iterrows():
        profile_idx[str(r["客户编码"]).strip()] = {
            "公司名称": r.get("公司名称"),
            "老板姓名": r.get("老板姓名"),
            "老板电话": r.get("老板电话"),
            "地址": r.get("地址"),
            "地市": r.get("地市"),
            "区县": r.get("区县"),
            "客户所有者": r.get("客户所有者") if "客户所有者" in r else None,
        }

contract_idx = {}
if not contract.empty:
    c_sub = contract[contract["客户编码"].astype(str).str.strip().isin(all_member_codes)]
    for _, r in c_sub.iterrows():
        contract_idx[str(r["客户编码"]).strip()] = {
            "客户名称": r.get("客户名称"),
            "联系人": r.get("联系人"),
            "联系电话": r.get("联系电话"),
            "详细地址": r.get("详细地址"),
            "上级分销商名称": r.get("上级分销商名称"),
            "签约日期": r.get("签约日期"),
        }

# 每个团伙的拜访人 dict
visit_by_code_filtered = {}
if not visits.empty and all_member_codes:
    v_temp = visits[visits["客户编码"].astype(str).str.strip().isin(all_member_codes)][
        ["客户编码", "打卡人姓名", "打卡人所属公司"]
    ].copy()
    v_temp["_code"] = v_temp["客户编码"].astype(str).str.strip()
    v_temp["_打卡方"] = v_temp["打卡人所属公司"].fillna("").apply(
        lambda x: "🏢 大华" if str(x).strip() == "" else "🏪 代理商"
    )
    for code, sub in v_temp.groupby("_code"):
        visitor_set = []
        for _, r in sub.iterrows():
            if pd.notna(r["打卡人姓名"]):
                tag = r["_打卡方"]
                visitor_set.append(f'{r["打卡人姓名"]}({tag})')
        visit_by_code_filtered[code] = sorted(set(visitor_set))[:5]


# 渲染每个团伙的证据卡片
for gr in shown_rows:
    gid = gr["团伙ID"]
    members = gr["_members"]
    conf = gr["置信度"]
    title = (
        f"**{gid}** {conf} · {gr['成员数']} 人 · "
        f"上线 {gr['上线台数']:,} 台 / ¥{gr['上线金额']:,} · "
        f"判定：{gr['判定依据']}"
    )

    with st.expander(title, expanded=expand_all):
        # 团伙含马甲警示
        vest_in = gr.get("_vest_members") or set()
        if vest_in:
            st.error(
                f"🎭 **本团伙含 {len(vest_in)} 个已确认马甲账号**："
                f"{', '.join(name_map.get(c, ('—', '—'))[0] for c in sorted(vest_in))}。"
                f"与马甲直接关联的其他成员高度怀疑也是同一控制人。"
            )

        # ── 1. 成员清单（沙盘 / 签约画像）──
        st.markdown("##### 📋 成员清单（沙盘 + 签约画像 · 🎭 = 已确认马甲）")
        detail_rows = []
        for code in members:
            p = profile_idx.get(code, {})
            c = contract_idx.get(code, {})
            name = p.get("公司名称") or c.get("客户名称") or name_map.get(code, ("—", "—"))[0]
            # 马甲账号在公司名前加 🎭
            display_name = ("🎭 " if code in vest_codes else "") + str(name or "—")
            n_台 = amt = rp_amt = 0
            info = rp_agg.get(code)
            if info:
                n_台, amt, rp_amt = info["台数"], info["金额"], info["红包"]
            ds_set = rp_dealers.get(code)
            dealers_str = ", ".join(sorted((ds_set or set()) - {""})[:3])
            visitors = ", ".join(visit_by_code_filtered.get(code, []))
            detail_rows.append({
                "服务商": display_name,
                "客户编码": code,
                "老板/联系人": str(p.get("老板姓名") or c.get("联系人") or "—"),
                "电话": str(p.get("老板电话") or c.get("联系电话") or "—"),
                "沙盘地址": str(p.get("地址") or "—"),
                "签约地址": str(c.get("详细地址") or "—"),
                "地市/区县": f"{p.get('地市') or '—'} / {p.get('区县') or '—'}",
                "上级分销商(签约)": str(c.get("上级分销商名称") or "—"),
                "上线台数": int(n_台),
                "上线金额": int(amt),
                "红包金额": int(rp_amt),
                "实际签约代理商": dealers_str or "—",
                "打卡人": visitors or "—",
            })
        df_detail = pd.DataFrame(detail_rows)
        st.dataframe(
            df_detail,
            use_container_width=True, hide_index=True,
            height=min(40 + len(df_detail) * 36, 280),
        )

        # ── 2. 关联依据（pair-by-pair） ──
        st.markdown("##### 🔗 关联依据（成员两两之间的证据）")
        ev_lines = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j]) if members[i] < members[j] else (members[j], members[i])
                ev = pair_evidence.get(key)
                if not ev:
                    continue
                score = pair_score[key]
                bits = []
                if ev["addr_p"]:
                    bits.append(f"📍 沙盘同址：{list(ev['addr_p'])[0]}")
                if ev["addr_c"]:
                    bits.append(f"📋 签约同址：{list(ev['addr_c'])[0]}")
                if ev["phone"]:
                    bits.append(f"☎️ 同老板电话：{list(ev['phone'])[0]}")
                if ev.get("co_install", 0) > 0:
                    examples = ev.get("co_install_examples", [])
                    if examples:
                        d, gps = examples[0]
                        gps_short = str(gps)[:30] + ("…" if len(str(gps)) > 30 else "")
                        bits.append(
                            f"🎯 同址同步上线 {ev['co_install']} 次"
                            f"（例 {d} 在「{gps_short}」）⭐ 强信号"
                        )
                    else:
                        bits.append(f"🎯 同址同步上线 {ev['co_install']} 次 ⭐ 强信号")
                if ev["sync"] > 0:
                    bits.append(f"📦 同步上线 {ev['sync']} 次")
                ev_lines.append({
                    "服务商 A": _name(members[i]),
                    "服务商 B": _name(members[j]),
                    "综合评分": score,
                    "证据": " ｜ ".join(bits),
                })
        if ev_lines:
            ev_df = pd.DataFrame(ev_lines).sort_values("综合评分", ascending=False)
            st.dataframe(
                ev_df, use_container_width=True, hide_index=True,
                height=min(40 + len(ev_df) * 36, 240),
            )

        # ── 2.5 项目级同址同步上线明细（完整 GPS 地址） ──
        co_install_lines = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j]) if members[i] < members[j] else (members[j], members[i])
                ev = pair_evidence.get(key)
                if not ev:
                    continue
                examples = ev.get("co_install_examples", [])
                if not examples:
                    continue
                total = ev.get("co_install", 0)
                for t_win, gps in examples:
                    # t_win 是 30 分钟窗口的起始时间
                    co_install_lines.append({
                        "服务商 A": _name(members[i]),
                        "服务商 B": _name(members[j]),
                        "时间窗口（30 分钟）": str(t_win),
                        "项目 GPS 地址": str(gps),
                        "该对累计窗口数": total,
                    })
        if co_install_lines:
            st.markdown("##### 🎯 项目级同址同步上线明细（30 分钟窗口 + 完整 GPS 地址）")
            st.caption(
                "**铁证级证据**：多家服务商在 **同一 30 分钟窗口内** + **同一 GPS 地址** 批量激活设备。"
                "已排除「都来集散市场取货激活」假阳性 — 半小时内连续激活几乎只能是同一团队操作。"
            )
            co_df = pd.DataFrame(co_install_lines)
            co_df = co_df.sort_values(
                ["该对累计窗口数", "时间窗口（30 分钟）"],
                ascending=[False, False],
            )
            st.dataframe(
                co_df,
                use_container_width=True, hide_index=True,
                height=min(40 + len(co_df) * 36, 320),
                column_config={
                    "项目 GPS 地址": st.column_config.TextColumn(
                        "项目 GPS 地址", width="large"
                    ),
                },
            )

        # ── 3. 业务员"知情"识别（团伙内 ≥2 家有打卡 = 知情）──
        v_in_group = pd.DataFrame()
        if not visits.empty:
            v_in_group = visits[visits["客户编码"].astype(str).str.strip().isin(members)].copy()
        if not v_in_group.empty:
            visitor_agg = v_in_group.groupby([
                "打卡人姓名", "打卡人所属公司",
            ], dropna=False).agg(
                打卡服务商数=("客户编码", lambda x: x.nunique()),
                总打卡次数=("客户编码", "count"),
            ).reset_index()
            visitor_agg["身份"] = visitor_agg["打卡人所属公司"].fillna("").apply(
                lambda x: "🏢 大华" if str(x).strip() == "" else "🏪 代理商"
            )
            informed = visitor_agg[visitor_agg["打卡服务商数"] >= 2].copy()

            if not informed.empty:
                # 区分大华 vs 代理商
                dh = informed[informed["身份"] == "🏢 大华"]
                dl = informed[informed["身份"] == "🏪 代理商"]

                st.markdown("##### 🚨 知情业务员（团伙内对 ≥2 家成员打过卡）")

                # 双重知情警示
                if len(dh) > 0 and len(dl) > 0:
                    st.error(
                        f"🔴🔴 **双重知情**：本团伙同时被 **{len(dh)} 名大华业务员** 和 "
                        f"**{len(dl)} 名代理商业务员** 知情。"
                        f"两端业务员都「知道」这是关联服务商但都没上报 — **强烈作假嫌疑**！"
                    )
                elif len(dh) > 0:
                    st.warning(
                        f"⚠️ **{len(dh)} 名大华业务员**对本团伙 ≥2 家成员打过卡（知情者）"
                    )
                elif len(dl) > 0:
                    st.warning(
                        f"⚠️ **{len(dl)} 名代理商业务员**对本团伙 ≥2 家成员打过卡（知情者）"
                    )

                # 排序：大华先于代理商，再按服务商数+次数降序
                informed["_sort_身份"] = informed["身份"].map({"🏢 大华": 0, "🏪 代理商": 1})
                informed = informed.sort_values(
                    ["_sort_身份", "打卡服务商数", "总打卡次数"],
                    ascending=[True, False, False],
                )
                show_cols = ["身份", "打卡人姓名", "打卡人所属公司", "打卡服务商数", "总打卡次数"]
                st.dataframe(
                    informed[show_cols],
                    use_container_width=True, hide_index=True,
                    height=min(40 + len(informed) * 36, 240),
                )

        # ── 4. 行动建议 ──
        if conf == "🔴 高":
            st.error("💡 行动建议：**立即人工核查**。可能的处理：① 实地走访登记地址核对挂牌情况；② 把团伙合并成单一服务商账号；③ 收回多余账号的政策福利")
        elif conf == "🟡 中":
            st.warning("💡 行动建议：**纳入观察**。建议下次现场拜访时拍照记录办公场地，确认是否真有 N 家独立公司")
        else:
            st.info("💡 行动建议：**先排除市场聚集**。这类多为商圈/产业园同址 — 抽查 1~2 家若无其他关联证据可标记排除")


# ══════════════════════════════════════════════
# V2 → V3 衔接
# ══════════════════════════════════════════════
from _ai_handoff import render_ai_followup_button as _render_ai_followup_button  # noqa: E402

st.divider()
st.markdown('#### 💬 想就假商/团伙线索继续追问？')
st.caption('比如换地市、换时间窗、画时间序列、批量出名单 —— 标准报表答不出来的，跳到 V3 让 AI 写代码。')
_render_ai_followup_button(
    source_page='03·🕵️ 关联挖掘与假商治理',
    context_summary=(
        '用户刚浏览假商识别 + 关联挖掘结果\n'
        '4 类假商：🎭 马甲（vest_account 已确认）/ ☂️ 伞形（同法人/手机号关联多家） / '
        '❌ 低效签约（签约 60+ 天但上线 ≤ 2 台） / 🔄 套上线（短期货值暴增）\n'
        '关联维度：同手机号 / 同地址 / 共同业务员 / 共同代理商'
    ),
    suggested_followups=[
        '套上线疑似名单按地市分布，top 5 城市',
        '低效签约的服务商签约时长中位数？比正常服务商少多少',
        '马甲账号关联的代理商 top 10（按马甲数量排序）',
        '画一张「过去 6 个月每月新增疑似套上线服务商」的趋势图',
    ],
    key_suffix='page03_end',
)
