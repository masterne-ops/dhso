#!/usr/bin/env python3
"""本周(2026-06-14)数据入库 — 自动探测表头版,6 张表

特点:
  - find_header 按关键列名自动定位真表头行 → 不管前面垫几行标题废行都兼容(新旧通吃)
  - 列对齐 3 模式:name(列名交集) / pos(列位置) / map(列名映射)
  - 增量表筛 6/07-6/13 + INSERT OR IGNORE;签约 REPLACE_ALL 全量替换;铺货带数据时点
  - 默认 DRY-RUN(只统计不写);加 --apply 才真入库,单事务、失败整体回滚

用法:
  python load_weekly_20260614.py            # dry-run,核对行数
  python load_weekly_20260614.py --apply    # 正式入库
"""
import sqlite3, openpyxl, sys, math
from datetime import datetime
import pandas as pd

DB = '/opt/so-data-analytics/db/product_flow.db'
DIR = '/tmp/614数据'
LO, HI = '2026-06-07 00:00:00', '2026-06-13 23:59:59'
APPLY = '--apply' in sys.argv


def to_cell(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def is_blank(c):
    return c is None or (isinstance(c, float) and math.isnan(c))


def find_header(rows, must_have, scan=8):
    """扫前 scan 行,返回首个包含全部 must_have 关键列的行号(0-based)"""
    for i, row in enumerate(rows[:scan]):
        cells = set(str(c).strip() for c in row if not is_blank(c))
        if all(k in cells for k in must_have):
            return i
    raise ValueError(f"找不到表头(需含 {must_have})")


def db_cols(conn, t):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]


DI_POS = [
    (0, '序列号'), (1, '物料号'), (2, '产品名称'), (3, '外部型号'), (4, '内部型号'), (5, '规格'),
    (6, '产品线一级'), (7, '产品线二级'), (8, '产品系列'), (9, '产品子系列'), (10, '销售类型一级'),
    (11, '分销产品标签'), (12, '出库时间'), (13, '订单时间'), (14, '设备上线时间'), (15, '设备上线数据来源'),
    (16, '合同编号'), (17, '订单编号'), (18, '产品下单价'), (19, '产品分销价'), (20, '分销合同类型'),
    (21, '产品标签'), (22, '扫码添加_手动添加'), (23, '扫码时GPS省'), (24, '扫码时GPS市'), (25, '扫码时GPS区'),
    (26, '出货客户账号_姓名'), (27, '客户编码_上级'), (28, '客户名称_上级'), (29, '所在省份_上级'),
    (30, '所在城市_上级'), (31, '所在区县_上级'), (32, '客户所有者'), (33, '客户编码_下级'), (34, '客户名称_下级'),
    (35, '客户所在省份_下级'), (36, '客户所在城市_下级'), (37, '客户所在区县_下级'), (38, '铺货单号'),
    (39, '提交铺货时间'), (40, '铺货单状态'), (41, '下级客户确认收货时间'), (42, '完成时间'),
    (43, '设备清除状态'), (44, '退回时间'),
]

MEETING_MAP = {
    'ADSPID': 'ADSPID', '参会客户编码(实时)': '参会客户编码', '云商会议ID': '云商会议ID',
    '活动名称': '活动名称', '活动开始时间': '活动开始时间', '主办方（认证名称）': '主办方代理商',
    '签到时填写的公司名称': '签到填写公司名', '参会客户名称(实时)': '参会客户名称', '参与人姓名': '参与人姓名',
    '账号': '账号', '用户类型': '用户类型', '渠道客户类型（固化）': '渠道客户类型_固化',
    '星级（固化）': '星级_固化', '渠道客户类型（实时）': '渠道客户类型_实时', '是否激活（实时）': '是否激活_实时',
    '参会后安装红包金额': '参会后安装红包金额', '报名时间': '报名时间', '签到时间': '签到时间',
    '评价时间': '评价时间', '评价内容': '评价内容', '50或100元签到券': '券50_编码', 'S2产品5元签到券': '券5_编码',
}

CONFIGS = [
    dict(table='provider_contract', file='服务商签约明细表_20260614.xlsx',
         must_have=['客户编码', '签约日期'], align='name', strategy='REPLACE_ALL',
         tcol=None, label='全量替换'),
    dict(table='visit_record', file='拜访活动明细表_20260614.xlsx',
         must_have=['活动编号'], align='name', strategy='IGNORE',
         tcol='拜访时间', pk=['活动编号'], label='6/07-6/13'),
    dict(table='product_flow', file='FX601-设备激活明细表-6.1-6.14.xlsx',
         must_have=['ID', '产品序列号'], align='name', strategy='IGNORE',
         tcol='上线时间', pk=['ID'], label='6/07-6/13'),
    dict(table='distribution_info', file='铺货明细表-6月.xlsx',
         must_have=['序列号', '铺货单号'], align='pos', pos=DI_POS, strategy='IGNORE',
         tcol='提交铺货时间', lo='2026-06-07 12:36:17', pk=['序列号', '铺货单号'],
         extra={'数据时点': '2026-06-14'}, label='>6/07 12:36 - 6/13'),
    dict(table='promotion_meeting', file='会议沙龙参会客户明细表_20260614.xlsx',
         must_have=['ADSPID'], align='map', cmap=MEETING_MAP, strategy='IGNORE',
         tcol='活动开始时间', pk=['ADSPID', '参会客户编码(实时)'], label='6/07-6/13'),
]

RP = dict(table='install_redpack', file='安装红包明细清单表-1.1-6.14.xlsx',
          must_have=['产品序列号', '上线时间'], tcol='上线时间', label='6/07-6/13')


def build_colmap(cfg, header, dbc):
    if cfg['align'] == 'name':
        return [(i, c) for i, c in enumerate(header) if c and c in dbc]
    if cfg['align'] == 'pos':
        return [(i, name) for i, name in cfg['pos'] if name in dbc]
    if cfg['align'] == 'map':
        return [(i, cfg['cmap'][c]) for i, c in enumerate(header)
                if c and c in cfg['cmap'] and cfg['cmap'][c] in dbc]


def process_pandas(conn, cfg):
    table = cfg['table']
    print(f"\n══ {table} ← {cfg['file']} ══")
    rows = pd.read_excel(f"{DIR}/{cfg['file']}", header=None).values.tolist()
    h = find_header(rows, cfg['must_have'])
    header = [str(c).strip() if not is_blank(c) else None for c in rows[h]]
    data = rows[h + 1:]
    print(f"  表头探测=第{h+1}行  数据{len(data)}行")
    dbc = db_cols(conn, table)
    cm = build_colmap(cfg, header, dbc)
    print(f"  列对齐({cfg['align']})={len(cm)}列")
    if cfg['align'] == 'map':
        unmatched = [c for c in header if c and c not in cfg['cmap']]
        if unmatched:
            print(f"  未映射(忽略): {unmatched[:8]}")

    ti = header.index(cfg['tcol']) if (cfg.get('tcol') and cfg['tcol'] in header) else None
    lo, hi = cfg.get('lo', LO), cfg.get('hi', HI)
    pk_idx = [header.index(k) for k in cfg.get('pk', []) if k in header]
    extra = cfg.get('extra', {})
    ecols, evals = list(extra), [extra[c] for c in extra]

    batch, nt, npk = [], 0, 0
    for r in data:
        if all(is_blank(c) for c in r):
            continue
        if ti is not None:
            tv = to_cell(r[ti]) if ti < len(r) else None
            if not tv or not (lo <= str(tv) <= hi):
                nt += 1
                continue
        if pk_idx and any(pi >= len(r) or to_cell(r[pi]) in (None, '') for pi in pk_idx):
            npk += 1
            continue
        batch.append(tuple(evals + [to_cell(r[i]) if i < len(r) else None for i, _ in cm]))
    print(f"  命中[{cfg['label']}]: {len(batch)}行  (时段外{nt} / PK缺{npk})")

    allc = ecols + [c for _, c in cm]
    csql = ', '.join(f'"{c}"' for c in allc)
    ph = ', '.join(['?'] * len(allc))
    if cfg['strategy'] == 'REPLACE_ALL':
        before = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        print(f"  REPLACE_ALL: {'删除' if APPLY else '将删'} {before}行 + 插 {len(batch)}行")
        if APPLY:
            conn.execute(f'DELETE FROM "{table}"')
            conn.executemany(f'INSERT INTO "{table}" ({csql}) VALUES ({ph})', batch)
    else:
        if APPLY:
            b0 = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            conn.executemany(f'INSERT OR IGNORE INTO "{table}" ({csql}) VALUES ({ph})', batch)
            b1 = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(f"  ✅ 实插 {b1-b0}  (主键重复跳过 {len(batch)-(b1-b0)})")
    return len(batch)


def process_redpack(conn):
    cfg = RP
    table = cfg['table']
    print(f"\n══ {table} ← {cfg['file']} (流式,大表) ══")
    wb = openpyxl.load_workbook(f"{DIR}/{cfg['file']}", read_only=True, data_only=True)
    ws = wb.active
    dbc = db_cols(conn, table)
    header = ti = cm = None
    batch, nt = [], 0
    for row in ws.iter_rows(values_only=True):
        if header is None:
            cells = set(str(c).strip() for c in row if not is_blank(c))
            if all(k in cells for k in cfg['must_have']):
                header = [str(c).strip() if not is_blank(c) else None for c in row]
                ti = header.index(cfg['tcol'])
                cm = [(i, c) for i, c in enumerate(header) if c and c in dbc]
            continue
        tv = to_cell(row[ti]) if ti < len(row) else None
        if not tv or not (LO <= str(tv) <= HI):
            nt += 1
            continue
        batch.append(tuple(to_cell(row[i]) if i < len(row) else None for i, _ in cm))
    wb.close()
    print(f"  表头探测OK  列对齐={len(cm)}列  命中[{cfg['label']}]: {len(batch)}行 (时段外{nt})")
    allc = [c for _, c in cm]
    csql = ', '.join(f'"{c}"' for c in allc)
    ph = ', '.join(['?'] * len(allc))
    if APPLY:
        b0 = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        conn.executemany(f'INSERT OR IGNORE INTO "{table}" ({csql}) VALUES ({ph})', batch)
        b1 = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        print(f"  ✅ 实插 {b1-b0}  (主键重复跳过 {len(batch)-(b1-b0)})")
    return len(batch)


def main():
    conn = sqlite3.connect(DB)
    mode = '正式入库 --apply' if APPLY else 'DRY-RUN(只统计,不写库)'
    print(f"{'='*22} {mode} {'='*22}")
    if APPLY:
        conn.execute('BEGIN')
    try:
        process_redpack(conn)
        for cfg in CONFIGS:
            process_pandas(conn, cfg)
        if APPLY:
            conn.commit()
            print("\n✅ 全部入库已提交")
        else:
            print("\n[DRY-RUN] 未写库。核对无误后加 --apply 执行。")
    except Exception as e:
        if APPLY:
            conn.rollback()
            print(f"\n❌ 出错,已整体回滚: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
