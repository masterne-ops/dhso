#!/usr/bin/env python3
"""前期 GTM 跑动历史(任务推广列表导出)→ gtm_target,让冷却期对已跑客户生效。

源: 「任务推广列表_*.xlsx」(大华任务系统导出,16列):
  任务编码/任务名称/推广标签(夜视王|无线)/任务责任人/任务下发时间/推广责任人/
  计划完成时间/状态/推广客户编码/推广客户名称/...

映射 → gtm_target(UNIQUE(任务年月,客户编码), INSERT OR IGNORE 不覆盖现有名单):
  任务年月   = 任务下发时间[:7]
  分销经理   = 推广责任人 第一个人名(去工号括号)
  推荐产品类 = 推广标签
  推荐依据   = 历史任务:{任务名称}({状态})
  来源       = '历史导入(任务推广列表)'
  城市/区县/所属代理商 = provider_contract 按客户编码回填

冷却: _gtm.cooling_codes(下月) 取前 2 个月 gtm_target 客户 → 导入后近两月已跑客户
自动进入冷却,不会被重复推荐。

用法:
  python load_gtm_history.py --files a.xlsx b.xlsx           # dry-run(按下发时间归月,增量)
  python load_gtm_history.py --files a.xlsx b.xlsx --apply
  # 把文件整体作为某月的正式名单(先清空该月,再全量导入;下发时间仅留档):
  python load_gtm_history.py --files 7月任务.xlsx --force-month 2026-07 --replace --apply
"""
import re
import sqlite3
import argparse
from datetime import datetime

import pandas as pd

DB = '/opt/so-data-analytics/db/product_flow.db'


def first_name(s):
    """'薛玉飞(15643),黄在洪(3...' → '薛玉飞'"""
    if not s or pd.isna(s):
        return '(历史任务)'
    m = re.match(r'^([^,，(（]+)', str(s).strip())
    return m.group(1).strip() if m else str(s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', nargs='+', required=True)
    ap.add_argument('--force-month', help='把文件全部行强制归入该任务月(如 2026-07),忽略下发时间归月')
    ap.add_argument('--replace', action='store_true', help='配合 --force-month:先清空该月现有名单再导入')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if a.replace and not a.force_month:
        raise SystemExit('--replace 必须配合 --force-month 使用')

    frames = [pd.read_excel(f) for f in a.files]
    df = pd.concat(frames, ignore_index=True).dropna(how='all')
    df = df[df['推广客户编码'].notna()]
    if a.force_month:
        df['任务年月'] = a.force_month
    else:
        df['任务年月'] = pd.to_datetime(df['任务下发时间'], errors='coerce').dt.strftime('%Y-%m')
    df['分销经理'] = df['推广责任人'].map(first_name)
    # 同(月,客户)多任务行去重:保留信息更全的第一条
    df = df.drop_duplicates(subset=['任务年月', '推广客户编码'])
    print(f"文件合计 {sum(len(x) for x in frames)} 行 → 去重后 {len(df)} 条(月,客户)")
    print("按任务年月:", df['任务年月'].value_counts().sort_index().to_dict())
    print("推广标签:", df['推广标签'].value_counts().to_dict())

    conn = sqlite3.connect(DB)
    # 回填维度
    prov = pd.read_sql("SELECT 客户编码, 客户城市, 客户区县, 上级分销商名称 FROM provider_contract "
                       "WHERE 客户编码 IS NOT NULL", conn).drop_duplicates('客户编码')
    df = df.merge(prov, left_on='推广客户编码', right_on='客户编码', how='left')
    n_dim = df['客户城市'].notna().sum()
    print(f"维度回填(命中签约表): {n_dim}/{len(df)}")

    if a.replace:
        n_old = conn.execute("SELECT COUNT(*) FROM gtm_target WHERE 任务年月=?",
                             (a.force_month,)).fetchone()[0]
        print(f"--replace: 将清空 {a.force_month} 现有名单 {n_old} 行(含系统推荐/历史导入)")
    exist = set((r[0], str(r[1])) for r in conn.execute("SELECT 任务年月, 客户编码 FROM gtm_target"))
    dup = sum(1 for _, r in df.iterrows() if (r['任务年月'], str(r['推广客户编码'])) in exist)
    print(f"已在 gtm_target(将跳过{'—replace 模式下先清空不冲突' if a.replace else ''}): {dup}")

    if not a.apply:
        print("[DRY-RUN] 加 --apply 写入。")
        conn.close()
        return

    if a.replace:
        conn.execute("DELETE FROM gtm_target WHERE 任务年月=?", (a.force_month,))
        conn.commit()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    src_label = '任务推广列表(官方任务)' if a.force_month else '历史导入(任务推广列表)'
    batch = []
    for _, r in df.iterrows():
        batch.append((
            r['任务年月'], r['分销经理'], str(r['推广客户编码']),
            str(r['推广客户名称'] or ''), r.get('客户城市'), r.get('客户区县'),
            r.get('上级分销商名称'), str(r['推广标签'] or ''),
            f"官方任务:{r['任务名称']}({r['状态']},下发{str(r['任务下发时间'])[:10]})",
            src_label,
            str(r['任务下发时间'])[:10] if pd.notna(r['任务下发时间']) else now,
        ))
    cur = conn.executemany("""
        INSERT OR IGNORE INTO gtm_target
        (任务年月, 分销经理, 客户编码, 客户名称, 城市, 区县, 所属代理商, 推荐产品类, 推荐依据, 来源, 分配时间)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", batch)
    conn.commit()
    print(f"✅ 实插 {cur.rowcount} / 跳过 {len(batch) - cur.rowcount}(已在名单)")
    for r in conn.execute("SELECT 任务年月, 来源, COUNT(*) FROM gtm_target GROUP BY 1,2 ORDER BY 1"):
        print("  ", r)
    conn.close()


if __name__ == '__main__':
    main()
