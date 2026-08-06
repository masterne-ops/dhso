#!/usr/bin/env python3
"""智能批量导入 — data-import 页面批量上传一批 Excel,自动识别每张表 + 按规则导入。

一次拖入 1~N 个 Excel(任意子集),系统:
  1. 解析每个文件(自动定位表头行 + 特征列识别)判断它对应哪张库表;
  2. 按各表自己的列对齐方式(名对齐 / 列位置 / COLMAP)对齐到库表列;
  3. 按规则导入:
       - monthly  按整月更新:探测数据里出现的月份,DELETE 该月 + INSERT 该月;
       - full     全量替换:DELETE 全表 + INSERT;
       - snapshot 按数据时点替换:DELETE 同时点 + INSERT(时点=导出日期,保留历史时点)。
  4. 全程先 analyze(只读预览:进哪个表/什么规则/影响哪些月/删X插N),用户确认后
     才 execute(单事务写入,失败回滚)。

7 张表的导入规格集中在 SPECS;新增一张表只需加一条规格。
"""
import re
import sqlite3
import datetime
import pandas as pd


# ── distribution_info:新版铺货文件(2026-07 起,57列,列名唯一)按名映射 ──
# Excel 列名 → DB 列名。上级/下级客户字段沿用库里旧列名(客户编码_上级 等),消费方零改动;
# 新增的 14 个字段(军团/省区/云联/拓新政策等)存库但暂不使用。
# 旧版文件(≤2026-06,45列,「客户编码」等列名重复)用下面的 DIST_COLS 位置映射,已弃用留档。
DIST_COLMAP = {
    # 与库列同名
    '序列号': '序列号', '物料号': '物料号', '产品名称': '产品名称', '外部型号': '外部型号',
    '内部型号': '内部型号', '规格': '规格', '产品线一级': '产品线一级', '产品线二级': '产品线二级',
    '产品系列': '产品系列', '产品子系列': '产品子系列', '销售类型一级': '销售类型一级',
    '分销产品标签': '分销产品标签', '出库时间': '出库时间', '订单时间': '订单时间',
    '合同编号': '合同编号', '订单编号': '订单编号', '产品下单价': '产品下单价',
    '产品分销价': '产品分销价', '分销合同类型': '分销合同类型', '产品标签': '产品标签',
    '铺货单号': '铺货单号', '提交铺货时间': '提交铺货时间', '铺货单状态': '铺货单状态',
    '下级客户确认收货时间': '下级客户确认收货时间', '完成时间': '完成时间',
    '设备清除状态': '设备清除状态', '退回时间': '退回时间',
    # 改名(库列名不动)
    '扫码添加/手动添加': '扫码添加_手动添加',
    '扫码时GPS-省': '扫码时GPS省', '扫码时GPS-市': '扫码时GPS市', '扫码时GPS-区': '扫码时GPS区',
    '出货客户账号(姓名)': '出货客户账号_姓名',
    '出货客户编码': '客户编码_上级', '出货客户名称': '客户名称_上级',
    '出货客户所在省份': '所在省份_上级', '出货客户所在城市': '所在城市_上级',
    '出货客户所在区县': '所在区县_上级',
    '客户所有者(姓名+工号)': '客户所有者',
    '下级客户编码': '客户编码_下级', '下级客户名称': '客户名称_下级',
    '下级客户所在省份': '客户所在省份_下级', '下级客户所在城市': '客户所在城市_下级',
    '下级客户所在区县': '客户所在区县_下级',
    # 新版新增字段(存库,暂不使用)
    '设备上线状态': '设备上线状态', '安装红包上线时间': '安装红包上线时间',
    '云联首次上线时间': '云联首次上线时间', '云联在线时长（h）': '云联在线时长_h',
    '无线产品有效上线时间': '无线产品有效上线时间',
    '出货客户军团': '出货客户军团', '出货客户省区': '出货客户省区',
    '客户所有者直接上级工号': '客户所有者直接上级工号',
    '下级客户所在省份-固化': '下级客户所在省份_固化',
    '下级客户军团': '下级客户军团', '下级客户省区': '下级客户省区',
    '下级客户是否新签认证SMB服务商': '下级客户是否新签认证SMB服务商',
    '下级客户是否25年未铺货无线设备客户': '下级客户是否25年未铺货无线设备客户',
    '铺货单是否满足拓新政策': '铺货单是否满足拓新政策',
}

# 旧版铺货文件的位置映射(已弃用,留档以备老文件重现)
DIST_COLS = [
    (0, '序列号'), (1, '物料号'), (2, '产品名称'), (3, '外部型号'), (4, '内部型号'),
    (5, '规格'), (6, '产品线一级'), (7, '产品线二级'), (8, '产品系列'), (9, '产品子系列'),
    (10, '销售类型一级'), (11, '分销产品标签'), (12, '出库时间'), (13, '订单时间'),
    (14, '设备上线时间'), (15, '设备上线数据来源'), (16, '合同编号'), (17, '订单编号'),
    (18, '产品下单价'), (19, '产品分销价'), (20, '分销合同类型'), (21, '产品标签'),
    (22, '扫码添加_手动添加'), (23, '扫码时GPS省'), (24, '扫码时GPS市'), (25, '扫码时GPS区'),
    (26, '出货客户账号_姓名'), (27, '客户编码_上级'), (28, '客户名称_上级'),
    (29, '所在省份_上级'), (30, '所在城市_上级'), (31, '所在区县_上级'), (32, '客户所有者'),
    (33, '客户编码_下级'), (34, '客户名称_下级'), (35, '客户所在省份_下级'),
    (36, '客户所在城市_下级'), (37, '客户所在区县_下级'), (38, '铺货单号'),
    (39, '提交铺货时间'), (40, '铺货单状态'), (41, '下级客户确认收货时间'),
    (42, '完成时间'), (43, '设备清除状态'), (44, '退回时间'),
]

# ── SMB 客户拜访明细:Excel 列名 → visit_record 列名(大华侧;打卡人所属公司留空)──
SMB_COLMAP = {
    '活动编码': '活动编号', '拜访类型': '拜访类型', '活动创建时间': '活动创建时间',
    '拜访时间': '拜访时间', '客户编码': '客户编码', '客户名称': '拜访客户',
    '省份': '拜访客户省份', '城市': '拜访客户城市', '区县': '拜访客户区县',
    '行动业务员': '打卡人姓名', '行动业务员工号': '打卡人工号/账号',
    '行动业务员省份': '打卡人所属省份', '行动业务员部门': '打卡人所属部门',
    '渠道客户类型': '渠道客户类型', '分销商认证': '分销商认证',
    '客户所有者': '客户所有者', '客户所有者工号': '所有者工号',
    '拜访对象': '拜访对象', '拜访对象职位': '拜访对象职位', '拜访目的': '拜访目的',
    '达成结果': '达成结果', '后期计划': '后期计划',
    '打卡异常类型': '打卡异常类型', '打卡异常描述': '打卡异常描述',
    '打卡地点': '打卡地点', '距离偏差_米': '距离偏离_米',
    '场景类型': '场景类型', '附件类型': '附件类型',
}

# ── 7 张表的导入规格 ───────────────────────────────────────────
# header_keys: 表头特征列(全部命中才算这张表) → 同时用于自动定位表头行
# exclude_keys: 这些列出现则排除(消除两张签约表的歧义)
# align: name(名对齐) / position(DIST_COLS) / colmap(SMB_COLMAP)
# rule: monthly / full / snapshot
# delete_filter: monthly 规则下 DELETE 的附加条件(过滤大华 / 只删大华)
SPECS = [
    {
        'key': 'install_redpack', 'label': '安装红包明细', 'table': 'install_redpack',
        # 产品序列号非唯一(同设备可多次红包记录=合法,库无 UNIQUE 约束),允许重复入库 → pk 留空不去重;
        # 批内去重仅用于有真唯一键的表(如 product_flow.ID),pk=[] 的表不做去重
        'sheet': 0, 'pk': [], 'header_keys': ['上线时间', '产品序列号', '安装军团'],
        'file_keys': ['安装红包', '红包明细'],
        'align': 'name', 'rule': 'monthly', 'time_excel': '上线时间', 'time_db': '上线时间',
        # 新版红包文件把「所属一级客户/编码」改名带「（固化）」后缀(实时口径另有列),
        # 映射回库表无后缀列名,否则代理商归属 SO(所属一级客户口径)全部丢失。
        'rename': {'所属一级客户（固化）': '所属一级客户', '所属一级客户编码（固化）': '所属一级客户编码'},
    },
    {
        'key': 'product_flow', 'label': 'FX601 设备激活(SO主表)', 'table': 'product_flow',
        'sheet': 0, 'pk': ['ID'], 'header_keys': ['ID', '产品序列号', '出库时间', '上线时间'],
        'file_keys': ['FX601', '设备激活', '激活明细'],
        'align': 'name', 'rule': 'monthly', 'time_excel': '上线时间', 'time_db': '上线时间',
    },
    {
        'key': 'provider_contract', 'label': '服务商签约明细', 'table': 'provider_contract',
        'sheet': 0, 'pk': ['客户编码'], 'header_keys': ['签约日期', '客户编码', '渠道客户类型'],
        'exclude_keys': ['累计任务', '活动编号'],
        'file_keys': ['签约明细', '服务商签约'],
        'align': 'name', 'rule': 'full',
    },
    {
        'key': 'visit_dealer', 'label': '拜访活动明细(代理商跑动)', 'table': 'visit_record',
        'sheet': 0, 'pk': ['活动编号'], 'header_keys': ['活动编号', '拜访时间', '打卡人所属公司'],
        'file_keys': ['拜访活动明细'],
        'align': 'name', 'rule': 'monthly', 'time_excel': '拜访时间', 'time_db': '拜访时间',
        # 过滤大华(打卡公司空):只删/插代理商行,大华走 SMB
        'row_filter': 'dealer', 'delete_filter': "COALESCE(打卡人所属公司,'')!=''",
    },
    {
        'key': 'distribution', 'label': '铺货明细(分销在途)', 'table': 'distribution_info',
        'sheet': 0, 'pk': ['序列号', '铺货单号'], 'header_keys': ['序列号', '铺货单号', '提交铺货时间'],
        'file_keys': ['ditribution', 'distribution', '铺货'],
        # 2026-07 新版文件(57列,列名唯一)改按名映射 DIST_COLMAP;旧版位置映射 DIST_COLS 弃用
        'align': 'colmap', 'colmap': DIST_COLMAP, 'rule': 'monthly',
        'time_excel': '提交铺货时间', 'time_db': '提交铺货时间', 'period_col': '数据时点',
    },
    {
        'key': 'visit_smb', 'label': 'SMB客户拜访(大华分销经理跑动)', 'table': 'visit_record',
        'sheet': '客户拜访活动明细', 'pk': ['活动编号'], 'header_keys': ['活动编码', '行动业务员', '拜访时间'],
        'file_keys': ['smb'],
        'align': 'colmap', 'rule': 'monthly', 'time_excel': '拜访时间', 'time_db': '拜访时间',
        # 大华侧:只删/插大华行(打卡公司空)
        'delete_filter': "COALESCE(打卡人所属公司,'')=''",
    },
    {
        # 「11_签约客户月度进度表」正确落地表 = signed_customer_monthly(28列,主键客户编码),
        # 见最新周更 load_replace_20260626.py;dealer_si_snapshot 是另一个文件「签约客户明细表」(67列)。
        'key': 'signed_monthly', 'label': '签约客户月度进度(SI)', 'table': 'signed_customer_monthly',
        'sheet': 0, 'pk': ['客户编码'], 'header_keys': ['累计任务', '累计任务完成率', '客户所有者'],
        'file_keys': ['签约客户月度进度', '月度进度'],
        'align': 'name', 'rule': 'full',
    },
    {
        # SMB客户沙盘管理表(服务商沙盘):服务商档案全量快照,整表替换。
        'key': 'provider_profile', 'label': '服务商管理沙盘', 'table': 'provider_profile',
        'sheet': 0, 'pk': ['客户编码'], 'header_keys': ['公司名称', '客户编码', '外部客户名称'],
        'file_keys': ['沙盘', 'SMB客户沙盘'],
        'align': 'name', 'rule': 'full',
    },
    {
        # 潜客明细表(全量潜客池:高德/一站式/NP流转 各来源) → gaode_potential_customer 按时点快照。
        # ⚠️ ≠ np_transfer_customer(NP转入表,CLI load_np_transfer 独立维护);NP 转入客户 ⊆ 本表。
        'key': 'lead_pool', 'label': '潜客明细表(全量潜客池)', 'table': 'gaode_potential_customer',
        'sheet': 0, 'pk': ['外部客户名称'], 'header_keys': ['外部客户名称', '客户来源', '责任分销经理'],
        'file_keys': ['潜客明细'],
        'align': 'name', 'rule': 'snapshot', 'period_col': '数据时点',
        # 拜访列 Excel 带换行,规范化后映射回库表下划线列名
        'rename': {
            '最近拜访时间 （分销经理）': '最近拜访时间_分销经理',
            '年度拜访次数 （分销经理）': '年度拜访次数_分销经理',
            '本月拜访次数 （分销经理）': '本月拜访次数_分销经理',
            '最近拜访时间 （分销商）': '最近拜访时间_分销商',
            '年度拜访次数 （分销商）': '年度拜访次数_分销商',
            '本月拜访次数 （分销商）': '本月拜访次数_分销商',
        },
    },
    {
        # RP10-SMB产品线分析-客户(双表头:行2产品线分组/行3字段;数据=本年累计到月)。
        # 产品经理工作·代理商产品均衡(page41):CCTV/数通/配套占比 → 产品均衡1%返点。
        # snapshot 按数据时点(=导出日期)全量替换:同时点重导替换,季度末快照累积供结算/年度补齐。
        'key': 'product_balance', 'label': 'SMB产品线分析-客户(产品均衡)', 'table': 'product_line_balance',
        'sheet': 0, 'pk': ['客户编码'], 'header_keys': ['客户编码', 'CCTV占比', '数通实销占比'],
        'file_keys': ['产品线分析', 'RP10'],
        'align': 'name', 'rule': 'snapshot', 'period_col': '数据时点',
    },
]


# ── 工具 ───────────────────────────────────────────────────────
def _norm(s):
    """列名规范化:strip + 换行/制表符→空格 + 折叠多空格(与 import_excel_to_db 一致)。"""
    return re.sub(r'\s+', ' ', re.sub(r'[\r\n\t]+', ' ', str(s).strip()))


def _norm_cols(cols):
    """规范化 + 重名加 .N 后缀(对齐库表里同样处理过的重复列)。"""
    out, seen = [], {}
    for c in cols:
        c = _norm(c)
        if c in seen:
            seen[c] += 1
            c = f"{c}.{seen[c]}"
        else:
            seen[c] = 0
        out.append(c)
    return out


def _cell(v):
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    if isinstance(v, (datetime.datetime, datetime.date, pd.Timestamp)):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, str):
        return v.strip()
    return v


def detect_period(filename):
    """从文件名提取导出日期(_20260628_ → 2026-06-28),取不到用今天。"""
    m = re.search(r'(20\d{2})(\d{2})(\d{2})', str(filename))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.date.today().isoformat()


def _find_header_row(raw, keys, scan=10):
    """在前 scan 行里找全部命中 keys(规范化后)的行,返回行号(0-based);找不到 None。"""
    nkeys = [_norm(k) for k in keys]
    for i in range(min(scan, len(raw))):
        cells = set(_norm(x) for x in raw.iloc[i].tolist() if pd.notna(x))
        if all(k in cells for k in nkeys):
            return i
    return None


def _pick_sheet(xls, sheet):
    names = xls.sheet_names
    if isinstance(sheet, int):
        return names[sheet] if sheet < len(names) else None
    return sheet if sheet in names else None


def _db_columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


# ── 识别 ───────────────────────────────────────────────────────
def identify(path, filename, xls=None):
    """返回 (spec, sheet_name, header_row);识别不出返回 None。多个命中取第一个。"""
    xls = xls or pd.ExcelFile(path)
    for spec in SPECS:
        sh = _pick_sheet(xls, spec['sheet'])
        if sh is None:
            continue
        raw = pd.read_excel(path, sheet_name=sh, header=None, nrows=12)
        hr = _find_header_row(raw, spec['header_keys'])
        if hr is None:
            continue
        cells = set(_norm(x) for x in raw.iloc[hr].tolist() if pd.notna(x))
        if any(_norm(k) in cells for k in spec.get('exclude_keys', [])):
            continue
        return spec, sh, hr
    return None


# ── 对齐 ───────────────────────────────────────────────────────
def _align(data_named, raw_body, spec, db_cols):
    """把数据对齐到库表列。返回 (cols_used, rows[list[tuple]])。

    data_named: 带规范化列名的 DataFrame(表头行之后);
    raw_body:   同样的数据但列按位置(0,1,2…),供 position 对齐用。
    """
    align = spec['align']
    if align == 'position':
        cols_used = [name for _, name in DIST_COLS if name in db_cols]
        idxs = [(idx, name) for idx, name in DIST_COLS if name in db_cols]
        rows = []
        for _, r in raw_body.iterrows():
            rows.append(tuple(_cell(r.iloc[idx]) if idx < len(r) else None for idx, _ in idxs))
        return cols_used, rows
    if align == 'colmap':
        cm = spec['colmap'] if 'colmap' in spec else SMB_COLMAP
        use = [(e, db) for e, db in cm.items() if e in data_named.columns and db in db_cols]
        cols_used = [db for _, db in use]
        rows = [tuple(_cell(r[e]) for e, _ in use) for _, r in data_named.iterrows()]
        return cols_used, rows
    # name 对齐(可选 rename)
    df = data_named
    if spec.get('rename'):
        df = df.rename(columns=spec['rename'])
    seen, cols_used = set(), []
    for c in df.columns:
        if c in db_cols and c not in seen:
            seen.add(c)
            cols_used.append(c)
    rows = [tuple(_cell(r[c]) for c in cols_used) for _, r in df.iterrows()]
    return cols_used, rows


# ── analyze(只读预览)──────────────────────────────────────────
def analyze_one(conn, path, filename):
    """识别 + 对齐 + 算影响,返回预览 dict(含内部 _spec/_cols/_rows 供 execute 用)。"""
    res = {'filename': filename, 'matched': False, 'warnings': []}
    try:
        xls = pd.ExcelFile(path)
    except Exception as e:
        res['warnings'].append(f'打不开文件:{e}')
        return res

    ident = identify(path, filename, xls)
    if not ident:
        res['warnings'].append('无法识别为任何已知表(表头特征列不匹配)')
        return res
    spec, sh, hr = ident

    raw = pd.read_excel(path, sheet_name=sh, header=None)
    header = _norm_cols(raw.iloc[hr].tolist())
    body = raw.iloc[hr + 1:].reset_index(drop=True)
    # 丢掉整行全空
    body = body[~body.isna().all(axis=1)].reset_index(drop=True)
    data_named = body.copy()
    data_named.columns = header[:body.shape[1]] + list(body.columns[len(header):])

    table = spec['table']
    db_cols = _db_columns(conn, table)
    period = detect_period(filename)

    # 行级过滤(代理商:剔除大华空) + 月份过滤(monthly:剔除时间字段空/异常)
    months = []
    keep_mask = pd.Series(True, index=data_named.index)
    n_dahua = n_badtime = 0

    if spec.get('row_filter') == 'dealer' and '打卡人所属公司' in data_named.columns:
        comp = data_named['打卡人所属公司'].map(lambda x: '' if pd.isna(x) else str(x).strip())
        is_dealer = comp != ''
        n_dahua = int((~is_dealer).sum())
        keep_mask &= is_dealer

    if spec['rule'] == 'monthly':
        tcol = spec['time_excel']
        if tcol not in data_named.columns:
            res['warnings'].append(f'缺时间列「{tcol}」,无法按月更新')
            return res
        ym = pd.to_datetime(data_named[tcol], errors='coerce').dt.strftime('%Y-%m')
        n_badtime = int(ym.isna().sum())
        keep_mask &= ym.notna()
        # 护栏:剔除「未来月份」(> 当前系统年月)的行——一行脏日期会把 DELETE 波及到
        # 不该动的月份,还会把脏行插进库,直接当脏数据剔除并告警。
        cur_ym = datetime.date.today().strftime('%Y-%m')
        future = ym.notna() & (ym > cur_ym)
        if bool(future.any()):
            for m, cnt in ym[future].value_counts().sort_index().items():
                res['warnings'].append(f'剔除未来月份 {m} 共 {cnt} 行(脏数据)')
            keep_mask &= ~future
        months = sorted(set(ym[keep_mask].dropna()))

    data_named = data_named[keep_mask].reset_index(drop=True)
    raw_body = raw.iloc[hr + 1:].reset_index(drop=True)
    raw_body = raw_body[~raw_body.isna().all(axis=1)].reset_index(drop=True)
    raw_body = raw_body[keep_mask.values].reset_index(drop=True)

    cols_used, rows = _align(data_named, raw_body, spec, db_cols)
    if not cols_used:
        res['warnings'].append('没有任何列能对齐到库表(列名全不匹配)')
        return res

    # snapshot:每行加上数据时点
    if spec['rule'] == 'snapshot':
        pc = spec['period_col']
        if pc in db_cols:
            cols_used = [pc] + cols_used
            rows = [(period,) + r for r in rows]
    # distribution(position + monthly):加数据时点
    elif spec.get('period_col') and spec['period_col'] in db_cols:
        pc = spec['period_col']
        cols_used = [pc] + cols_used
        rows = [(period,) + r for r in rows]

    # 主键为空的行剔除:Excel 末尾常带「合计行」(客户编码等主键为空、数值列是全表总和),
    # 混入库会让 SUM 聚合翻倍(2026-07-12 在 signed_customer_monthly 实际发生过)。
    n_pk_null = 0
    pk_in = [c for c in spec.get('pk', []) if c in cols_used]
    if pk_in:
        kidx = [cols_used.index(c) for c in pk_in]
        kept = []
        for r in rows:
            if any(r[i] is None or str(r[i]).strip() == '' for i in kidx):
                n_pk_null += 1
            else:
                kept.append(r)
        rows = kept

    # 主键重复检测 + 内存去重(保留最后一条):monthly 用纯 INSERT,批次内重复主键会触发
    # UNIQUE 冲突、整批回滚;数据源偶有完全重复的导出行,必须先去重再入库。
    n_dup = 0
    if pk_in:
        kidx = [cols_used.index(c) for c in pk_in]
        seen, deduped = {}, []
        for r in rows:
            k = tuple(r[i] for i in kidx)
            if k in seen:
                n_dup += 1
                deduped[seen[k]] = r   # 后者覆盖前者
            else:
                seen[k] = len(deduped)
                deduped.append(r)
        rows = deduped

    # 算影响(现有库里将被删的行数)
    affected = _count_affected(conn, spec, table, months, period)

    # 覆盖度校验:monthly 按月算「删X→插Y」明细,任一月插入 < 删除*0.8 → 缩水预警;
    # full 全表同样校验。防脏日期/半月文件把整月(全表)历史替换成残缺数据。
    months_detail = {}
    shrink_warning = []
    if spec['rule'] == 'monthly':
        ins_by_m = ym[keep_mask].value_counts().to_dict()
        for m in months:
            n_del = _count_affected(conn, spec, table, [m], period)
            n_ins = int(ins_by_m.get(m, 0))
            months_detail[m] = {'delete': n_del, 'insert': n_ins}
            if n_ins < n_del * 0.8:
                shrink_warning.append(
                    f'{table} {m}:将删 {n_del:,} 行但本批仅插 {n_ins:,} 行(<80%),疑似数据不全')
    elif spec['rule'] == 'full':
        if len(rows) < affected * 0.8:
            shrink_warning.append(
                f'{table} 全量替换:现有 {affected:,} 行但本批仅 {len(rows):,} 行(<80%),疑似数据不全')

    res.update(
        matched=True, key=spec['key'], label=spec['label'], table=table,
        rule=spec['rule'], sheet=sh, header_row=hr, period=period,
        months=months, insert_rows=len(rows), delete_rows=affected,
        months_detail=months_detail,
        cols_aligned=len(cols_used), excel_cols=len(header),
        filtered_dahua=n_dahua, skipped_badtime=n_badtime, dup_pk=n_dup,
        _spec=spec, _cols=cols_used, _rows=rows,
    )
    if shrink_warning:
        res['shrink_warning'] = shrink_warning
    if n_dahua:
        res['warnings'].append(f'过滤大华行 {n_dahua}(大华走 SMB)')
    if n_badtime:
        res['warnings'].append(f'时间为空跳过 {n_badtime} 行')
    if n_pk_null:
        res['warnings'].append(f'剔除主键{pk_in}为空 {n_pk_null} 行(疑似合计行)')
    if n_dup:
        res['warnings'].append(f'主键{pk_in}重复 {n_dup} 行(已去重,保留最后一条)')
    return res


def _count_affected(conn, spec, table, months, period):
    rule = spec['rule']
    if rule == 'full':
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if rule == 'snapshot':
        pc = spec['period_col']
        return conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{pc}"=?', (period,)).fetchone()[0]
    # monthly
    if not months:
        return 0
    ph = ','.join('?' * len(months))
    q = f'SELECT COUNT(*) FROM "{table}" WHERE substr("{spec["time_db"]}",1,7) IN ({ph})'
    params = list(months)
    if spec.get('delete_filter'):
        q += f' AND {spec["delete_filter"]}'
    return conn.execute(q, params).fetchone()[0]


# ── execute(单事务写库)────────────────────────────────────────
def execute_one(conn, analysis):
    """对一个已识别的 analysis 执行删插。调用方负责事务(BEGIN/COMMIT/ROLLBACK)。"""
    spec = analysis['_spec']
    table = spec['table']
    cols = analysis['_cols']
    rows = analysis['_rows']
    months = analysis['months']
    period = analysis['period']
    rule = spec['rule']

    csql = ', '.join(f'"{c}"' for c in cols)
    ph = ', '.join(['?'] * len(cols))

    if rule == 'full':
        conn.execute(f'DELETE FROM "{table}"')
    elif rule == 'snapshot':
        conn.execute(f'DELETE FROM "{table}" WHERE "{spec["period_col"]}"=?', (period,))
    else:  # monthly
        if months:
            mph = ','.join('?' * len(months))
            q = f'DELETE FROM "{table}" WHERE substr("{spec["time_db"]}",1,7) IN ({mph})'
            params = list(months)
            if spec.get('delete_filter'):
                q += f' AND {spec["delete_filter"]}'
            conn.execute(q, params)

    # monthly 用纯 INSERT(靠「先删整月」保证幂等):避免 OR REPLACE 在 UNIQUE/PK 冲突时
    # 静默删掉非本批月份的历史行(跨月误删)。full/snapshot 已删干净对应范围,OR REPLACE
    # 仅在该范围内对批内重复保留最后一条,无跨月风险。
    verb = 'INSERT' if rule == 'monthly' else 'INSERT OR REPLACE'
    conn.executemany(f'{verb} INTO "{table}" ({csql}) VALUES ({ph})', rows)
    return {'table': table, 'deleted': analysis['delete_rows'], 'inserted': len(rows)}


def run_batch(conn, files, do_write=False):
    """files: [(filename, path)]. 先全部 analyze;do_write=True 时对识别成功的逐个单事务写库。

    返回 [analysis…];写库结果写回各 analysis 的 'result'/'error'。
    """
    analyses = [analyze_one(conn, path, fn) for fn, path in files]
    if not do_write:
        return analyses
    for an in analyses:
        if not an.get('matched'):
            continue
        try:
            conn.execute('BEGIN')
            an['result'] = execute_one(conn, an)
            conn.commit()
        except Exception as e:
            conn.rollback()
            an['error'] = str(e)
    return analyses
