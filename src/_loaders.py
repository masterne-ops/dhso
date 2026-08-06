"""
共享数据加载模块（跨 page 共享缓存）
- 所有 page import 同一个加载函数 → @st.cache_data 缓存全局共用
- 第一次进入任意 page 加载一次，其他 page 直接复用，避免重复转圈
- 列集合是各 page 需要的并集（少量冗余换缓存命中）
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


# ══════════════════════════════════════════════
# Monkey-patch pandas: groupby 默认 observed=True
#
# 背景：pandas 2.x 在 category 列上 groupby 时默认 observed=False，
# 会算所有 category 集合的笛卡尔积（即使大多数组合数据里不存在）。
# 5 个 category 列 × 每列 100 类 → 100 亿组合 → 13 GB → OOM。
# pandas 3.0 默认改 True，我们提前生效。
# 用户显式传 observed=False 仍然生效（setdefault 不会覆盖）。
# ══════════════════════════════════════════════

_orig_df_groupby = pd.DataFrame.groupby
_orig_series_groupby = pd.Series.groupby


def _df_groupby_observed(self, *args, **kwargs):
    kwargs.setdefault('observed', True)
    return _orig_df_groupby(self, *args, **kwargs)


def _series_groupby_observed(self, *args, **kwargs):
    kwargs.setdefault('observed', True)
    return _orig_series_groupby(self, *args, **kwargs)


pd.DataFrame.groupby = _df_groupby_observed
pd.Series.groupby = _series_groupby_observed


# ══════════════════════════════════════════════
# Monkey-patch pandas: Categorical.fillna 自动加新类别
#
# 背景：内存优化把高重复字符串列转 category（5-50× 内存收益），
# 但 page 里大量 `df[col].fillna(其他 Series)` 这种写法，
# 当 fill 值不在 category 集合里时，pandas 抛
#   TypeError: Cannot setitem on a Categorical with a new category
# 此处包一层：检测到 Categorical 且 value 含新类别 → 自动 add_categories
# ══════════════════════════════════════════════

_orig_series_fillna = pd.Series.fillna


def _safe_series_fillna(self, value=None, *args, **kwargs):
    """fillna 前对 Categorical 自动扩展类别集合

    两个动作：
      1. 如果 value 也是 Categorical，转 object（避免「categories 必须 identical」错）
      2. 把 value 中的新类别 add 到 self.categories
    """
    try:
        if isinstance(self.dtype, pd.CategoricalDtype):
            # ① value 是 Categorical → 转 object（pandas 拒绝 cat→cat 直接 fillna）
            if isinstance(value, pd.Series) and isinstance(value.dtype, pd.CategoricalDtype):
                value = value.astype(object)

            # ② 把 value 里的新值加进 self.categories
            new_vals = set()
            if isinstance(value, pd.Series):
                new_vals.update(value.dropna().unique().tolist())
            elif isinstance(value, dict):
                new_vals.update(v for v in value.values() if v is not None and not pd.isna(v))
            elif value is not None and not (isinstance(value, float) and pd.isna(value)):
                new_vals.add(value)
            existing = set(self.cat.categories)
            to_add = [v for v in new_vals if v not in existing and not pd.isna(v)]
            if to_add:
                self = self.cat.add_categories(to_add)
    except Exception:
        # 任何意外都退回原 fillna —— 不要因 monkey-patch 把功能打挂
        pass
    return _orig_series_fillna(self, value, *args, **kwargs)


pd.Series.fillna = _safe_series_fillna


_orig_df_fillna = pd.DataFrame.fillna


def _safe_df_fillna(self, value=None, *args, **kwargs):
    """DataFrame.fillna — 智能处理 Categorical 列

    核心 trick：把 scalar `fillna(0)` 改写成 per-column dict，跳过
    类型不匹配且无需填充的 Categorical 列（pandas 即使列没 NaN 也会
    upfront validate 填充值的类型，所以必须改写 value）

    规则：
      ① Categorical 列没 NaN → 从 fillna 的对象里剔除（fillna 本来是 no-op）
      ② Categorical 列有 NaN + 类型一致 → add_categories
      ③ Categorical 列有 NaN + 类型不匹配 → 转 object 再填
    """
    try:
        cat_cols = [c for c in self.columns
                    if isinstance(self[c].dtype, pd.CategoricalDtype)]
        if not cat_cols:
            return _orig_df_fillna(self, value, *args, **kwargs)

        # 决定 scalar 还是 dict，方便 per-column 改写
        if isinstance(value, pd.DataFrame):
            # 太复杂，直接交还原 pandas
            return _orig_df_fillna(self, value, *args, **kwargs)

        if isinstance(value, dict):
            scalar_mode = False
            per_col = dict(value)
        elif value is None or (isinstance(value, float) and pd.isna(value)):
            return _orig_df_fillna(self, value, *args, **kwargs)
        else:
            scalar_mode = True
            per_col = {c: value for c in self.columns}

        self = self.copy()
        for c in list(per_col.keys()):
            if c not in cat_cols:
                continue
            ser = self[c]
            v = per_col[c]
            if pd.isna(v):
                continue
            existing_cats = list(ser.cat.categories)
            in_cats = v in set(existing_cats)
            has_nan = ser.isna().any()

            # ① 无 NaN → 这列 fillna 本来就 no-op，从 dict 移除（避免 type 校验报错）
            if not has_nan:
                del per_col[c]
                continue
            # 已经在 categories 里 → 直接走 pandas
            if in_cats:
                continue
            # ③ 类型不匹配 → 转 object，并把 v 强转为列原类型（避免 str+int 混合）
            if existing_cats and not isinstance(v, type(existing_cats[0])):
                target_type = type(existing_cats[0])
                try:
                    coerced_v = target_type(v)
                    self[c] = ser.astype(object)
                    per_col[c] = coerced_v       # 后续用强转的值填
                except Exception:
                    self[c] = ser.astype(object)
                continue
            # ② 类型一致 → 加 category
            self[c] = ser.cat.add_categories([v])

        # scalar_mode：如果 dict 还非空就传 dict，否则传 None（全跳过）
        if scalar_mode and not per_col:
            return self  # 全跳过，直接返回 copy
        new_value = per_col if (per_col != value) else value
        return _orig_df_fillna(self, new_value, *args, **kwargs)
    except Exception:
        pass
    return _orig_df_fillna(self, value, *args, **kwargs)


pd.DataFrame.fillna = _safe_df_fillna


# ══════════════════════════════════════════════
# Monkey-patch pandas: Categorical Series + str → 自动转 object
#
# 背景：page 里大量 `df['cat列'] + ' | ' + df['cat列']` 这种字符串拼接，
# 但 Categorical 列被禁止做算术运算，pandas 抛
#   TypeError: Object with dtype category cannot perform the numpy op add
# 包一层 __add__ / __radd__：Categorical + 字符串/object 时自动转 object 再加
# ══════════════════════════════════════════════

_orig_series_add = pd.Series.__add__
_orig_series_radd = pd.Series.__radd__


def _safe_series_add(self, other):
    """Categorical Series 的 + 操作：self 或 other 是 Categorical 都自动转 object"""
    try:
        self_is_cat = isinstance(self.dtype, pd.CategoricalDtype)
        other_is_cat = isinstance(other, pd.Series) and isinstance(other.dtype, pd.CategoricalDtype)
        if self_is_cat or other_is_cat:
            l = self.astype(object) if self_is_cat else self
            r = other.astype(object) if other_is_cat else other
            return _orig_series_add(l, r)
    except Exception:
        pass
    return _orig_series_add(self, other)


def _safe_series_radd(self, other):
    try:
        self_is_cat = isinstance(self.dtype, pd.CategoricalDtype)
        other_is_cat = isinstance(other, pd.Series) and isinstance(other.dtype, pd.CategoricalDtype)
        if self_is_cat or other_is_cat:
            l = self.astype(object) if self_is_cat else self
            r = other.astype(object) if other_is_cat else other
            return _orig_series_radd(l, r)
    except Exception:
        pass
    return _orig_series_radd(self, other)


pd.Series.__add__ = _safe_series_add
pd.Series.__radd__ = _safe_series_radd


BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db" / "product_flow.db"


def _fast_connect(path=None):
    """带 PRAGMA 优化的 SQLite 连接，比默认 read_sql 快 2-3 倍

    - cache_size=-200000：用 200MB 页缓存（默认 ~2MB）
    - mmap_size=1GB：mmap 大文件，避免 read syscall
    - temp_store=MEMORY：临时表/排序放内存
    """
    import sqlite3 as _sq
    conn = _sq.connect(str(path or DB_PATH))
    conn.execute("PRAGMA cache_size = -200000")        # 200 MB 页缓存
    conn.execute("PRAGMA mmap_size = 1073741824")      # 1 GB mmap
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn

# 主表 — 各 page 用到的列并集
MAIN_COLS = [
    'ID', '产品序列号',
    '出库客户名称', '出货客户城市',
    '上线区县', '上线城市', '上线时间', '出库时间',
    '最新分销价',
    '上线自客户名称', '所属一级客户',
    '产品系列', '产品子系列', '产品子系列-新', '内部型号',
    '三大重点专项', '安装红包是否发放抽奖机会',
    '数据剔除', '客户行为异常',
    '是否异省', '是否异城', '是否异县',
]

# 红包表 — 各 page 用到的列并集
REDPACK_COLS = [
    '产品序列号', '上线时间',
    '上线客户编码', '上线客户名称', '上线客户地市', '上线客户区县',
    '所属一级客户',
    '出货客户名称', '出货客户城市',
    '安装城市', '安装区县', '安装省份',
    '安装GPS详细地址',
    '产品现有分销价',
    '产品系列', '产品子系列', '产品子系列-新', '内部型号',
    '是否异省',
    '是否抽奖', '中奖金额', '抽奖机会发放时间',
]

# 服务商管理沙盘 — 列名带换行的已在 import 时被替换为空格
PROFILE_COLS = [
    '公司名称', '客户编码', '外部客户名称',
    '渠道客户类型', '分销商认证', '协议类型',
    '客户所有者', '客户所有者工号', '客户所有者部门',
    '责任分销经理工号', '责任分销经理',
    '客户状态',
    '省份', '地市', '区县', '乡镇', '地址',
    '老板姓名', '老板电话', '老板类型', '对大华品牌认可度',
    '老板年龄', '老板生日（非必填）',
    '签约层级', '上级客户名称', '上级分销商名称',
    '客户分类', '经营类型占比', '业务类型',
    '年安防采购量', '是否HKTOP竞品服务商',
    '主营品牌', '主营品牌安防产品 销售额',
    '主营摄像机产品类型', '监控主推品牌 （单选）',
    '线下店铺类型', '店铺门头品牌', '服务用户类型',
    '上游供货渠道',
    '公司总人数', '安防销售人员数量', '技术人员总数',
    '仓储面积（㎡）', '仓库地址',
]


def _select_clause(cols: list) -> str:
    return ", ".join('"' + c + '"' for c in cols)


# ──────────────────────────────────────────
# Scope 自动注入（白名单）— 全部 load_*_shared 调用都自动过滤
#
# 原则：
#   - 原始加载用 @st.cache_data（全局 cache，所有用户共享内存）
#   - 公开的 load_xxx_shared() 函数在返回前应用当前用户 scope
#   - admin/manager 跳过过滤
#   - 未登录（开发模式）跳过过滤
# ──────────────────────────────────────────


def _apply_scope_filter(df, *, city_col=None, district_col=None,
                        salesperson_col=None, dealer_col=None):
    """统一 scope 过滤入口。空 scope 或 admin → 原样返回"""
    if df is None or df.empty:
        return df
    try:
        from _auth import is_admin, get_current_scope, current_user
        if not current_user() or is_admin():
            return df
        scope = get_current_scope()
        if not scope:
            return df
        if city_col and city_col in df.columns and scope.get('city'):
            df = df[df[city_col].isin(scope['city'])]
        if district_col and district_col in df.columns and scope.get('district'):
            df = df[df[district_col].isin(scope['district'])]
        if salesperson_col and salesperson_col in df.columns and scope.get('salesperson'):
            df = df[df[salesperson_col].isin(scope['salesperson'])]
        if dealer_col and dealer_col in df.columns and scope.get('dealer'):
            df = df[df[dealer_col].isin(scope['dealer'])]
    except Exception:
        pass
    return df


def scoped_cities(all_cities: list) -> list:
    """城市下拉框的便捷过滤器：把全城市列表收敛到用户可见的"""
    try:
        from _auth import is_admin, get_current_scope, current_user
        if not current_user() or is_admin():
            return all_cities
        scope = get_current_scope()
        if scope.get('city'):
            return [c for c in all_cities if c in scope['city']]
    except Exception:
        pass
    return all_cities


# ──────────────────────────────────────────
# 内存优化工具
# 高重复字符串列转 category（内存降 5-50×），数值列 downcast
# 在每个 loader 派生 + 字符串处理完后调用
# ──────────────────────────────────────────

# 给 category 列预先加这些"安全" fill 值，避免 page 里
# `df[col].fillna('其他')` `df[col].replace('', pd.NA)` 这类防御性代码炸
# (TypeError: Cannot setitem on a Categorical with a new category)
# 来源：grep 全 page 里所有 fillna(string) / replace(string) 的值
_SAFE_CATEGORY_FILLS = [
    '',           # 最常见
    '其他',
    '未知', '未知城市', '未知区县', '（未知）',
    '(空)', '空',
    '—', '-', '无',
    'N/A', 'nan',
    'v0未激活',
]


def _safe_categorize(s: pd.Series) -> pd.Series:
    """转 category，并把常见 fill 值加进 category 集合。

    page 里大量 `df[col].fillna('').astype(str).str.strip()` 这类代码，
    如果 '' 不在 category 集合里，pandas 拒绝 setitem。预加进去就 OK。
    """
    if isinstance(s.dtype, pd.CategoricalDtype):
        cat = s
    else:
        cat = s.astype('category')

    existing = set(cat.cat.categories)
    to_add = [v for v in _SAFE_CATEGORY_FILLS if v not in existing]
    if to_add:
        cat = cat.cat.add_categories(to_add)
    return cat


def _optimize_dtypes(df: pd.DataFrame,
                     categorize: list = None,
                     to_float32: list = None,
                     to_int16: list = None) -> pd.DataFrame:
    """统一 dtype 优化。

    Args:
        categorize: 转 category 的列（高重复字符串）
        to_float32: 转 float32 的列（金额、距离等数值，2× 节省）
        to_int16: 转 Int16 的列（年/月等小整数，4× 节省）
    """
    if categorize:
        for c in categorize:
            if c in df.columns and not isinstance(df[c].dtype, pd.CategoricalDtype):
                df[c] = _safe_categorize(df[c])
    if to_float32:
        for c in to_float32:
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                df[c] = df[c].astype('float32')
    if to_int16:
        for c in to_int16:
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                # 用 nullable Int16（保留 NaN 语义）
                df[c] = df[c].astype('Int16')
    return df


@st.cache_data(ttl=3600, show_spinner="正在加载主表（一次加载，多页面复用）…")
def load_main_shared() -> pd.DataFrame:
    """从 product_flow_v 视图读 — 派生字段（KPI金额/产品系列_有效/上线区县_全/流通天数/上线年月）已在 SQL 层算好

    视图不存在时（旧 DB / 视图重建失败）fallback 到原表 + Python 派生（保持兼容）
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        # 优先视图，没有就 fallback 到原表
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('view','table') AND name IN ('product_flow_v','product_flow')"
        )
        names = {r[0] for r in cur.fetchall()}
        if 'product_flow_v' in names:
            source = 'product_flow_v'
            extended_cols = MAIN_COLS + [
                '上线年份', '上线月份', '上线年月', '上线日期',
                '流通天数', '产品系列_有效', '上线区县_全', 'KPI金额',
            ]
        elif 'product_flow' in names:
            source = 'product_flow'
            extended_cols = MAIN_COLS
        else:
            return pd.DataFrame()

        cur.execute(f'PRAGMA table_info("{source}")')
        db_cols = {r[1] for r in cur.fetchall()}
        if not db_cols:
            return pd.DataFrame()
        cols = [c for c in extended_cols if c in db_cols]
        df = pd.read_sql(f'SELECT {_select_clause(cols)} FROM "{source}"', conn)
    finally:
        conn.close()

    # 补齐缺失列（保持下游兼容）
    for c in MAIN_COLS:
        if c not in df.columns:
            df[c] = None

    # 时间字段：视图返回的字符串，转 datetime
    df['上线时间'] = pd.to_datetime(df['上线时间'], errors='coerce')
    df['出库时间'] = pd.to_datetime(df['出库时间'], errors='coerce')

    # 视图缺啥就 fallback 在 Python 端补，确保下游 page 永远看到这些列
    if '上线年份' not in df.columns or df['上线年份'].isna().all():
        df['上线年份'] = df['上线时间'].dt.year
    if '上线月份' not in df.columns or df['上线月份'].isna().all():
        df['上线月份'] = df['上线时间'].dt.month
    if '上线年月' not in df.columns:
        df['上线年月'] = df['上线时间'].dt.to_period('M').astype(str)
    if '上线日期' not in df.columns:
        df['上线日期'] = df['上线时间'].dt.date
    if '流通天数' not in df.columns:
        raw_days = (df['上线时间'] - df['出库时间']).dt.days
        df['流通天数'] = raw_days.where((raw_days >= 0) & (raw_days <= 730))
    if '产品系列_有效' not in df.columns:
        df['产品系列_有效'] = (
            df['产品子系列-新'].fillna(df['产品子系列']).fillna(df['产品系列'])
        )
    if '上线区县_全' not in df.columns:
        city = df['上线城市'].fillna('未知城市').astype(str)
        dist = df['上线区县'].fillna('未知区县').astype(str)
        df['上线区县_全'] = city + ' / ' + dist

    # 数值列规范
    df['最新分销价'] = pd.to_numeric(df['最新分销价'], errors='coerce').fillna(0)
    if 'KPI金额' in df.columns:
        df['KPI金额'] = pd.to_numeric(df['KPI金额'], errors='coerce').fillna(0)
    else:
        # 视图缺 KPI金额 → 在 Python 端补 fallback（旧逻辑保留）
        try:
            conn2 = _fast_connect()
            rp_amt = pd.read_sql(
                'SELECT 产品序列号, "产品现有分销价" FROM install_redpack', conn2,
            )
            conn2.close()
            rp_amt['产品现有分销价'] = pd.to_numeric(
                rp_amt['产品现有分销价'], errors='coerce'
            )
            rp_amt = rp_amt.dropna(subset=['产品序列号']).drop_duplicates(
                subset='产品序列号', keep='first'
            )
            df = df.merge(
                rp_amt.rename(columns={'产品现有分销价': '_产品现有分销价'}),
                on='产品序列号', how='left',
            )
            df['KPI金额'] = df['_产品现有分销价'].fillna(df['最新分销价'])
            df = df.drop(columns=['_产品现有分销价'])
        except Exception:
            df['KPI金额'] = df['最新分销价']

    # 内存优化：高重复字符串转 category，数值列 downcast
    df = _optimize_dtypes(
        df,
        categorize=[
            # 客户/代理商/服务商名（几百~几千枚举）
            '出库客户名称', '上线自客户名称', '所属一级客户',
            # 地理（几十~几百枚举）
            '出货客户城市', '上线城市', '上线区县', '上线区县_全',
            # 产品分类（几十枚举）
            '产品系列', '产品子系列', '产品子系列-新', '产品系列_有效', '内部型号',
            # 标签/枚举（个位数枚举）
            '三大重点专项', '安装红包是否发放抽奖机会',
            '数据剔除', '客户行为异常',
            '是否异省', '是否异城', '是否异县',
            # 派生年月（≤ 30 个值）
            '上线年月',
        ],
        to_float32=['最新分销价', 'KPI金额', '流通天数'],
        to_int16=['上线年份', '上线月份'],
    )

    return df


@st.cache_data(ttl=3600, show_spinner="正在加载安装红包表（一次加载，多页面复用）…")
def load_redpack_shared() -> pd.DataFrame:
    """从 install_redpack_v 视图读 — 派生字段（是否签约/签约状态/安装区县_全/上线年月）已在 SQL 层算好"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('view','table') AND name IN ('install_redpack_v','install_redpack')"
        )
        names = {r[0] for r in cur.fetchall()}
        if 'install_redpack_v' in names:
            source = 'install_redpack_v'
            extended_cols = REDPACK_COLS + [
                '上线年份', '上线月份', '上线年月', '上线日期',
                '是否签约', '签约状态', '安装区县_全',
            ]
        elif 'install_redpack' in names:
            source = 'install_redpack'
            extended_cols = REDPACK_COLS
        else:
            return pd.DataFrame()

        cur.execute(f'PRAGMA table_info("{source}")')
        db_cols = {r[1] for r in cur.fetchall()}
        cols = [c for c in extended_cols if c in db_cols]
        df = pd.read_sql(
            f'SELECT {_select_clause(cols)} FROM "{source}"', conn
        )
    finally:
        conn.close()

    # 补齐缺失列
    for c in REDPACK_COLS:
        if c not in df.columns:
            df[c] = None

    # 时间转换
    df['上线时间'] = pd.to_datetime(df['上线时间'], errors='coerce')
    # 抽奖机会发放时间 原始是 object（字符串/datetime 混存）→ 转 datetime64 省 25 MB
    if '抽奖机会发放时间' in df.columns:
        df['抽奖机会发放时间'] = pd.to_datetime(df['抽奖机会发放时间'], errors='coerce')

    # 派生 fallback：视图缺啥 Python 端补
    if '上线年份' not in df.columns or df['上线年份'].isna().all():
        df['上线年份'] = df['上线时间'].dt.year
    if '上线月份' not in df.columns or df['上线月份'].isna().all():
        df['上线月份'] = df['上线时间'].dt.month
    if '上线年月' not in df.columns:
        df['上线年月'] = df['上线时间'].dt.to_period('M').astype(str)
    if '上线日期' not in df.columns:
        df['上线日期'] = df['上线时间'].dt.date

    df['产品现有分销价'] = pd.to_numeric(df['产品现有分销价'], errors='coerce').fillna(0)
    if '中奖金额' in df.columns:
        df['中奖金额'] = pd.to_numeric(df['中奖金额'], errors='coerce').fillna(0)

    # 视图返回的「是否签约」是 0/1（SQLite 没 BOOL）→ 转 bool 保持下游兼容
    if '是否签约' in df.columns:
        df['是否签约'] = df['是否签约'].astype(bool)
    else:
        # fallback：Python 端算
        def _normalize_dealer_name(s):
            return s.fillna('').astype(str).str.replace(r'[\s　]+', '', regex=True)
        out_dealer = _normalize_dealer_name(df['出货客户名称'])
        sign_dealer = _normalize_dealer_name(df['所属一级客户'])
        df['是否签约'] = (out_dealer == sign_dealer) & (sign_dealer != '')

    if '签约状态' not in df.columns:
        # fallback：Python 端算
        df['签约状态'] = '无签约'
        has_sign = df['所属一级客户'].fillna('').astype(str).str.strip() != ''
        df.loc[has_sign & df['是否签约'], '签约状态'] = '签约采购'
        df.loc[has_sign & ~df['是否签约'], '签约状态'] = '跨渠道采购'

    if '安装区县_全' not in df.columns:
        city = df['安装城市'].fillna('未知城市').astype(str)
        dist = df['安装区县'].fillna('未知区县').astype(str)
        df['安装区县_全'] = city + ' / ' + dist

    # 内存优化：red_pack 是最大的表（422k 行），收益最大
    df = _optimize_dtypes(
        df,
        categorize=[
            # 服务商（几千枚举）— 红包表里复读 N 次（一个服务商对应几十-几百个序列号）
            '上线客户编码', '上线客户名称',
            # 代理商（几百枚举）
            '所属一级客户', '出货客户名称',
            # 地理（几十-几百枚举）
            '上线客户地市', '上线客户区县',
            '出货客户城市', '安装城市', '安装区县', '安装省份',
            '安装区县_全',
            # 产品分类（几十枚举）
            '产品系列', '产品子系列', '产品子系列-新', '内部型号',
            # 标签/枚举
            '是否异省', '是否抽奖',
            # 派生
            '签约状态', '上线年月',
        ],
        to_float32=['产品现有分销价', '中奖金额'],
        to_int16=['上线年份', '上线月份'],
    )

    return df


@st.cache_data(ttl=3600, show_spinner="正在加载服务商沙盘（一次加载，多页面复用）…")
def load_profile_shared() -> pd.DataFrame:
    """加载服务商管理沙盘表（静态画像，无时间维度）。
    可能信息不全，调用方应做好缺失值兜底。
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_profile'"
        )
        if cur.fetchone() is None:
            return pd.DataFrame()
        cur.execute('PRAGMA table_info("provider_profile")')
        db_cols = {r[1] for r in cur.fetchall()}
        cols = [c for c in PROFILE_COLS if c in db_cols]
        if not cols:
            return pd.DataFrame()
        df = pd.read_sql(
            f'SELECT {_select_clause(cols)} FROM "provider_profile"', conn
        )
    finally:
        conn.close()

    # 补齐缺失列（保持下游兼容）
    for c in PROFILE_COLS:
        if c not in df.columns:
            df[c] = None

    # 客户编码转字符串（与红包表的「上线客户编码」可关联）
    if '客户编码' in df.columns:
        df['客户编码'] = df['客户编码'].astype(str).str.strip()

    # 内存优化（profile 表小，但有些字段重复度极高）
    df = _optimize_dtypes(
        df,
        categorize=[
            '渠道客户类型', '分销商认证', '协议类型',
            '客户所有者', '客户所有者部门', '客户所有者工号',
            '责任分销经理', '责任分销经理工号',
            '客户状态',
            '省份', '地市', '区县', '乡镇',
            '老板类型', '对大华品牌认可度',
            '签约层级', '上级客户名称', '上级分销商名称',
            '客户分类', '业务类型',
            '是否HKTOP竞品服务商',
            '主营品牌', '主营摄像机产品类型', '监控主推品牌 （单选）',
            '线下店铺类型', '店铺门头品牌', '服务用户类型',
            '上游供货渠道',
        ],
    )

    return df


# 服务商签约明细 — 只列核心字段（跑动/采购指标用红包表更全，这里不读）
CONTRACT_COLS = [
    '签约日期',
    '客户编码', '客户名称',
    '客户省区', '客户城市', '客户区县',
    '客户分类',
    '年安防采购量 （年采购视频类产品金额）',
    '是否竞品TOP服务商（安防体量≥20W）',
    '服务用户类型',
    '签约层级', '上级客户名称', '上级分销商名称',
    '渠道客户类型', '协议类型', '分销商认证',
    '服务商等级',
    '是否新签', '是否激活', '激活时间',
    '是否复购', '复购时间',
    '详细地址',
    '联系人', '联系人职位', '联系电话',
    '客户所有者', '所有者工号', '所有者部门',
    '分销商业务员姓名', '分销商业务员手机号',
    '分销商业务员云商账号',
    '启信宝匹配结果', '经营状态',
]


@st.cache_data(ttl=3600, show_spinner="正在加载服务商签约明细（一次加载，多页面复用）…")
def load_contract_shared() -> pd.DataFrame:
    """加载服务商签约明细表。
    只读核心字段（基础信息 + 签约状态），跑动/采购统计指标用红包表算更准，不在此读取。
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_contract'"
        )
        if cur.fetchone() is None:
            return pd.DataFrame()
        cur.execute('PRAGMA table_info("provider_contract")')
        db_cols = {r[1] for r in cur.fetchall()}
        cols = [c for c in CONTRACT_COLS if c in db_cols]
        if not cols:
            return pd.DataFrame()
        df = pd.read_sql(
            f'SELECT {_select_clause(cols)} FROM "provider_contract"', conn
        )
    finally:
        conn.close()

    # 补齐缺失列
    for c in CONTRACT_COLS:
        if c not in df.columns:
            df[c] = None

    # 时间字段解析
    for c in ('签约日期', '激活时间', '复购时间'):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors='coerce')

    # 客户编码字符串化（与红包表的「上线客户编码」可关联）
    if '客户编码' in df.columns:
        df['客户编码'] = df['客户编码'].astype(str).str.strip()

    # ⚠️ 用统一标准（红包累计金额）覆盖「服务商等级」字段
    # 原值保留为「服务商等级_原始」便于对比
    df = _apply_tier_to_df(df, code_col='客户编码')

    # 内存优化（contract 表 9k 行，收益小但顺手做）
    df = _optimize_dtypes(
        df,
        categorize=[
            '客户编码',
            '客户省区', '客户城市', '客户区县',
            '客户分类', '服务用户类型',
            '签约层级', '上级客户名称', '上级分销商名称',
            '渠道客户类型', '协议类型', '分销商认证',
            '服务商等级', '服务商等级_原始',
            '是否新签', '是否激活', '是否复购',
            '是否竞品TOP服务商（安防体量≥20W）',
            '联系人职位',
            '客户所有者', '所有者工号', '所有者部门',
            '分销商业务员姓名', '分销商业务员手机号',
            '启信宝匹配结果', '经营状态',
        ],
    )

    return df


# 签约表 → 沙盘命名的字段映射（合并视图统一字段名）
_CONTRACT_TO_PROFILE_RENAMES = {
    '客户名称': '公司名称',
    '客户省区': '省份',
    '客户城市': '地市',
    '客户区县': '区县',
    '详细地址': '地址',
    '所有者工号': '客户所有者工号',
    '所有者部门': '客户所有者部门',
}


@st.cache_data(ttl=3600, show_spinner="正在合并服务商主档（沙盘 + 签约）…")
def load_provider_master_shared() -> pd.DataFrame:
    """合并视图：沙盘 + 签约 → 一张主档表
    - 主键：客户编码
    - 同名字段冲突：沙盘优先，签约 fallback
    - 不同名同义字段：签约 rename 到沙盘命名（省份/地市/区县/地址等）
    - 加列 `_来源_沙盘` / `_来源_签约` 标记数据来源

    下游 page 用一个统一接口拿合并后的画像。
    """
    profile = load_profile_shared()
    contract = load_contract_shared()

    if profile.empty and contract.empty:
        return pd.DataFrame()

    # 签约表标准化字段名
    if not contract.empty:
        contract = contract.rename(columns={
            k: v for k, v in _CONTRACT_TO_PROFILE_RENAMES.items()
            if k in contract.columns
        })

    # 单表的情况
    if profile.empty:
        out = contract.copy()
        out['_来源_沙盘'] = False
        out['_来源_签约'] = True
        return out
    if contract.empty:
        out = profile.copy()
        out['_来源_沙盘'] = True
        out['_来源_签约'] = False
        return out

    # 两表都有 → outer join on 客户编码
    # suffixes=('', '_c') 让沙盘的同名列保持原名，签约的同名列加 _c
    # ⚠️ merge 前要把 category 列转成 object，否则两侧 categories 集合不同时 merge 会出问题
    profile_for_merge = profile.copy()
    contract_for_merge = contract.copy()
    for d in (profile_for_merge, contract_for_merge):
        for c in d.columns:
            if isinstance(d[c].dtype, pd.CategoricalDtype):
                d[c] = d[c].astype('object')

    merged = pd.merge(
        profile_for_merge, contract_for_merge,
        on='客户编码', how='outer',
        suffixes=('', '_c'),
    )

    # 同名冲突：沙盘 NaN 时用签约填，然后删掉 _c 后缀列
    # 此时两侧都是 object，fillna 无障碍
    for col in list(merged.columns):
        if col.endswith('_c') and col != '客户编码_c':
            base = col[:-2]
            if base in merged.columns:
                merged[base] = merged[base].fillna(merged[col])
                merged = merged.drop(columns=[col])

    # 来源标记
    profile_codes = set(profile['客户编码'].astype(str))
    contract_codes = set(contract['客户编码'].astype(str))
    merged['_来源_沙盘'] = merged['客户编码'].astype(str).isin(profile_codes)
    merged['_来源_签约'] = merged['客户编码'].astype(str).isin(contract_codes)

    # 内存优化：merge 完再统一 categorize（结果只 ~25k 行，开销低）
    # 取 profile + contract categorize 列的并集，凡是字符串列都试着转 category
    merged = _optimize_dtypes(
        merged,
        categorize=[
            '渠道客户类型', '分销商认证', '协议类型',
            '客户所有者', '客户所有者部门', '客户所有者工号',
            '客户状态', '省份', '地市', '区县',
            '客户分类', '业务类型',
            '签约层级', '上级客户名称', '上级分销商名称',
            '服务用户类型',
            '服务商等级', '服务商等级_原始',
            '是否新签', '是否激活', '是否复购',
            '联系人职位',
            '所有者工号', '所有者部门',
            '分销商业务员姓名', '分销商业务员手机号',
            '启信宝匹配结果', '经营状态',
            '主营品牌', '主营摄像机产品类型',
            '线下店铺类型', '店铺门头品牌',
            '上游供货渠道',
        ],
    )

    return merged


# 业务员跑动记录（打卡表）
VISIT_COLS = [
    '活动编号', '拜访类型',
    '活动创建时间', '拜访时间', '拜访活动提交时间',
    '客户编码', '拜访客户',
    '拜访客户省份', '拜访客户城市', '拜访客户区县',
    '渠道客户类型', '协议类型', '分销商认证', '服务商等级',
    '客户所有者', '所有者工号', '所有者部门',
    '打卡人工号/账号', '打卡人姓名', '打卡人所属省份',
    '打卡人手机号', '打卡人所属公司编码', '打卡人所属公司',
    '打卡人所属部门',
    '场景类型', '附件类型',
    '拜访对象', '拜访对象职位',
    '拜访目的', '达成结果', '后期计划', '问题风险_资源诉求',
    '打卡地点', '距离偏离_米',
    '打卡异常类型', '打卡异常描述',
]


@st.cache_data(ttl=3600, show_spinner="正在加载业务员跑动记录（一次加载，多页面复用）…")
def load_visit_shared() -> pd.DataFrame:
    """从 visit_record_v 视图读 — 派生字段（_打卡异常无效/_真异常打卡/_打卡方/拜访时间_修正/拜访年月）已在 SQL 层算好"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('view','table') AND name IN ('visit_record_v','visit_record')"
        )
        names = {r[0] for r in cur.fetchall()}
        if 'visit_record_v' in names:
            source = 'visit_record_v'
            extended_cols = VISIT_COLS + [
                '拜访年月', '拜访年份', '拜访日期', '拜访时间_修正',
                '_是否大华', '_打卡方', '_打卡异常无效', '_真异常打卡',
            ]
        elif 'visit_record' in names:
            source = 'visit_record'
            extended_cols = VISIT_COLS
        else:
            return pd.DataFrame()

        cur.execute(f'PRAGMA table_info("{source}")')
        db_cols = {r[1] for r in cur.fetchall()}
        cols = [c for c in extended_cols if c in db_cols]
        if not cols:
            return pd.DataFrame()
        df = pd.read_sql(
            f'SELECT {_select_clause(cols)} FROM "{source}"', conn
        )
    finally:
        conn.close()

    # 补齐缺失列
    for c in VISIT_COLS:
        if c not in df.columns:
            df[c] = None

    # 时间字段解析
    for c in ('拜访时间', '活动创建时间', '拜访活动提交时间', '拜访时间_修正'):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors='coerce')

    # 拜访时间修正：视图里返回 拜访时间_修正；保留兼容性把它写回 拜访时间
    if '拜访时间_修正' in df.columns:
        df['拜访时间_原始'] = df['拜访时间']
        df['拜访时间'] = df['拜访时间_修正'].fillna(df['拜访时间'])
    elif '活动创建时间' in df.columns:
        df['拜访时间_原始'] = df['拜访时间']
        df['拜访时间'] = df['活动创建时间'].fillna(df['拜访时间'])

    # 派生 fallback：视图缺啥 Python 补
    if '拜访年月' not in df.columns:
        df['拜访年月'] = df['拜访时间'].dt.to_period('M').astype(str)
    if '拜访年份' not in df.columns or df['拜访年份'].isna().all():
        df['拜访年份'] = df['拜访时间'].dt.year
    if '拜访日期' not in df.columns:
        df['拜访日期'] = df['拜访时间'].dt.date

    # 打卡人字段规范化（保持下游 groupby 不丢大华行）
    for col in ['打卡人所属公司', '打卡人手机号', '打卡人工号/账号', '打卡人姓名']:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    # _是否大华 / _打卡方：视图返回 0/1 + 字符串，转 bool 保持下游兼容
    if '_是否大华' in df.columns:
        df['_是否大华'] = df['_是否大华'].astype(bool)
    else:
        df['_是否大华'] = df['打卡人所属公司'] == ''
    if '_打卡方' not in df.columns:
        df['_打卡方'] = df['_是否大华'].map({True: '🏢 大华', False: '🏪 代理商'})

    # 客户编码字符串化
    if '客户编码' in df.columns:
        df['客户编码'] = df['客户编码'].astype(str).str.strip()

    # 距离偏离 → 数值
    if '距离偏离_米' in df.columns:
        df['距离偏离_米'] = pd.to_numeric(df['距离偏离_米'], errors='coerce')

    # _打卡异常无效 / _真异常打卡：视图返回 0/1 → 转 bool
    if '_打卡异常无效' in df.columns:
        df['_打卡异常无效'] = df['_打卡异常无效'].astype(bool)
    else:
        # fallback：Python 端算
        anomaly_type = df.get('打卡异常类型', pd.Series(dtype=str)).fillna('').astype(str).str.strip()
        invalid_anomaly = {
            '系统客户地址信息维护错误，后续更正',
            '系统客户地址信息维护错误,后续更正',
            '客户多地址办公',
            '系统客户地址信息维护错误',
        }
        df['_打卡异常无效'] = anomaly_type.isin(invalid_anomaly)

    if '_真异常打卡' in df.columns:
        df['_真异常打卡'] = df['_真异常打卡'].astype(bool)
    elif '距离偏离_米' in df.columns:
        df['_真异常打卡'] = (
            (df['距离偏离_米'] > 1000).fillna(False) & (~df['_打卡异常无效'])
        )

    # ⚠️ 用统一标准（红包累计）覆盖「服务商等级」字段
    # 原值保留为「服务商等级_原始」（拜访时的历史快照，不同时段会变）
    df = _apply_tier_to_df(df, code_col='客户编码')

    # 内存优化
    df = _optimize_dtypes(
        df,
        categorize=[
            # 客户（几千枚举）
            '客户编码', '拜访客户',
            # 地理
            '拜访客户省份', '拜访客户城市', '拜访客户区县',
            # 客户标签（枚举）
            '渠道客户类型', '协议类型', '分销商认证',
            '服务商等级', '服务商等级_原始',
            # 客户所有者
            '客户所有者', '所属者部门', '所有者工号',
            # 打卡人（业务员，几百-几千枚举）
            '打卡人姓名', '打卡人手机号', '打卡人工号/账号',
            '打卡人所属公司', '打卡人所属部门', '打卡人所属省份',
            '打卡人所属公司编码',
            # 拜访属性（枚举）
            '拜访类型', '场景类型', '附件类型',
            '拜访目的', '达成结果',
            '打卡异常类型',
            # 派生
            '拜访年月', '_打卡方',
        ],
        to_float32=['距离偏离_米'],
        to_int16=['拜访年份'],
    )

    return df


# 马甲账号名单（已确认的代理商自有账号）
VEST_COLS = [
    '城市', '服务商客户编码', '服务商客户名称', '对应一级',
]


@st.cache_data(ttl=3600, show_spinner="正在加载马甲账号名单…")
def load_vest_shared() -> pd.DataFrame:
    """加载马甲账号名单（已确认的代理商自有服务商）。
    用于：找出与马甲账号关联的"嫌疑团伙"成员。
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vest_account'"
        )
        if cur.fetchone() is None:
            return pd.DataFrame()
        cur.execute('PRAGMA table_info("vest_account")')
        db_cols = {r[1] for r in cur.fetchall()}
        cols = [c for c in VEST_COLS if c in db_cols]
        if not cols:
            return pd.DataFrame()
        df = pd.read_sql(
            f'SELECT {_select_clause(cols)} FROM "vest_account"', conn
        )
    finally:
        conn.close()
    for c in VEST_COLS:
        if c not in df.columns:
            df[c] = None
    if '服务商客户编码' in df.columns:
        df['服务商客户编码'] = df['服务商客户编码'].astype(str).str.strip()
    return df


# ──────────────────────────────────────────
# 服务商等级（基于上线金额累计 = 累计扫码激活的设备货值）
#
# 业务定义：服务商安装设备后扫二维码激活 → 该台货值（产品现有分销价）累加 → 决定等级
# 抽奖中奖金额是激活后的可选互动，不影响等级
#
# 标准（公司 KPI）：
#   v0未激活：累计上线金额 = 0
#   已激活  ：0 < 累计上线 < 1,000
#   v2服务商：1,000 ≤ 累计上线 < 10,000
#   v3服务商：10,000 ≤ 累计上线 < 30,000
#   v4服务商：累计上线 ≥ 30,000
# ──────────────────────────────────────────


def _kpi_tier_from_so(so_amount: float) -> str:
    """根据上线金额累计（产品现有分销价之和）返回标准等级名"""
    if so_amount >= 30000:
        return 'v4服务商'
    if so_amount >= 10000:
        return 'v3服务商'
    if so_amount >= 1000:
        return 'v2服务商'
    if so_amount > 0:
        return '已激活'
    return 'v0未激活'


@st.cache_data(ttl=3600, show_spinner="正在计算服务商等级（基于上线金额累计）…")
def load_provider_tier_shared() -> pd.DataFrame:
    """每个服务商一行：客户编码 / 上线累计 / 红包累计 / 服务商等级（KPI 口径）

    优先从 provider_tier_v 视图读（SQLite 直接聚合，省 Python 内存）；
    没有视图就 fallback 到 Python 聚合（保持兼容）。
    """
    if not DB_PATH.exists():
        return pd.DataFrame(columns=['客户编码', '上线累计', '红包累计', '服务商等级'])

    conn = _fast_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='provider_tier_v'"
        )
        if cur.fetchone():
            df = pd.read_sql(
                'SELECT "客户编码", "上线累计", "红包累计", "服务商等级" FROM provider_tier_v',
                conn,
            )
            df['客户编码'] = df['客户编码'].astype(str).str.strip()
            df['上线累计'] = pd.to_numeric(df['上线累计'], errors='coerce').fillna(0)
            df['红包累计'] = pd.to_numeric(df['红包累计'], errors='coerce').fillna(0)
            return df
    finally:
        conn.close()

    # fallback：旧 Python 路径
    rp = load_redpack_shared()
    if rp.empty:
        return pd.DataFrame(columns=['客户编码', '上线累计', '红包累计', '服务商等级'])
    df = rp[['上线客户编码', '中奖金额', '产品现有分销价']].copy()
    df['_code'] = df['上线客户编码'].astype(str).str.strip()
    df['_red'] = pd.to_numeric(df['中奖金额'], errors='coerce').fillna(0)
    df['_red'] = df['_red'].where(df['_red'] > 0, 0)
    df['_so'] = pd.to_numeric(df['产品现有分销价'], errors='coerce').fillna(0)
    agg = df.groupby('_code').agg(
        上线累计=('_so', 'sum'),
        红包累计=('_red', 'sum'),
    ).reset_index().rename(columns={'_code': '客户编码'})
    agg['服务商等级'] = agg['上线累计'].apply(_kpi_tier_from_so)
    return agg


# ──────────────────────────────────────────
# RFM 共享算法（避免在多页面重复实现）
# ──────────────────────────────────────────
#
# 设计原则：
#   1. MECE — 每个有红包记录的客户精确归一类
#   2. 业务贴合 — 每类对应一个明确的 next-action：维护 / 培育 / 救援 / 放弃 / 开发
#   3. 简洁 — 5 类（包括"未采购"）而不是 8 类
#
# 分类逻辑（默认阈值，可参数化）：
#   是否有红包记录？
#     ├─ 否 → ❓ 未采购客户 — 完全没有红包扫码历史（最高优先级开发）
#     └─ 是 → 按 R + (F OR M) 划分：
#         R ≤ 60 天 + (F ≥ 6 OR M ≥ 1万) → 🏆 核心活跃 — 重点保留 / 持续维护
#         R ≤ 60 天 + F < 6 AND M < 1万   → 🌱 培育客户 — 在买但量小，可拉单提频
#         R > 60 天 + (F ≥ 6 OR M ≥ 1万) → ⚠️ 流失风险 — 曾是好客户，紧急救援
#         R > 60 天 + F < 6 AND M < 1万   → 🪦 已流失 — 战略放弃 / 简化跟进
#
# 阈值选择：
#   R = 60 天：业务行业惯例（活跃/沉睡分界线，相当于 2 个月）
#   F = 6 次/12 月：≥ 2 个月一次稳定采购
#   M = 1 万元：相当于 V3 服务商等级（V3 = 累计 1-3 万）

# 默认阈值
DEFAULT_R_DAYS = 60
DEFAULT_F_COUNT = 6
DEFAULT_M_VALUE = 10000

# 类别顺序（从高优先级到低）
# 优先级排序业务依据：流失风险（高价值掉量了，紧急）> 培育客户（新人成长，长期）
RFM_CATEGORIES_ORDERED = [
    '🏆 核心活跃',     # 当前命脉 — 重点保留
    '⚠️ 流失风险',     # 紧急救援 — 比培育更急（曾经的好客户掉了）
    '🌱 培育客户',     # 长期培育 — 拉单提频
    '🪦 已流失',       # 战略放弃 / 简化跟进
    '❓ 未采购客户',   # 完全没红包记录的客户
]
RFM_HIGH_VALUE_CATS = {'🏆 核心活跃', '⚠️ 流失风险'}  # 业务上必须重视的两类
RFM_RECENT_CATS = {'🏆 核心活跃', '🌱 培育客户'}      # R 近的两类


def categorize_rfm(rfm_df: pd.DataFrame, *,
                    r_days: int = DEFAULT_R_DAYS,
                    f_count: int = DEFAULT_F_COUNT,
                    m_value: float = DEFAULT_M_VALUE) -> pd.DataFrame:
    """给 RFM 表新增「RFM类别」列（5 类 MECE）

    Returns: 原 df + ['RFM类别', '_is_recent', '_is_valuable']
    """
    out = rfm_df.copy()
    is_recent = out['R'] <= r_days
    is_valuable = (out['F'] >= f_count) | (out['M'] >= m_value)

    # 默认全部归 "🪦 已流失"，后面按条件覆盖
    out['RFM类别'] = '🪦 已流失'
    out.loc[is_recent & is_valuable, 'RFM类别'] = '🏆 核心活跃'
    out.loc[is_recent & ~is_valuable, 'RFM类别'] = '🌱 培育客户'
    out.loc[~is_recent & is_valuable, 'RFM类别'] = '⚠️ 流失风险'
    out['_is_recent'] = is_recent
    out['_is_valuable'] = is_valuable

    # 兼容性：保留 R 级 / F 级 / M 级 字段
    out['R级'] = is_recent.map({True: '高', False: '低'})
    out['F级'] = (out['F'] >= f_count).map({True: '高', False: '低'})
    out['M级'] = (out['M'] >= m_value).map({True: '高', False: '低'})

    return out


@st.cache_data(ttl=3600, show_spinner="正在计算 RFM…")
def _compute_rfm_shared_raw(window_months: int = 12,
                              r_days: int = DEFAULT_R_DAYS,
                              f_count: int = DEFAULT_F_COUNT,
                              m_value: float = DEFAULT_M_VALUE) -> pd.DataFrame:
    """🚫 内部用 — 全量未 scope 过滤的 RFM。所有用户共用缓存。

    包含 城市 / 区县 字段，供包装层 _apply_scope_filter 使用。
    """
    rp = _raw_load_redpack_shared()   # 用 raw（不带 scope）— 因为本函数自己也缓存，要保证全量
    if rp.empty:
        return pd.DataFrame()
    anchor = rp['上线时间'].max()
    if pd.isna(anchor):
        return pd.DataFrame()
    cutoff = anchor - pd.DateOffset(months=window_months)
    rp_w = rp[rp['上线时间'] >= cutoff]
    if rp_w.empty:
        return pd.DataFrame()

    def _first_non_null(series):
        s = series.dropna()
        if s.empty:
            return None
        m = s.mode()
        return m.iloc[0] if not m.empty else s.iloc[0]

    rfm = rp_w.groupby(['上线客户编码', '上线客户名称']).agg(
        最近上线=('上线时间', 'max'),
        F=('上线日期', 'nunique'),
        M=('产品现有分销价', 'sum'),
        签约代理商=('所属一级客户', _first_non_null),
        城市=('上线客户地市', _first_non_null),
        区县=('上线客户区县', _first_non_null),
    ).reset_index().rename(columns={'上线客户编码': '客户编码'})
    rfm['R'] = (anchor - rfm['最近上线']).dt.days
    rfm['M'] = rfm['M'].round(0).astype(float)
    rfm['客户编码'] = rfm['客户编码'].astype(str).str.strip()

    rfm = categorize_rfm(rfm, r_days=r_days, f_count=f_count, m_value=m_value)

    return rfm[[
        '客户编码', '上线客户名称', '签约代理商',
        '城市', '区县',
        '最近上线', 'R', 'F', 'M', 'R级', 'F级', 'M级', 'RFM类别',
    ]]


def compute_rfm_shared(window_months: int = 12,
                        r_days: int = DEFAULT_R_DAYS,
                        f_count: int = DEFAULT_F_COUNT,
                        m_value: float = DEFAULT_M_VALUE) -> pd.DataFrame:
    """统一 RFM 计算（基于红包表）+ scope 过滤

    Args:
        window_months: 时间窗口（月）
        r_days / f_count / m_value: 分类阈值（默认 60 天 / 6 次 / 1 万元）

    Returns:
        DataFrame[客户编码, 上线客户名称, 签约代理商, 城市, 区县,
                   最近上线, R, F, M, R级, F级, M级, RFM类别]

    🔒 重要：自动按当前用户 scope 过滤
    实现方式：底层 _compute_rfm_shared_raw 缓存全量，本函数包装层应用 scope。
    避免 cache_data 命中导致跨用户数据泄露。
    """
    df = _compute_rfm_shared_raw(window_months, r_days, f_count, m_value)
    return _apply_scope_filter(df, city_col='城市', district_col='区县')


def _apply_tier_to_df(df: pd.DataFrame, code_col: str = '客户编码') -> pd.DataFrame:
    """给一个含「客户编码」的 DataFrame 用 load_provider_tier_shared 的等级覆盖「服务商等级」列。
    原列保留为「服务商等级_原始」便于对比。
    """
    if df.empty or code_col not in df.columns:
        return df
    tier_df = load_provider_tier_shared()
    if tier_df.empty:
        return df
    tier_map = dict(zip(
        tier_df['客户编码'].astype(str).str.strip(),
        tier_df['服务商等级'],
    ))
    code_norm = df[code_col].astype(str).str.strip()
    if '服务商等级' in df.columns:
        df['服务商等级_原始'] = df['服务商等级']
    df['服务商等级'] = code_norm.map(tier_map).fillna('v0未激活')
    return df


# ══════════════════════════════════════════════
# Scope-aware 包装：所有 load_*_shared 调用自动按当前用户 scope 过滤
#
# 实现：捕获原 @st.cache_data 装饰过的 raw 函数 → 替换为包一层 scope 过滤
# admin/manager 不受影响。
# ══════════════════════════════════════════════

_raw_load_main_shared = load_main_shared
_raw_load_redpack_shared = load_redpack_shared
_raw_load_profile_shared = load_profile_shared
_raw_load_contract_shared = load_contract_shared
_raw_load_provider_master_shared = load_provider_master_shared
_raw_load_visit_shared = load_visit_shared
_raw_load_vest_shared = load_vest_shared


def load_main_shared() -> pd.DataFrame:
    df = _raw_load_main_shared()
    return _apply_scope_filter(df, city_col='上线城市', district_col='上线区县')


def load_redpack_shared() -> pd.DataFrame:
    df = _raw_load_redpack_shared()
    return _apply_scope_filter(df, city_col='上线客户地市', district_col='上线客户区县')


def load_profile_shared() -> pd.DataFrame:
    df = _raw_load_profile_shared()
    return _apply_scope_filter(df, city_col='地市', district_col='区县')


def load_contract_shared() -> pd.DataFrame:
    df = _raw_load_contract_shared()
    return _apply_scope_filter(df, city_col='客户城市', district_col='客户区县')


def load_provider_master_shared() -> pd.DataFrame:
    df = _raw_load_provider_master_shared()
    return _apply_scope_filter(df, city_col='地市', district_col='区县')


def load_visit_shared() -> pd.DataFrame:
    df = _raw_load_visit_shared()
    return _apply_scope_filter(df, city_col='拜访客户城市', district_col='拜访客户区县',
                                salesperson_col='打卡人姓名')


def load_vest_shared() -> pd.DataFrame:
    df = _raw_load_vest_shared()
    return _apply_scope_filter(df, city_col='城市')
