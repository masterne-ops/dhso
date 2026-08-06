#!/usr/bin/env python3
"""主页 — 数据导入 + 数据库概览（被 app.py 的 st.navigation 调用）

注意：set_page_config 和 require_auth 已经由 app.py 在 navigation 前调用，
这里不再重复调用。
"""

import sqlite3
import datetime as _datetime
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db" / "product_flow.db"

# 两套数据源 schema
DB_SCHEMAS = {
    'main': {
        'label': '主表（产品流向 / 设备明细）',
        'table': 'product_flow',
        'pk': 'ID',
        'required_cols': [
            'ID', '产品序列号', '上线时间',
            '出库客户名称', '上线城市', '上线区县',
            '最新分销价',
        ],
        'indexes': [
            ('idx_id', '"ID"', True),
            ('idx_online_time', '"上线时间"', False),
            ('idx_district', '"上线区县"', False),
            ('idx_dealer', '"出库客户名称"', False),
            ('idx_provider', '"上线自客户名称"', False),
        ],
    },
    'redpack': {
        'label': '安装红包记录',
        'table': 'install_redpack',
        'pk': '产品序列号',
        'required_cols': [
            '产品序列号', '上线时间',
            '上线客户编码', '上线客户名称',
            '所属一级客户', '出货客户名称',
            '产品现有分销价',
        ],
        'indexes': [
            # 产品序列号非唯一(同设备多次红包=合法,生产索引 unique=0),不能建 UNIQUE
            ('idx_rp_serial', '"产品序列号"', False),
            ('idx_rp_time', '"上线时间"', False),
            ('idx_rp_provider_code', '"上线客户编码"', False),
            ('idx_rp_provider_name', '"上线客户名称"', False),
            ('idx_rp_signed', '"所属一级客户"', False),
            ('idx_rp_actual', '"出货客户名称"', False),
        ],
    },
    'profile': {
        'label': '服务商管理沙盘（详细信息）',
        'table': 'provider_profile',
        'pk': '客户编码',
        'required_cols': [
            '客户编码', '公司名称',
        ],
        'indexes': [
            ('idx_pp_code', '"客户编码"', True),
            ('idx_pp_name', '"公司名称"', False),
            ('idx_pp_dealer', '"上级分销商名称"', False),
            ('idx_pp_city', '"地市"', False),
        ],
    },
    'contract': {
        'label': '服务商签约明细',
        'table': 'provider_contract',
        'pk': '客户编码',
        'required_cols': [
            '客户编码', '客户名称',
        ],
        'indexes': [
            ('idx_pc_code', '"客户编码"', True),
            ('idx_pc_name', '"客户名称"', False),
            ('idx_pc_dealer', '"上级分销商名称"', False),
            ('idx_pc_city', '"客户城市"', False),
            ('idx_pc_signed', '"签约日期"', False),
        ],
    },
    'visit': {
        'label': '业务员跑动记录（打卡）',
        'table': 'visit_record',
        'pk': '活动编号',
        'required_cols': [
            '活动编号', '拜访时间', '客户编码', '拜访客户', '打卡人姓名',
        ],
        'indexes': [
            ('idx_vr_id', '"活动编号"', True),
            ('idx_vr_time', '"拜访时间"', False),
            ('idx_vr_code', '"客户编码"', False),
            ('idx_vr_client', '"拜访客户"', False),
            ('idx_vr_clerk', '"打卡人姓名"', False),
            ('idx_vr_company', '"打卡人所属公司"', False),
        ],
    },
    'vest': {
        'label': '🎭 马甲账号名单（已确认的代理商自有账号）',
        'table': 'vest_account',
        'pk': '服务商客户编码',
        'required_cols': [
            '服务商客户编码', '服务商客户名称',
        ],
        'indexes': [
            ('idx_va_code', '"服务商客户编码"', True),
            ('idx_va_dealer', '"对应一级"', False),
            ('idx_va_city', '"城市"', False),
        ],
    },
    # ─── KPI 目标 / 节奏（走 _kpi_loader 专用通道）───────
    'kpi_targets': {
        'label': '📊 区县全年 SO 目标（按年度替换）',
        'table': 'kpi_targets',
        'special_loader': 'kpi_targets',  # 标记：导入时走特殊路径
        'required_cols': ['城市', '区县'],  # 第三列允许任意"XX年SO目标"命名
        'help': '3 列 Excel：城市 / 区县 / XX年SO目标（万）。导入时整年覆盖。',
    },
    'kpi_rhythm': {
        'label': '📊 月度进度条比例（按年度替换）',
        'table': 'kpi_rhythm',
        'special_loader': 'kpi_rhythm',
        'required_cols': ['指标', '1月', '12月'],
        'help': '宽表：指标 / 适用范围 / 类型 / 1月 ~ 12月 / 全年。每行一个指标，每列一个月的占比（小数）。',
    },
    # ─── 业务方人工标注的「明确无采购意向」服务商（走专用导入）─────
    'closed_provider': {
        'label': '🚫 明确无采购意向 服务商名单（业务方人工标记）',
        'table': 'closed_provider',
        'special_loader': 'closed_provider',
        'required_cols': ['客户编码', '客户名称'],
        'help': 'Excel 需含「标注关闭」列 = "是" 的行。会按客户编码覆盖。'
                '识别字段：客户编码 / 客户名称 / 客户城市 / 客户区县 / 上级客户名称 / 服务商等级 / 签约日期 / 联系人 / 联系电话。',
    },
    # ─── ⚔️ 竞品 Top 服务商（重点开拓目标）─────
    'competitor_top': {
        'label': '⚔️ 竞品 Top 服务商（海康核心服务商 — 重点开拓）',
        'table': 'competitor_top_provider',
        'special_loader': 'competitor_top',
        'required_cols': ['客户编码', '拓新客户公司名称', '客户经营品牌'],
        'help': '识别字段：客户编码 / 客户所在省区+城市+区县 / 拓新客户公司名称 / 责任人姓名+角色 / '
                '老板姓名+手机号 / 客户经营品牌 / 25年竞品分销安防体量 / 拜访内容 / 资源投入及转化策略说明 / 备注。'
                '会按客户编码覆盖，「在售大华」字段自动从客户经营品牌派生。',
    },
}

# ──────────────────────────────────────────
# 共享：数据库 / 导入
# ──────────────────────────────────────────

def sanitize_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """pandas 扩展类型 → SQLite 兼容的基础类型"""
    df = df.copy()

    # 防御：列名重复时 df[col] 返回 DataFrame，会让下面的 .dtype 失败
    # 强制去重（保留第一个出现的，后续加 ".dupN" 后缀）
    if len(df.columns) != len(set(df.columns)):
        seen = {}
        new_cols = []
        for c in df.columns:
            if c in seen:
                seen[c] += 1
                new_cols.append(f"{c}.dup{seen[c]}")
            else:
                seen[c] = 0
                new_cols.append(c)
        df.columns = new_cols

    for col in df.columns:
        s = df[col]
        # 再多一道保险：如果还是 DataFrame（不该发生），取第一列
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        if pd.api.types.is_extension_array_dtype(s.dtype):
            df[col] = s.astype(object).where(s.notna(), None)
    return df


def validate_headers(df_cols: list, dataset: str) -> tuple[bool, list]:
    """
    校验上传文件的表头与所选数据集是否匹配。
    多一两列不算错；只有缺少必备列才算"完全不对"。
    返回 (是否通过, 缺失的必备列)
    """
    schema = DB_SCHEMAS[dataset]
    missing = [c for c in schema['required_cols'] if c not in df_cols]
    return len(missing) == 0, missing


def import_excel_to_db(uploaded_file, mode: str = 'merge', dataset: str = 'main') -> dict:
    """
    将上传的 Excel 文件导入到指定表。

    dataset:
      - 'main'    → product_flow 表，PK = ID
      - 'redpack' → install_redpack 表，PK = 产品序列号

    模式：
      - merge:   PK 重合则覆盖（INSERT OR REPLACE），新行追加
      - append:  PK 重合则跳过（INSERT OR IGNORE）
      - replace: 先清空再导入
    """
    if dataset not in DB_SCHEMAS:
        raise ValueError(f"未知数据类型 dataset={dataset}")
    schema = DB_SCHEMAS[dataset]
    table = schema['table']
    pk = schema['pk']

    df_new = pd.read_excel(uploaded_file)
    # 列名标准化：strip + 把内部换行/制表符替换成空格 + 折叠多空格
    # （服务商沙盘里有些字段带 \n，比如 "主营品牌安防产品\n销售额"）
    cleaned = (
        df_new.columns.astype(str).str.strip()
        .str.replace(r'[\r\n\t]+', ' ', regex=True)
        .str.replace(r'\s+', ' ', regex=True)
    )

    # 列名去重：清洗后可能有重复（签约 Excel 里"上线金额"等会出现多次）
    # 把第 N 次出现的同名列加 ".N" 后缀，避免 df[col] 返回 DataFrame
    seen = {}
    unique_cols = []
    for c in cleaned:
        if c in seen:
            seen[c] += 1
            unique_cols.append(f"{c}.{seen[c]}")
        else:
            seen[c] = 0
            unique_cols.append(c)
    df_new.columns = unique_cols

    # 表头校验：缺任一必备列 → 报错（多列允许）
    ok, missing = validate_headers(list(df_new.columns), dataset)
    if not ok:
        raise ValueError(
            f"表头与所选数据类型「{schema['label']}」不匹配，"
            f"缺少必备列：{', '.join(missing)}\n"
            f"请确认你上传的是不是这个数据类型的 Excel。"
        )

    df_new[pk] = df_new[pk].astype(str).str.strip()

    # 文件内 PK 去重（保留最后一条 = 假设新覆盖旧）
    dup = len(df_new) - df_new[pk].nunique()
    if dup > 0:
        df_new = df_new.drop_duplicates(subset=pk, keep='last').reset_index(drop=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
    table_exists = cur.fetchone() is not None

    inserted = updated = 0

    if not table_exists or mode == 'replace':
        df_write = sanitize_for_sqlite(df_new)
        df_write.to_sql(table, conn, if_exists='replace', index=False)
        inserted = len(df_write)
    else:
        cur.execute(f'PRAGMA table_info("{table}")')
        db_cols = [r[1] for r in cur.fetchall()]
        new_cols = [c for c in df_new.columns if c not in db_cols]
        for c in new_cols:
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')

        existing_pks = set(
            r[0] for r in cur.execute(f'SELECT "{pk}" FROM "{table}"').fetchall()
        )
        new_rows = df_new[~df_new[pk].isin(existing_pks)]
        update_rows = df_new[df_new[pk].isin(existing_pks)]
        inserted = len(new_rows)
        updated = len(update_rows) if mode == 'merge' else 0

        df_aligned = sanitize_for_sqlite(df_new)
        df_aligned.to_sql('_tmp_import', conn, if_exists='replace', index=False)

        cols_sql = ', '.join(f'"{c}"' for c in df_aligned.columns)
        verb = 'INSERT OR REPLACE' if mode == 'merge' else 'INSERT OR IGNORE'
        cur.execute(f"""
            {verb} INTO "{table}" ({cols_sql})
            SELECT {cols_sql} FROM _tmp_import
        """)
        cur.execute("DROP TABLE _tmp_import")

    # 索引
    for idx_name, col_expr, unique in schema['indexes']:
        unique_kw = 'UNIQUE ' if unique else ''
        cur.execute(
            f'CREATE {unique_kw}INDEX IF NOT EXISTS {idx_name} ON "{table}"({col_expr})'
        )

    conn.commit()
    total = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    # ─── 派生层视图（V2/V3 共享口径） ─────────────────
    # 把 KPI金额 / 签约状态 / _真异常打卡 / 服务商等级 等派生字段物化进视图
    # 这样 V3 沙箱里 AI 直接 SELECT 视图能看到，跟 V2 报表口径一致
    try:
        from _views import ensure_views
        view_result = ensure_views(conn)
    except Exception as e:
        # 视图重建失败不影响导入主流程，只是警告
        view_result = {'_error': str(e)}

    conn.close()

    # 清各 page 的缓存
    st.cache_data.clear()

    return {
        'inserted': inserted,
        'updated': updated,
        'total': total,
        'duplicates_in_file': dup,
        'dataset': dataset,
        'table': table,
        'views': view_result,
    }


def db_summary() -> dict:
    """返回两张表各自的概况。"""
    if not DB_PATH.exists():
        return {'exists': False, 'tables': {}}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    out = {'exists': True, 'tables': {}}

    for ds_key, schema in DB_SCHEMAS.items():
        table = schema['table']
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if cur.fetchone() is None:
            continue
        cnt = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        try:
            df = pd.read_sql(f'SELECT "上线时间" FROM "{table}"', conn)
            df['上线时间'] = pd.to_datetime(df['上线时间'], errors='coerce')
            df['年月'] = df['上线时间'].dt.to_period('M').astype(str)
            months = df.groupby('年月').size().reset_index(name='台数')
            months = months[months['年月'] != 'NaT'].sort_values('年月')
        except Exception:
            months = pd.DataFrame()
        out['tables'][ds_key] = {
            'label': schema['label'],
            'table': table,
            'total': cnt,
            'months': months,
        }
    conn.close()
    return out


def _save_upload(uf, data_dir):
    """把上传文件安全落盘到 data_dir：取 basename 防路径穿越 + 后缀白名单。

    合法返回落盘 Path；不合法 st.error 并返回 None（调用方跳过该文件）。
    """
    fname = Path(uf.name).name
    if Path(fname).suffix.lower() not in {'.xlsx', '.xls'}:
        st.error(f"❌ 文件名不合法（仅支持 .xlsx / .xls）：{uf.name}")
        return None
    p = data_dir / fname
    with open(p, 'wb') as f:
        f.write(uf.getbuffer())
    return p


# ──────────────────────────────────────────
# 主页 UI
# ──────────────────────────────────────────

st.title("📊 SO 数据分析平台")

st.markdown("""
#### 🎯 核心管理功能（侧栏 01-05）

| # | 页面 | 用途 | 频率 |
|---|---|---|---|
| 02 | 🧭 服务商智能分类（核心） | RFM 5 类分群 + 派单（业务员每日用） | **日度** |
| 03 | 🕵️ 关联挖掘与假商治理 | KPI 治理（团伙 / 假商 / 无效跑动 / 红包审计 / 挤水分） | **周度** |
| 04 | 💪 动作刺激分析 | 管理洞察（哪个动作刺激上线最强） | 月度 |
| 05 | 🔭 服务商全景 | 单点深度画像（客户档案 + 跑动 + 红包 + 上线 + 标签） | 按需 |
| 22 | 📇 服务商全名册 | 全省一览表 + 标签过滤 | 按需 |
| 23 | ☂️ 伞形组管理 | 伞形客户组关系 + 启发式建组 | 周度 |

#### 📊 辅助分析（侧栏 11-17）
SO 环比 · 产品流向 · 代理商能力评分 · 服务商行为 · 销售机会发现 · 红包排行榜 · 大 PK

#### 🧪 实验室（最末）
批量序列号查询 · 数据排行榜 · 产品组合关联 · 红包刺激分析（事件研究）
""")

st.divider()

# ── 智能批量导入（推荐）──────────────────────
st.markdown("### 📦 智能批量导入")
st.caption(
    "一次拖入多张 Excel（安装红包 / FX601设备激活 / 服务商签约明细 / 拜访活动明细 / "
    "SMB客户拜访 / 铺货明细 / 签约客户月度进度），系统**自动识别每张表 + 按规则导入**。"
    "规则：**服务商签约明细、签约客户月度进度 = 全量替换；其余 = 按整月更新（删该月+插该月）**。"
    "可一次全传、也可只传其中几张。"
)
import _smart_import as _si

_batch = st.file_uploader(
    "拖入一批 Excel（可多选）", type=['xlsx', 'xls'],
    accept_multiple_files=True, key="smart_batch",
)
if _batch:
    _data_dir = BASE_DIR / 'data'
    _data_dir.mkdir(parents=True, exist_ok=True)
    _paths = []
    for _uf in _batch:
        _p = _save_upload(_uf, _data_dir)
        if _p is None:
            continue
        _paths.append((_p.name, str(_p)))

    _conn = sqlite3.connect(DB_PATH)
    _ans = _si.run_batch(_conn, _paths, do_write=False)
    _RULE_CN = {'monthly': '按整月更新', 'full': '全量替换', 'snapshot': '按数据时点替换(保留历史)'}
    st.markdown("#### 识别结果 / 导入预览")
    _ok = [a for a in _ans if a.get('matched')]
    for _a in _ans:
        if _a.get('matched'):
            with st.container(border=True):
                st.markdown(f"**{_a['filename']}** → `{_a['table']}`（{_a['label']}）")
                _c = st.columns(4)
                _c[0].metric("规则", _RULE_CN.get(_a['rule'], _a['rule']))
                _mon = '、'.join(_a['months']) if _a['months'] else (
                    '全表' if _a['rule'] == 'full' else _a['period'])
                _c[1].metric("影响月份/时点", _mon)
                _c[2].metric("将删除", f"{_a['delete_rows']:,}")
                _c[3].metric("将插入", f"{_a['insert_rows']:,}")
                st.caption(f"对齐 {_a['cols_aligned']}/{_a['excel_cols']} 列 · 表头第 {_a['header_row']+1} 行")
                if _a.get('months_detail'):
                    st.caption("按月明细：" + "  ·  ".join(
                        f"{_m} 删 {_d['delete']:,} → 插 {_d['insert']:,}"
                        for _m, _d in sorted(_a['months_detail'].items())))
                if _a.get('shrink_warning'):
                    st.error("🚨 " + "\n\n🚨 ".join(_a['shrink_warning']))
                if _a['warnings']:
                    st.warning("⚠️ " + " / ".join(_a['warnings']))
        else:
            st.error(f"❌ **{_a['filename']}**：" + "；".join(_a['warnings']))

    if _ok:
        st.info(f"共识别 {len(_ok)} 张表可导入。每张表单事务写入，失败自动整体回滚。")
        # 缩水替换预警：本批插入 < 库中将删行数的 80%（见上方 🚨），必须人工勾选确认才放行
        _allow_go = True
        if any(_a.get('shrink_warning') for _a in _ok):
            st.error("🚨 存在缩水替换预警（本批行数明显少于库中将删行数，详见上方 🚨），请先核对文件是否完整。")
            _allow_go = st.checkbox("我已核对，确认缩水替换", value=False, key="smart_shrink_ack")
        if st.button(f"💾 确认导入这 {len(_ok)} 张表", type="primary", key="smart_go",
                     disabled=not _allow_go):
            with st.spinner("导入中…"):
                _res = _si.run_batch(_conn, _paths, do_write=True)
                try:
                    from _views import ensure_views
                    ensure_views(_conn)
                except Exception as _e:
                    st.warning(f"视图重建警告：{_e}")
            for _a in _res:
                if _a.get('error'):
                    st.error(f"❌ {_a.get('key', _a['filename'])}：{_a['error']}")
                elif _a.get('result'):
                    st.success(
                        f"✓ {_a['result']['table']}：删 {_a['result']['deleted']:,} / 插 {_a['result']['inserted']:,}")
            st.cache_data.clear()
            st.caption("导入完成，相关页面缓存已清。")
    _conn.close()

st.divider()

# ── 🛒 代理商进货明细导入（按年整年替换,供 page41 进货指导）──────────────
st.markdown("### 🛒 代理商进货明细导入")
st.caption(
    "「工作簿3」类进货明细导出（14 列,日期为「N月D日」）→ dealer_purchase。"
    "**按年整年替换**：删除所选年份全部旧数据 + 插入本文件全量,其他年份不动;"
    "入库后自动派生「下单客户类型」（一级代理商/服务商/其他）。进货指导(page40)自动取最新 6 个月。"
)
_dpc1, _dpc2 = st.columns([1, 3])
_dp_year = _dpc1.number_input("数据年份", min_value=2024, max_value=2099,
                              value=_datetime.date.today().year, step=1,
                              help="文件无年份列,以此为准;该年旧数据将被整体替换")
_dp_file = st.file_uploader("选择进货明细 Excel（.xlsx / .xls）", type=['xlsx', 'xls'], key="dp_uploader")
if _dp_file:
    _dp_path = _save_upload(_dp_file, BASE_DIR / 'data')
    if _dp_path is not None:
        from _dealer_purchase_loader import parse_file as _dp_parse, apply_import as _dp_apply
        try:
            _dp_batch, _dp_stats = _dp_parse(str(_dp_path), int(_dp_year))
        except ValueError as _e:
            st.error(f"❌ {_e}")
            _dp_batch = None
        if _dp_batch:
            _conn2 = sqlite3.connect(DB_PATH)
            _dp_old = _conn2.execute(
                'SELECT COUNT(*) FROM dealer_purchase WHERE 数据年份=?', (int(_dp_year),)).fetchone()[0]
            st.info(
                f"文件 **{_dp_stats['rows']:,} 行** / 实销 **{_dp_stats['amt_wan']:,.1f} 万** / "
                f"月份分布 {_dp_stats['months']}　→　将替换库中 {_dp_year} 年现有 **{_dp_old:,} 行**")
            if _dp_old and _dp_stats['rows'] < _dp_old * 0.8:
                st.error("🚨 本文件行数明显少于库中该年现有行数(<80%),疑似数据不全,请核对后再导。")
                _dp_ack = st.checkbox("我已核对,确认缩水替换", value=False, key="dp_shrink_ack")
            else:
                _dp_ack = True
            if st.button(f"💾 确认整年替换 {_dp_year} 年进货明细", type="primary",
                         key="dp_go", disabled=not _dp_ack):
                with st.spinner("导入中…(单事务,失败回滚)"):
                    try:
                        _r = _dp_apply(_conn2, _dp_batch, int(_dp_year))
                        st.success(f"✅ 删旧 {_r['deleted']:,} / 插 {_r['inserted']:,} | 类型: {_r['types']}")
                        st.cache_data.clear()
                    except Exception as _e:
                        st.error(f"❌ 导入失败(已回滚): {_e}")
            _conn2.close()

st.divider()

# ── 数据导入区域（单表 / 高级）──────────────────────
st.markdown("### 📥 数据导入（单表）")

dataset_pick = st.selectbox(
    "数据类型",
    options=list(DB_SCHEMAS.keys()),
    format_func=lambda k: DB_SCHEMAS[k]['label'],
    index=0,
    key="main_dataset",
    help="主表用于产品流向 + SO 环比；红包表用于服务商行为分析",
)

_schema = DB_SCHEMAS[dataset_pick]
_is_special = bool(_schema.get('special_loader'))

with st.expander(f"该类型必备列（{_schema['label']}）", expanded=False):
    st.code("、".join(_schema['required_cols']), language=None)
    if _schema.get('help'):
        st.caption(_schema['help'])
    else:
        st.caption("⚠️ 缺少这些列将导入失败；多出的列会保留。")

if dataset_pick == 'visit':
    st.info(
        "**业务员跑动有两张源表，更新方式 = 整月全量替换（先删该月、再插该月全量），统一走 CLI 脚本入库：**\n\n"
        "- 🏪 **分销商业务员**（代理商，`打卡人所属公司` 非空）= **拜访活动明细表** → "
        "`migrations/load_visit_record.py --file 拜访.xlsx --months 2026-06:2026-06 --apply`（自动过滤大华行）\n\n"
        "- 🏢 **大华分销经理**（`打卡人所属公司` 空）= **SMB 客户拜访活动明细表**（2026 起换源）→ "
        "`migrations/load_smb_visit.py --file smb.xlsx --months 2026-06:2026-06 --apply`\n\n"
        "两侧互不影响；25 年大华跑动已一次性清零。⚠️ 从本页直接上传只做 merge、不区分大华也不按月替换，**不要用于这两张表的常规更新**。"
    )

uploaded_file = st.file_uploader(
    "选择 Excel 文件（.xlsx / .xls）",
    type=['xlsx', 'xls'],
    key="main_uploader",
)

if uploaded_file and _is_special and _schema['special_loader'] == 'closed_provider':
    # ─── 「明确无采购意向」专用导入流 ────────────────
    from datetime import datetime as _dt2
    if st.button("💾 开始导入", type="primary", key="closed_import_btn"):
        data_dir = BASE_DIR / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        target_path = _save_upload(uploaded_file, data_dir)
        if target_path is None:
            st.stop()

        with st.spinner("解析 + 导入中…"):
            try:
                df_cp = pd.read_excel(target_path)
                # 只保留「标注关闭=是」的行
                if '标注关闭' in df_cp.columns:
                    df_cp = df_cp[df_cp['标注关闭'].astype(str).str.strip() == '是']
                records = []
                for _, r in df_cp.iterrows():
                    code = r.get('客户编码')
                    if pd.isna(code) or not code:
                        continue
                    qty_col = '年安防采购量\n（年采购视频类产品金额）'
                    records.append({
                        '客户编码': str(code),
                        '客户名称': r.get('客户名称'),
                        '城市': r.get('客户城市'),
                        '区县': r.get('客户区县'),
                        '标记类型': '无采购意向',
                        '上级客户': r.get('上级客户名称'),
                        '服务商等级_原始': r.get('服务商等级'),
                        '年安防采购量_万': float(r[qty_col]) if qty_col in df_cp.columns and pd.notna(r[qty_col]) else None,
                        '签约日期': str(r['签约日期'])[:10] if pd.notna(r.get('签约日期')) else None,
                        '联系人': r.get('联系人'),
                        '联系电话': (str(int(r['联系电话'])) if pd.notna(r.get('联系电话')) and isinstance(r.get('联系电话'), float)
                                    else str(r.get('联系电话') or '')),
                    })
                from _ai_log import import_closed_providers
                result = import_closed_providers(records, source_file=uploaded_file.name)
                st.success(
                    f"✅ 导入完成：新增 {result['inserted']} / 更新 {result['updated']} / 共 {result['total']} 条"
                )
                st.caption(f"Excel 已存至 `{target_path.relative_to(BASE_DIR)}`")
            except Exception as e:
                st.error(f"❌ 导入失败：{e}")
                import traceback
                with st.expander("Traceback"):
                    st.code(traceback.format_exc())

elif uploaded_file and _is_special and _schema['special_loader'] == 'competitor_top':
    # ─── ⚔️ 竞品 Top 服务商 专用导入流 ──────────────
    if st.button("💾 开始导入", type="primary", key="ctp_import_btn"):
        data_dir = BASE_DIR / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        target_path = _save_upload(uploaded_file, data_dir)
        if target_path is None:
            st.stop()

        with st.spinner("解析 + 导入中…"):
            try:
                df_ct = pd.read_excel(target_path)
                records = []
                for _, r in df_ct.iterrows():
                    code = r.get('客户编码')
                    if pd.isna(code) or not code:
                        continue
                    phone = r.get('老板手机号')
                    if pd.notna(phone):
                        try:
                            phone = str(int(phone))
                        except Exception:
                            phone = str(phone)
                    else:
                        phone = ''
                    records.append({
                        '客户编码': str(code),
                        '客户名称': r.get('拓新客户公司名称'),
                        '省份': r.get('客户所在省区'),
                        '城市': r.get('客户所在城市'),
                        '区县': r.get('客户所在区县'),
                        '责任人角色': r.get('责任人角色'),
                        '责任人姓名': r.get('责任人姓名'),
                        '老板姓名': r.get('老板姓名'),
                        '老板手机号': phone,
                        '客户经营品牌': r.get('客户经营品牌'),
                        '竞品体量_万': float(r['25年竞品分销安防体量']) if pd.notna(r.get('25年竞品分销安防体量')) else None,
                        '拜访内容': r.get('拜访内容'),
                        '转化策略': r.get('资源投入及转化策略说明'),
                        '备注': r.get('备注'),
                    })
                from _ai_log import import_competitor_top
                result = import_competitor_top(records, source_file=uploaded_file.name)
                st.success(
                    f"✅ 导入完成：新增 {result['inserted']} / 更新 {result['updated']} / 共 {result['total']} 条"
                )
                st.caption(f"Excel 已存至 `{target_path.relative_to(BASE_DIR)}`")
            except Exception as e:
                st.error(f"❌ 导入失败：{e}")
                import traceback
                with st.expander("Traceback"):
                    st.code(traceback.format_exc())

elif uploaded_file and _is_special:
    # ─── KPI 专用导入流（走 _kpi_loader）──────────────
    from datetime import datetime as _dt
    from _kpi_loader import (  # noqa: E402
        ensure_tables as _kpi_ensure,
        import_kpi_targets as _kpi_imp_t,
        import_kpi_rhythm as _kpi_imp_r,
    )

    year = st.number_input(
        "📅 这批数据对应的年度",
        min_value=2020, max_value=2099,
        value=_dt.now().year,
        step=1,
        help="按年度整体替换 — 同年度旧数据会先清空再导入。",
    )
    st.caption("⚠️ KPI 数据导入是「按年度替换」，不支持 merge/append。")

    if st.button("💾 开始导入", type="primary", key="main_import_btn"):
        # 把上传 Excel 落地到本地 data/，方便 CLI 重跑
        data_dir = BASE_DIR / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        target_path = _save_upload(uploaded_file, data_dir)
        if target_path is None:
            st.stop()

        with st.spinner("导入中…"):
            try:
                conn = _sqlite3.connect(DB_PATH)
                _kpi_ensure(conn)

                if _schema['special_loader'] == 'kpi_targets':
                    n = _kpi_imp_t(target_path, conn, year=int(year))
                    msg = f"✅ 区县全年 SO 目标导入成功：{n} 行（{int(year)} 年）"
                elif _schema['special_loader'] == 'kpi_rhythm':
                    n = _kpi_imp_r(target_path, conn, year=int(year))
                    msg = f"✅ 月度进度条比例导入成功：{n} 行（{int(year)} 年）"
                else:
                    msg = "❌ 未知 special_loader"

                conn.close()
                st.success(msg)
                st.caption(
                    f"Excel 已存至 `{target_path.relative_to(BASE_DIR)}` — "
                    f"CLI 可重跑：`python src/_kpi_loader.py --year {int(year)}`"
                )
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                import traceback
                st.error(f"❌ 导入失败：{e}")
                with st.expander("🐞 完整错误堆栈", expanded=True):
                    st.code(traceback.format_exc())

elif uploaded_file:
    import_mode = st.radio(
        "导入模式",
        options=['merge', 'append', 'replace'],
        format_func=lambda x: {
            'merge':   '🔀 合并（重合则覆盖，新行追加）— 推荐',
            'append':  '➕ 追加（已存在的跳过）',
            'replace': '🔄 覆盖（先清空表，再导入）',
        }[x],
        index=0,
        horizontal=False,
    )
    if st.button("💾 开始导入", type="primary", key="main_import_btn"):
        with st.spinner("正在导入…大文件可能需要 30~60 秒"):
            try:
                r = import_excel_to_db(uploaded_file, mode=import_mode, dataset=dataset_pick)
                msg = (
                    f"✅ 导入成功（表 `{r['table']}`）："
                    f"新增 {r['inserted']:,} 行，"
                    f"更新 {r['updated']:,} 行，"
                    f"现有 {r['total']:,} 行"
                )
                if r['duplicates_in_file'] > 0:
                    msg += f"（Excel 内 {r['duplicates_in_file']} 条主键重复，已保留最后一条）"
                st.success(msg)
                st.rerun()
            except ValueError as e:
                st.error(f"❌ {e}")
            except Exception as e:
                import traceback
                st.error(f"❌ 导入失败：{e}")
                with st.expander("🐞 完整错误堆栈（给开发看）", expanded=True):
                    st.code(traceback.format_exc())
else:
    st.info("先选数据类型，再上传 Excel，最后点击「开始导入」")

st.divider()

# ── 当前数据库状况 ──────────────────────
st.markdown("### 📦 当前数据库")

info = db_summary()
if not info['exists'] or not info['tables']:
    st.warning("数据库尚未创建，请先在上方上传 Excel 文件。")
else:
    cols = st.columns(len(DB_SCHEMAS))
    for i, ds_key in enumerate(DB_SCHEMAS.keys()):
        t = info['tables'].get(ds_key)
        with cols[i]:
            st.markdown(f"#### {DB_SCHEMAS[ds_key]['label']}")
            if t is None:
                st.caption(f"❌ 表 `{DB_SCHEMAS[ds_key]['table']}` 尚未创建")
                continue
            has_months = isinstance(t.get('months'), pd.DataFrame) and not t['months'].empty
            if has_months:
                mc = st.columns(2)
                mc[0].metric("行数", f"{t['total']:,}")
                mc[1].metric("月份数", f"{len(t['months'])}")
                st.caption(
                    f"覆盖 {t['months']['年月'].min()} ~ {t['months']['年月'].max()}"
                )
                with st.expander("各月分布"):
                    st.dataframe(
                        t['months'].rename(columns={'年月': '上线年月'}),
                        use_container_width=True,
                        hide_index=True,
                    )
            else:
                # 沙盘类（无时间字段）只显示行数
                st.metric("行数", f"{t['total']:,}")
                st.caption("📋 静态画像数据，无时间维度")

# ── 服务商主档合并视图（沙盘 + 签约）──────────
profile_t = info['tables'].get('profile')
contract_t = info['tables'].get('contract')
if profile_t or contract_t:
    try:
        from _loaders import load_provider_master_shared  # noqa: E402
        master = load_provider_master_shared()
        if not master.empty:
            n_total = len(master)
            n_both = int((master['_来源_沙盘'] & master['_来源_签约']).sum())
            n_only_p = int((master['_来源_沙盘'] & ~master['_来源_签约']).sum())
            n_only_c = int((~master['_来源_沙盘'] & master['_来源_签约']).sum())
            st.info(
                f"🔗 **服务商主档（沙盘 + 签约 自动合并视图）**：共 **{n_total:,}** 个不同客户编码  ·  "
                f"两表都有 {n_both:,} · 仅沙盘 {n_only_p:,} · 仅签约 {n_only_c:,}"
            )
    except Exception:
        pass

st.divider()

# ── 导航提示 ──────────────────────
st.markdown("### 👉 进入分析")
st.markdown(
    """
左侧侧边栏选择：

- **📈 产品流向分析** — 同比 2025 vs 2026，按区县/代理商/服务商透视，含代理商健康度评分（F7）
- **📊 SO 环比分析** — 选择任意两个月做环比，按 5 个维度（城市/区县/产品系列/代理商/服务商）下钻
- **🤝 服务商行为分析** — 基于安装红包记录表，识别流失服务商 + 混合采购异常

数据导入后，相关模块自动读到最新数据。
"""
)
