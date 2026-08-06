#!/usr/bin/env python3
"""代理商进货明细入库 dealer_purchase

源:工作簿1.xlsx
  Sheet1 = 2025 全年(89120 行,有区县+计返利)
  Sheet2 = 2026 1-3月(20477 行,无区县+计奖励)

设计:
  - 两 sheet 字段差异 → 统一列(区县/计返利 Sheet2 空;计奖励 Sheet1 空)
  - 全量入库(含负值退货/折扣行,不去重 — 进货明细每行是一笔流水)
  - 派生「下单客户类型」:JOIN dealer_si_snapshot / provider_contract 判定
      一级代理商 / 服务商(赠品) / 其他
  - 派生「月份」便于按月分析
"""
import sqlite3
import openpyxl
from datetime import datetime

DB = '/opt/so-data-analytics/db/product_flow.db'
FP = '/tmp/工作簿1.xlsx'

# Sheet1 列序(17 列)
S1 = {'年份':0,'日期':1,'二级办':2,'业务员':3,'下单客户':4,'区县':5,'行业一级':6,
      '产品一级':7,'产品二级':8,'产品三级':9,'产品四级':10,'内部型号':11,'外部型号':12,
      '实销万':13,'实发数量':14,'计返利':15,'计销售':16}
# Sheet2 列序(16 列,无区县,15=计销售 16=计奖励)
S2 = {'年份':0,'日期':1,'二级办':2,'业务员':3,'下单客户':4,'行业一级':5,
      '产品一级':6,'产品二级':7,'产品三级':8,'产品四级':9,'内部型号':10,'外部型号':11,
      '实销万':12,'实发数量':13,'计销售':14,'计奖励':15}

# SMB 补充名单 — 不在 dealer_si_snapshot 签约快照里,但用户人工确认属 SMB 一级代理商
# (25年在做、26年流失的;或公司改名的)。每次标记后强制标为「一级代理商」。
# 维护:新增确认的 SMB 代理商往这里加。
SMB_SUPPLEMENT = [
    '浙江名硕建设工程有限公司',       # 25 做 26 停(确认 2026-05-29)
    '浙江歌呈智能科技有限公司',       # 25 做 26 停
    '微空间网络科技（湖州）有限公司',  # 微空间 2025 名
    '湖州微空间网络科技有限公司',     # 微空间 2026 改名(同一家)
]


def month_of(d):
    s = str(d or '')
    if '月' in s:
        try: return int(s.split('月')[0])
        except: return None
    return None


def num(v):
    if v is None or v == '': return None
    try: return float(str(v).replace(',','').strip())
    except: return None


def s(v):
    if v is None: return None
    return str(v).strip()


def main():
    wb = openpyxl.load_workbook(FP, read_only=True, data_only=True)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dealer_purchase (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            数据年份 INTEGER,
            月份 INTEGER,
            日期原文 TEXT,
            业绩归属二级办 TEXT,
            业务员姓名 TEXT,
            下单客户 TEXT,
            下单客户类型 TEXT,
            下单客户区县 TEXT,
            行业一级 TEXT,
            产品一级 TEXT, 产品二级 TEXT, 产品三级 TEXT, 产品四级 TEXT,
            内部型号 TEXT, 外部型号 TEXT,
            实销万 REAL, 实发数量 REAL,
            计返利 REAL, 计销售 REAL, 计奖励 REAL,
            导入时间 TEXT
        )
    """)
    cur.execute('DELETE FROM dealer_purchase')
    for idx in ['idx_dp_year','idx_dp_client','idx_dp_type','idx_dp_sales','idx_dp_model']:
        pass
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dp_year ON dealer_purchase(数据年份)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dp_client ON dealer_purchase(下单客户)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dp_type ON dealer_purchase(下单客户类型)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dp_model ON dealer_purchase(内部型号)')

    cols = ['数据年份','月份','日期原文','业绩归属二级办','业务员姓名','下单客户','下单客户类型',
            '下单客户区县','行业一级','产品一级','产品二级','产品三级','产品四级',
            '内部型号','外部型号','实销万','实发数量','计返利','计销售','计奖励','导入时间']
    ph = ','.join('?'*len(cols))
    insert = f"INSERT INTO dealer_purchase ({','.join(cols)}) VALUES ({ph})"
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    total = 0
    for sn, M in [('Sheet1', S1), ('Sheet2', S2)]:
        ws = wb[sn]; it = ws.iter_rows(values_only=True); next(it)
        batch = []
        for r in it:
            if not r or not r[M['下单客户']]: continue
            batch.append((
                int(r[M['年份']]) if r[M['年份']] else None,
                month_of(r[M['日期']]),
                s(r[M['日期']]),
                s(r[M['二级办']]), s(r[M['业务员']]), s(r[M['下单客户']]),
                None,  # 下单客户类型,后面 UPDATE
                s(r[M['区县']]) if '区县' in M else None,
                s(r[M['行业一级']]),
                s(r[M['产品一级']]), s(r[M['产品二级']]), s(r[M['产品三级']]), s(r[M['产品四级']]),
                s(r[M['内部型号']]), s(r[M['外部型号']]),
                num(r[M['实销万']]), num(r[M['实发数量']]),
                num(r[M['计返利']]) if '计返利' in M else None,
                num(r[M['计销售']]),
                num(r[M['计奖励']]) if '计奖励' in M else None,
                now,
            ))
            if len(batch) >= 5000:
                cur.executemany(insert, batch); total += len(batch); batch = []
        if batch:
            cur.executemany(insert, batch); total += len(batch)
        print(f'  ✓ {sn} 入库')
    conn.commit()
    print(f'✅ 总入库 {total:,} 行')

    # ── 派生「下单客户类型」──
    # 一级代理商:命中 dealer_si_snapshot.客户名称
    cur.execute("""
        UPDATE dealer_purchase SET 下单客户类型 = '一级代理商'
         WHERE 下单客户 IN (SELECT DISTINCT "客户名称（无@办事处）" FROM dealer_si_snapshot)
    """)
    n_dealer = cur.rowcount
    # SMB 补充名单(人工确认)→ 强制一级代理商
    if SMB_SUPPLEMENT:
        ph2 = ','.join('?' * len(SMB_SUPPLEMENT))
        cur.execute(f"UPDATE dealer_purchase SET 下单客户类型='一级代理商' WHERE 下单客户 IN ({ph2})",
                    SMB_SUPPLEMENT)
        n_supplement = cur.rowcount
    else:
        n_supplement = 0
    # 服务商:剩余里命中 provider_contract.客户名称
    cur.execute("""
        UPDATE dealer_purchase SET 下单客户类型 = '服务商'
         WHERE 下单客户类型 IS NULL
           AND 下单客户 IN (SELECT DISTINCT 客户名称 FROM provider_contract)
    """)
    n_provider = cur.rowcount
    # 其他
    cur.execute("UPDATE dealer_purchase SET 下单客户类型 = '其他' WHERE 下单客户类型 IS NULL")
    n_other = cur.rowcount
    conn.commit()
    print(f'\n标记:一级代理商 {n_dealer:,} 行(+补充名单 {n_supplement:,} 行)/ 服务商 {n_provider:,} 行 / 其他 {n_other:,} 行')

    # ── 校验:按类型 × 年份 ──
    print('\n═══ 下单客户类型 × 年份(行数 / 实销万 / 客户数)═══')
    for r in cur.execute("""
        SELECT 数据年份, 下单客户类型,
               COUNT(*) AS 行数,
               ROUND(SUM(实销万), 1) AS 实销万,
               COUNT(DISTINCT 下单客户) AS 客户数
          FROM dealer_purchase GROUP BY 1, 2 ORDER BY 1, 4 DESC
    """).fetchall():
        print(f'  {r[0]} {r[1]:8s} 行 {r[2]:>7,} / 实销 {r[3]:>9,.1f} 万 / 客户 {r[4]}')

    print('\n═══ "服务商赠品"样本(被排除的)top 10 ═══')
    for r in cur.execute("""
        SELECT 下单客户, COUNT(*), ROUND(SUM(实销万),1)
          FROM dealer_purchase WHERE 下单客户类型='服务商'
         GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """).fetchall():
        print(f'  {str(r[0])[:35]:35s} {r[1]:>5} 行 / {r[2]:>7,.1f} 万')

    print('\n═══ "其他"样本(省外/个人,需你判断)top 10 ═══')
    for r in cur.execute("""
        SELECT 下单客户, COUNT(*), ROUND(SUM(实销万),1)
          FROM dealer_purchase WHERE 下单客户类型='其他'
         GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """).fetchall():
        print(f'  {str(r[0])[:35]:35s} {r[1]:>5} 行 / {r[2]:>7,.1f} 万')

    conn.close()


if __name__ == '__main__':
    main()
