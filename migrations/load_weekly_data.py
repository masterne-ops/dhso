#!/usr/bin/env python3
"""周更新数据入库 — 4 个 Excel 文件按策略入 product_flow.db

策略:
  1. install_redpack  — INSERT OR IGNORE(按 产品序列号 去重)
  2. visit_record     — INSERT OR IGNORE(按 活动编号 去重)
  3. product_flow     — INSERT OR IGNORE(按 ID 去重,unique idx 已存在)
  4. provider_contract — DELETE 全表 + INSERT 全量(快照型)
"""
import sqlite3
import openpyxl
import sys
from datetime import datetime

DB = '/opt/so-data-analytics/db/product_flow.db'

FILES = {
    'install_redpack':    ('/tmp/安装红包明细清单表_0517-0523.xlsx', 'sheet1', 1, '产品序列号', 'INSERT_IGNORE'),
    'visit_record':       ('/tmp/拜访活动明细表_0517-0524.xlsx',     'sheet1', 1, '活动编号',   'INSERT_IGNORE'),
    'product_flow':       ('/tmp/产品流向0517-0524.xlsx',             'sheet1', 1, 'ID',         'INSERT_IGNORE'),
    'provider_contract':  ('/tmp/服务商签约明细表_0524.xlsx',         'sheet1', 2, None,         'REPLACE_ALL'),
}


def read_excel(fp, sheet, header_row):
    """读 Excel,返回 (列名 list, 行 list)"""
    wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c else None for c in rows[header_row - 1]]
    data_rows = rows[header_row:]
    return header, data_rows


def get_db_cols(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]


def to_cell(v):
    """转 SQLite 兼容的值"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v


def load_table(conn, table, file_info):
    fp, sheet, header_row, _, strategy = file_info
    print(f'\n══ {table} ({strategy}) ══')
    print(f'  读取 {fp}')
    header, data_rows = read_excel(fp, sheet, header_row)
    print(f'  Excel: {len(header)} 列, {len(data_rows)} 行')

    db_cols = get_db_cols(conn, table)
    print(f'  DB 表: {len(db_cols)} 列')

    # 列对齐:用 Excel header 跟 DB 列的交集
    col_map = []  # [(excel_idx, db_col_name)]
    for i, h in enumerate(header):
        if h and h in db_cols:
            col_map.append((i, h))

    print(f'  对齐列: {len(col_map)} / {len(header)}')
    missing = [h for h in header if h and h not in db_cols]
    extra_db = [c for c in db_cols if c not in header]
    if missing:
        print(f'  ⚠️ Excel 有但 DB 没有(忽略): {missing[:5]}{"..." if len(missing)>5 else ""}')
    if extra_db:
        print(f'  📝 DB 有但 Excel 没(留 NULL): {extra_db[:5]}{"..." if len(extra_db)>5 else ""}')

    # 准备 SQL
    db_col_names = [c for _, c in col_map]
    col_list_sql = ', '.join(f'"{c}"' for c in db_col_names)
    placeholders = ', '.join(['?'] * len(col_map))

    if strategy == 'REPLACE_ALL':
        before = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        conn.execute(f'DELETE FROM {table}')
        insert_sql = f'INSERT INTO {table} ({col_list_sql}) VALUES ({placeholders})'
        print(f'  🗑️  DELETE 旧数据 ({before} 行)')
    else:
        insert_sql = f'INSERT OR IGNORE INTO {table} ({col_list_sql}) VALUES ({placeholders})'

    # 转换行
    batch = []
    inserted = 0
    skipped = 0
    for r in data_rows:
        if not any(r):
            continue
        vals = tuple(to_cell(r[i]) if i < len(r) else None for i, _ in col_map)
        batch.append(vals)
        if len(batch) >= 1000:
            cur = conn.executemany(insert_sql, batch)
            inserted += cur.rowcount
            skipped += len(batch) - cur.rowcount
            batch = []
    if batch:
        cur = conn.executemany(insert_sql, batch)
        inserted += cur.rowcount
        skipped += len(batch) - cur.rowcount

    conn.commit()
    after = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f'  ✅ 插入 {inserted} 行 / 跳过(重复) {skipped} 行 / 表总数 {after}')
    return inserted, skipped, after


def main():
    conn = sqlite3.connect(DB)
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('BEGIN')
    try:
        for table, info in FILES.items():
            load_table(conn, table, info)
        # 提交在 load_table 里已 commit
        print('\n═══ 全部完成 ═══')
    except Exception as e:
        conn.rollback()
        print(f'\n❌ 失败:{e}')
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
