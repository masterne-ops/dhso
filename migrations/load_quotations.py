#!/usr/bin/env python3
"""报价单入库 — 2 张表

1. dahua_quotation     大华官方报价单(22 sheet 合并,字段统一)
2. competitor_quotation 服务商混卖报价单(海康/TP/萤石/水星等)

字段映射(异构 sheet 通过 alias 统一到主表):
  规格型号 / 产品型号 / 外部型号 / 套餐名称 → 型号
  产品名称 / 物料名称 / 套餐名称             → 产品名称
  下单价 / 分销价 / 套餐售价 / 线上管控红线价 → 价格_元
  产品状态 / 产品生命周期 / 套餐类型          → 产品状态

非映射字段塞进 规格JSON 兜底,不丢数据。
"""
import json
import sqlite3
import openpyxl
from pathlib import Path

DB = '/opt/so-data-analytics/db/product_flow.db'
FP_DAHUA = '/tmp/分销产品报价单260509.xlsx'
FP_COMPETITOR = '/tmp/报价单5.12.xlsx'
PERIOD = '2026-05-09'   # 报价单日期(从文件名提取)

SKIP_SHEETS = {'更新说明', 'WpsReserved_CellImgList'}

# 字段别名映射(原列名 → 标准列名)
ALIAS = {
    # 型号
    '规格型号': '型号', '产品型号': '型号', '外部型号': '型号', '套餐名称': '产品名称',
    # 产品名
    '产品名称': '产品名称', '物料名称': '产品名称',
    # 价格(取第一个非空)
    '下单价': '价格_元', '分销价': '价格_元',
    '套餐售价（用户充值价格）': '价格_元', '线上管控红线价': '价格_元',
    # 产品状态
    '产品状态': '产品状态', '产品生命周期': '产品状态', '套餐类型': '产品状态',
    # 分类
    '分类': '分类', '产品类别': '分类',
    # 目录
    '一级目录': '一级目录', '云商一级目录': '一级目录', '产品系列': '一级目录',
    '二级目录': '二级目录', '云商二级目录': '二级目录',
    # 描述
    '产品功能概述': '功能概述', '产品卖点': '功能概述', '产品描述': '功能概述',
    # 链接
    '云商链接': '云商链接',
    # 物料号(部分 sheet 有)
    '料号': '物料号', '物料号': '物料号',
}
STD_COLS = ['分类', '一级目录', '二级目录', '产品名称', '型号', '物料号',
            '价格_元', '产品状态', '功能概述', '云商链接']


def find_header_row(ws):
    """找表头行(含「规格型号」「产品型号」「物料名称」「套餐名称」等关键字)"""
    anchors = ('规格型号', '产品型号', '物料名称', '套餐名称', '产品名称')
    for i, r in enumerate(ws.iter_rows(max_row=6, values_only=True), start=1):
        cells = [str(c).strip().replace('\n', '') if c else '' for c in r]
        if any(a in cells for a in anchors):
            return i, {c: j for j, c in enumerate(cells) if c}
    return None, {}


def load_dahua(conn):
    print(f'\n══ 大华官方报价单 {FP_DAHUA} ══')
    wb = openpyxl.load_workbook(FP_DAHUA, read_only=True, data_only=True)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dahua_quotation (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            报价日期     TEXT NOT NULL,
            板块         TEXT NOT NULL,
            分类         TEXT,
            一级目录     TEXT,
            二级目录     TEXT,
            产品名称     TEXT,
            型号         TEXT,
            物料号       TEXT,
            价格_元      REAL,
            产品状态     TEXT,
            功能概述     TEXT,
            云商链接     TEXT,
            规格JSON     TEXT,
            导入时间     TEXT
        );
        """)
    cur.execute('DELETE FROM dahua_quotation WHERE 报价日期 = ?', (PERIOD,))
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dq_model ON dahua_quotation(型号)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dq_board ON dahua_quotation(板块)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dq_status ON dahua_quotation(产品状态)')

    total_in = 0
    for sn in wb.sheetnames:
        if sn in SKIP_SHEETS: continue
        ws = wb[sn]
        header_row, header_cols = find_header_row(ws)
        if not header_row:
            print(f'  ⚠️ {sn}: 未找到表头, 跳过')
            continue

        # 反向 map:列下标 → 列名
        col_by_idx = {idx: name for name, idx in header_cols.items()}
        n = 0
        rows_to_insert = []
        for r in ws.iter_rows(min_row=header_row + 1, values_only=True):
            if not r or not any(x for x in r if x is not None and str(x).strip()):
                continue
            std = {k: None for k in STD_COLS}
            extras = {}
            for j, val in enumerate(r):
                if val is None or val == '': continue
                col_name = col_by_idx.get(j)
                if not col_name: continue
                std_name = ALIAS.get(col_name)
                if std_name:
                    # 价格转 REAL
                    if std_name == '价格_元':
                        if std[std_name] is not None: continue  # 已经填了不覆盖
                        try:
                            std[std_name] = float(str(val).replace('（一口价）','').replace(',','').strip())
                        except (ValueError, TypeError):
                            extras[col_name] = str(val)
                    else:
                        if std[std_name]: continue
                        std[std_name] = str(val).strip()
                else:
                    extras[col_name] = str(val).strip()

            # 跳过完全空行
            if not std['产品名称'] and not std['型号']:
                continue

            rows_to_insert.append((
                PERIOD, sn,
                std['分类'], std['一级目录'], std['二级目录'],
                std['产品名称'], std['型号'], std['物料号'],
                std['价格_元'], std['产品状态'],
                std['功能概述'], std['云商链接'],
                json.dumps(extras, ensure_ascii=False) if extras else None,
            ))
            n += 1

        if rows_to_insert:
            cur.executemany("""
                INSERT INTO dahua_quotation
                  (报价日期, 板块, 分类, 一级目录, 二级目录, 产品名称, 型号, 物料号,
                   价格_元, 产品状态, 功能概述, 云商链接, 规格JSON, 导入时间)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, rows_to_insert)
        print(f'  ✓ {sn:30s} {n} 行')
        total_in += n

    conn.commit()
    after = cur.execute('SELECT COUNT(*) FROM dahua_quotation WHERE 报价日期 = ?', (PERIOD,)).fetchone()[0]
    print(f'\n✅ 大华报价单入库 {total_in} 行 / 库内 {after} 行')


def load_competitor(conn):
    print(f'\n══ 服务商报价单 {FP_COMPETITOR} ══')
    wb = openpyxl.load_workbook(FP_COMPETITOR, read_only=True, data_only=True)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS competitor_quotation (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            报价日期     TEXT NOT NULL,
            来源服务商   TEXT,
            sheet名      TEXT NOT NULL,
            分组         TEXT,
            品牌         TEXT,
            产品名称     TEXT,
            型号         TEXT,
            面价_元      REAL,
            批发价_元    REAL,
            用户价_元    REAL,
            价格_元      REAL,
            规格JSON     TEXT,
            原始JSON     TEXT,
            导入时间     TEXT
        );
        """)
    cur.execute('DELETE FROM competitor_quotation WHERE 报价日期 = ?', ('2026-05-12',))
    cur.execute('CREATE INDEX IF NOT EXISTS idx_cq_brand ON competitor_quotation(品牌)')

    # ── Sheet 1: 监控类产品(海康/水星/萤石/TP/网线)──
    # 它是 4 块拼接(每块 2 列:产品名 / 价格),需要按列对分块
    ws = wb['监控类产品']
    rows = list(ws.iter_rows(values_only=True))

    # 第 1 行是分组标题(海康POE / 无线4G / 路由器 / 网线)
    # 第 2 行起每 2 列一组(品名, 价格)
    group_titles = []
    for j in range(0, ws.max_column, 2):
        title = rows[0][j] if j < len(rows[0]) else None
        group_titles.append(str(title).strip()[:50] if title else '')

    n1 = 0
    inserted = []
    for r in rows[1:]:
        for grp_i, j in enumerate(range(0, ws.max_column, 2)):
            name = r[j] if j < len(r) else None
            price = r[j+1] if j+1 < len(r) else None
            if not name: continue
            if isinstance(name, str) and not name.strip(): continue
            # 猜品牌
            grp_title = group_titles[grp_i]
            brand = ''
            for b in ['海康', '水星', '萤石', 'TP', '康普', 'TP-LINK', '水钻','嗨浪']:
                if b in str(name) or b in grp_title:
                    brand = b
                    break
            try:
                price_v = float(str(price).replace('元一米', '').strip()) if price is not None else None
            except (ValueError, TypeError):
                price_v = None
            inserted.append((
                '2026-05-12', None, '监控类产品', grp_title, brand,
                str(name).strip()[:200], None,
                None, None, None,
                price_v,
                None,
                json.dumps({'原始价格字段': str(price) if price is not None else None}, ensure_ascii=False),
            ))
            n1 += 1

    # ── Sheet 2: TP-LINK 域联(70 行,三层价)──
    ws2 = wb['TP-LINK域联产品价格表']
    rows2 = list(ws2.iter_rows(values_only=True))
    # 表头在 R3(R1=标题, R2=企业路由分组)
    hdr_idx = None
    for i, r in enumerate(rows2[:5]):
        if r and '型号' in [str(c).strip() if c else '' for c in r]:
            hdr_idx = i; break

    n2 = 0
    if hdr_idx is not None:
        hdr = {str(c).strip() if c else '': j for j, c in enumerate(rows2[hdr_idx])}
        for r in rows2[hdr_idx + 1:]:
            if not r or not r[hdr.get('型号', 1)]: continue
            model = str(r[hdr['型号']]).strip()
            if not model or '类型' in model: continue
            def _num(idx_name):
                v = r[hdr[idx_name]] if hdr.get(idx_name) is not None and hdr[idx_name] < len(r) else None
                try: return float(v) if v is not None else None
                except: return None
            inserted.append((
                '2026-05-12', None, 'TP-LINK域联产品', '企业路由', 'TP-LINK',
                str(r[hdr.get('类型', 0)] or '').strip()[:200], model,
                _num('面价'), _num('批发价'), _num('用户价'), None,
                json.dumps({
                    '端口数量': r[hdr['端口数量']] if hdr.get('端口数量') else None,
                    '带机量':   r[hdr['带机量']] if hdr.get('带机量') else None,
                    '支持带宽': r[hdr['支持带宽']] if hdr.get('支持带宽') else None,
                    'POE':      r[hdr.get('POE', -1)] if hdr.get('POE') is not None and hdr['POE'] < len(r) else None,
                    '官网链接': r[hdr['官网链接']] if hdr.get('官网链接') else None,
                }, ensure_ascii=False, default=str),
                None,
            ))
            n2 += 1

    cur.executemany("""
        INSERT INTO competitor_quotation
          (报价日期, 来源服务商, sheet名, 分组, 品牌, 产品名称, 型号,
           面价_元, 批发价_元, 用户价_元, 价格_元, 规格JSON, 原始JSON)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, inserted)
    conn.commit()
    print(f'  ✓ 监控类产品 {n1} 行')
    print(f'  ✓ TP-LINK域联 {n2} 行')
    after = cur.execute("SELECT COUNT(*) FROM competitor_quotation WHERE 报价日期 = '2026-05-12'").fetchone()[0]
    print(f'\n✅ 服务商报价单入库 {n1 + n2} 行 / 库内 {after} 行')


def main():
    conn = sqlite3.connect(DB)
    load_dahua(conn)
    load_competitor(conn)

    # ── 校验 ──
    print('\n═══ 校验 ═══')
    print('\n大华报价单 按板块:')
    for board, n in conn.execute('SELECT 板块, COUNT(*) FROM dahua_quotation GROUP BY 1 ORDER BY 2 DESC').fetchall():
        print(f'  {board:25s} {n} 行')

    print('\n大华产品状态分布:')
    for st, n in conn.execute('SELECT 产品状态, COUNT(*) FROM dahua_quotation WHERE 产品状态 IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12').fetchall():
        print(f'  {st:15s} {n}')

    print('\n大华价格区间(三大专项):')
    for sn in ['夜视王专项', '场景化专项', '无线专项']:
        rs = conn.execute("""
            SELECT MIN(价格_元), MAX(价格_元), ROUND(AVG(价格_元), 0), COUNT(价格_元)
              FROM dahua_quotation WHERE 板块 = ? AND 价格_元 IS NOT NULL
        """, (sn,)).fetchone()
        print(f'  {sn:10s} 价格范围 ¥{rs[0]:.0f} ~ ¥{rs[1]:.0f}, 均 ¥{rs[2]:.0f}, 有价 {rs[3]} 条')

    print('\n服务商报价单 按品牌:')
    for b, n in conn.execute('SELECT 品牌, COUNT(*) FROM competitor_quotation WHERE 品牌 != "" GROUP BY 1 ORDER BY 2 DESC').fetchall():
        print(f'  {b:15s} {n}')

    print('\n海康 vs 大华 关键 SKU 对比示例(前 5):')
    print('海康(服务商报价):')
    for r in conn.execute("""
        SELECT 产品名称, 价格_元 FROM competitor_quotation
         WHERE 品牌 = '海康' AND 价格_元 IS NOT NULL ORDER BY 价格_元 LIMIT 5
    """).fetchall():
        print(f'  {r[0][:50]:50s} ¥{r[1]}')

    conn.close()


if __name__ == '__main__':
    main()
