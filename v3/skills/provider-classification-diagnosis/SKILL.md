# SKILL: provider-classification-diagnosis — 服务商分类诊断（page 02）

> ⭐ **触发场景**：用户在 page 02「🧭 服务商智能分类」点了「🤖 AI 智能诊断」按钮。
>
> **业务员问的就两个问题**：
> 1. 本周期内，**哪些重要客户没跑动**？（漏跑 — 失血点）
> 2. **哪些跑动是没有效果的**？（无效跑动 — 资源浪费）
>
> 报告就回答这两问 + 一段「本周该做的事」。**直指问题，不堆数据**。

---

## 〇、必读前置

```
Read /sandbox/skills/system-overview/SKILL.md   ← 第一步必做
Read /sandbox/skills/provider-classification-diagnosis/SKILL.md   ← 本文档
```

---

## 一、设计哲学

### 1.1 业务员要的是「该干什么」，不是「数据全景」

❌ 不做：5 类 KPI 卡 + 健康度评分 + 业务员/区县/代理商三维大表
✅ 只做：**两个问题 + 三件该做的事**

### 1.2 简单 ≠ 简陋

**简单** = 报告里**每一行数据**都直接对应某个**待办动作**。
看到「杭州 XX 科技 流失风险 30 万 距今 75 天」→ 业务员立刻知道这单要救援。

**简陋** = 只给汇总数字（"60 家漏跑"），让用户自己去翻明细。**不要**。

### 1.3 HTML 不是装饰，是**结构化**

用 HTML 是因为：
- 表格里能**用颜色标重点**（货值高的、超期长的）
- **图文混排**：每张表前面有一句话定位"这是什么 / 该看什么"
- **可双击离线打开** — 业务员发给同事看不用登录系统

---

## 二、本页面在干什么（背景知识）

### 2.1 RFM 5 类（MECE）— 业务铁律

| 类别 | 判定 | 业务含义 |
|------|------|----------|
| 🏆 **核心活跃** | R ≤ 60 天 AND (F ≥ 6 OR M ≥ 1万) | 频次或货值高 + 最近还在采购 |
| ⚠️ **流失风险** | R > 60 天 AND (F ≥ 6 OR M ≥ 1万) | 曾是好客户，最近 2 月没动 |
| 🌱 **培育客户** | R ≤ 60 天 AND F < 6 AND M < 1万 | 最近在采购但量小 |
| 🪦 **已流失** | R > 60 天 AND F < 6 AND M < 1万 | 量小 + 长期不动 |
| ❓ **未采购** | 红包扫码 0 条 | 完全没采购历史 |

阈值默认：R=60d / F=6 / M=10000。**口径：红包扫码**（`install_redpack.产品现有分销价`）。

### 2.2 5 张派单表 → 映射到 2 个问题

| 表 | 映射到 |
|----|--------|
| 🚨 紧急派单（流失风险 + 超 14 天）| **Q1 漏跑** — 最紧急的子集 |
| ⚠️ 核心客户漏跑（核心活跃 + 超 30 天）| **Q1 漏跑** |
| 💸 资源错配（已流失 + 频繁拜访）| **Q2 无效** — 跑错对象 |
| 🔁 人员重复（多业务员撞同一户）| **Q2 无效** — 协作问题 |
| 🌱 新客成长池 | **不在诊断范围**（培育是长期工作，本诊断不展开）|

---

## 三、数据策略

### 3.1 Context 给什么（不再算）

prompt 开头的 `【上下文】` 块已经有：

| 字段 | 用法 |
|------|------|
| `filters`（城市/区县/时间窗/阈值）| **WHERE 子句** — 所有 SQL 套上 |
| `distribution`（5 类总数）| 不直接展示 — 只用来在结论里说"X 家有红包客户中" |
| `dispatch_tables.{表}.数量`| 报告里"涉 N 家"直接引用 |

### 3.2 DB 必须查（context 没给）

| 必查 | 用途 |
|------|------|
| 漏跑客户**名单**（流失风险 + 核心活跃 合并 TOP 20）| Q1 主表 |
| 每个漏跑客户的**累计货值** | 表里展示 |
| 每个漏跑客户的**应负责业务员**（join `salesperson_scope`）| 行动建议里点名 |
| 资源错配**名单**（已流失 + 频繁拜访 TOP 10）| Q2.A 主表 |
| 资源错配 - 每个客户的**主要拜访人** | Q2.A 行动建议 |
| 人员重复**名单**（多业务员撞户 TOP 10）| Q2.B 主表 |
| 人员重复 - **业务员列表**（join visit_record_v 拿名字）| Q2.B 协作分工 |

### 3.3 信号铁律

context 是结论数；自己 SQL 算的数对不上 → **信 context**（V2 公司标准口径）。
你的 SQL 只用来**拿名单**、不是质疑数字。

---

## 四、SQL 模板（按需取用）

> ⚠️ 所有 SQL 必须套 context.filters（地市/区县/时间窗）。下面用 `:city` 表示占位。

### A. Q1 - 重要客户漏跑 TOP 20（合并：流失风险 + 核心活跃）

```sql
WITH asof AS (SELECT MAX(上线时间) AS d FROM install_redpack),
     -- 客户 RFM
     rfm AS (
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户地市) AS 城市, MAX(上线客户区县) AS 区县,
               MAX(所属一级客户) AS 代理商,
               julianday((SELECT d FROM asof)) - julianday(MAX(上线时间)) AS R,
               COUNT(DISTINCT date(上线时间)) FILTER (
                 WHERE 上线时间 >= datetime((SELECT d FROM asof), '-365 day')
               ) AS F,
               SUM(产品现有分销价) FILTER (
                 WHERE 上线时间 >= datetime((SELECT d FROM asof), '-365 day')
               ) AS M
          FROM install_redpack
         WHERE 上线客户地市 IN (:cities)   -- ← context.filters.城市
         GROUP BY 上线客户编码
     ),
     -- 分类（只看 流失风险 / 核心活跃 — 高价值客户）
     classified AS (
        SELECT *, CASE
                    WHEN R > 60 AND (F >= 6 OR M >= 10000) THEN '⚠️ 流失风险'
                    WHEN R <= 60 AND (F >= 6 OR M >= 10000) THEN '🏆 核心活跃'
                    ELSE NULL
                  END AS RFM类别
          FROM rfm
     ),
     hv AS (SELECT * FROM classified WHERE RFM类别 IS NOT NULL),
     -- 最近拜访（用 visit_record_v 过滤异常打卡）
     lv AS (
        SELECT 客户编码, MAX(拜访时间_修正) AS 最近拜访时间
          FROM visit_record_v
         WHERE _打卡异常无效 = 0 AND _真异常打卡 = 0
         GROUP BY 客户编码
     ),
     -- 应负责业务员（按区县匹配 salesperson_scope；可能多个业务员）
     sp_map AS (
        SELECT s.市, s.区县, GROUP_CONCAT(DISTINCT s.业务员) AS 应负责业务员
          FROM salesperson_scope s
         GROUP BY s.市, s.区县
     )
SELECT hv.客户名称, hv.客户编码, hv.城市, hv.区县, hv.代理商,
       hv.RFM类别,
       ROUND(hv.M/10000.0, 2) AS 累计货值_万,
       CAST(hv.R AS INT) AS R_天,
       lv.最近拜访时间,
       CAST(julianday('now') - julianday(lv.最近拜访时间) AS INT) AS 距今拜访天数,
       sp_map.应负责业务员
  FROM hv
  LEFT JOIN lv ON lv.客户编码 = hv.客户编码
  LEFT JOIN sp_map ON sp_map.市 = hv.城市 AND sp_map.区县 = hv.区县
 WHERE -- 漏跑判定：
       -- 流失风险 → 超 14 天（risk_th）；核心活跃 → 超 30 天（cham_th）
       (hv.RFM类别 = '⚠️ 流失风险' AND
            (lv.最近拜访时间 IS NULL OR julianday('now') - julianday(lv.最近拜访时间) > 14))
    OR (hv.RFM类别 = '🏆 核心活跃' AND
            (lv.最近拜访时间 IS NULL OR julianday('now') - julianday(lv.最近拜访时间) > 30))
 ORDER BY hv.M DESC
 LIMIT 20;
```

### B. Q2.A - 资源错配（已流失客户被频繁拜访）

```sql
WITH asof AS (SELECT MAX(上线时间) AS d FROM install_redpack),
     lost AS (
        SELECT 上线客户编码 AS 客户编码,
               MAX(上线客户名称) AS 客户名称,
               MAX(上线客户地市) AS 城市, MAX(上线客户区县) AS 区县,
               MAX(所属一级客户) AS 代理商,
               ROUND(SUM(产品现有分销价)/10000.0, 2) AS 累计货值_万,
               CAST(julianday((SELECT d FROM asof)) - julianday(MAX(上线时间)) AS INT) AS R_天
          FROM install_redpack
         WHERE 上线客户地市 IN (:cities)
         GROUP BY 上线客户编码
        HAVING julianday((SELECT d FROM asof)) - julianday(MAX(上线时间)) > 60
           AND COUNT(DISTINCT date(上线时间)) < 6
           AND SUM(产品现有分销价) < 10000
     ),
     -- 窗口内拜访（默认 90 天）
     vc AS (
        SELECT 客户编码,
               COUNT(*) AS 拜访次数,
               GROUP_CONCAT(DISTINCT 打卡人姓名) AS 主要拜访人
          FROM visit_record_v
         WHERE _打卡异常无效 = 0 AND _真异常打卡 = 0
           AND 拜访时间_修正 >= datetime('now', '-90 day')   -- ← mtx_window_days
         GROUP BY 客户编码
        HAVING COUNT(*) >= 3                                 -- ← mtx_high_threshold
     )
SELECT l.客户名称, l.客户编码, l.城市, l.区县, l.代理商,
       l.累计货值_万, l.R_天,
       vc.拜访次数, vc.主要拜访人
  FROM lost l
  JOIN vc ON vc.客户编码 = l.客户编码
 ORDER BY vc.拜访次数 DESC, l.累计货值_万 ASC   -- 拜访多 + 货值低 = 最浪费
 LIMIT 10;
```

### C. Q2.B - 人员重复（多业务员撞同一户）

```sql
WITH v30 AS (
    SELECT 客户编码,
           COUNT(*) AS 拜访次数,
           COUNT(DISTINCT 打卡人姓名) AS 业务员人数,
           GROUP_CONCAT(DISTINCT 打卡人姓名) AS 涉及业务员
      FROM visit_record_v
     WHERE _打卡异常无效 = 0 AND _真异常打卡 = 0
       AND 拜访时间_修正 >= datetime('now', '-30 day')
       AND 客户编码 IN (
            SELECT DISTINCT 客户编码 FROM visit_record_v
             WHERE 拜访客户城市 IN (:cities)   -- ← context.filters
       )
     GROUP BY 客户编码
    HAVING COUNT(*) >= 3 AND COUNT(DISTINCT 打卡人姓名) >= 2
)
SELECT pp.公司名称 AS 客户名称, v30.客户编码,
       pp.地市 AS 城市, pp.区县,
       v30.拜访次数, v30.业务员人数, v30.涉及业务员
  FROM v30
  LEFT JOIN provider_profile pp ON pp.客户编码 = v30.客户编码
 ORDER BY v30.业务员人数 DESC, v30.拜访次数 DESC
 LIMIT 10;
```

### D. 行动建议数据 — 漏跑按业务员聚合（决定让谁干）

```sql
-- 把 §A 的结果按 应负责业务员 聚合：每个业务员要跑的客户数 / 总货值
SELECT 应负责业务员,
       COUNT(*) AS 待跑客户数,
       SUM(累计货值_万) AS 涉总货值_万,
       GROUP_CONCAT(客户名称, ' / ') AS 客户名单
  FROM ( ... §A SQL 整张表 ... )
 WHERE 应负责业务员 IS NOT NULL
 GROUP BY 应负责业务员
 ORDER BY 涉总货值_万 DESC
 LIMIT 5;   -- TOP 5 业务员
```

---

## 五、报告结构（HTML 单文件）

### 5.1 总体长度目标

**屏幕一屏到一屏半**（PC 上不用滚太多）。所以：
- 标题 + 1 句核心结论
- Q1 漏跑：1 段定位 + 1 张 TOP 20 表
- Q2 无效：2 段定位 + 2 张 TOP 10 表
- 行动 3 条
- 页脚方法论 1 行

### 5.2 HTML 模板

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>服务商分类诊断 — {范围}</title>
<style>
:root {
  --bg: #f8fafc; --card: #ffffff; --border: #e5e7eb; --text: #111827; --muted: #6b7280;
  --risk: #dc2626; --core: #16a34a; --warn: #d97706; --info: #2563eb;
}
* { box-sizing: border-box; }
body {
  font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--text); margin: 0; padding: 24px;
  max-width: 980px; margin-left: auto; margin-right: auto; line-height: 1.6;
}
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; padding-bottom: 6px; border-bottom: 2px solid var(--border); }
h3 { font-size: 14px; margin: 14px 0 6px; color: var(--muted); font-weight: 500; }
.muted { color: var(--muted); font-size: 12px; }
.lead {
  background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--info);
  border-radius: 6px; padding: 14px 18px; margin: 12px 0 20px; font-size: 15px;
}
.lead strong { color: var(--risk); }
.section-lead { color: var(--muted); font-size: 13px; margin: 6px 0 10px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--card); border-radius: 6px; overflow: hidden; }
th, td { padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: left; }
th { background: #f3f4f6; font-weight: 600; font-size: 12px; color: var(--muted); }
tr:hover td { background: #fafbfc; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.hot { color: var(--risk); font-weight: 600; }
td.cool { color: var(--core); }
.tag-risk { display: inline-block; padding: 1px 6px; background: #fee2e2; color: var(--risk); border-radius: 3px; font-size: 11px; }
.tag-core { display: inline-block; padding: 1px 6px; background: #dcfce7; color: var(--core); border-radius: 3px; font-size: 11px; }
.action {
  background: #fffbeb; border-left: 4px solid var(--warn);
  padding: 10px 14px; margin: 8px 0; border-radius: 4px; font-size: 14px;
}
.action b { color: var(--warn); }
.footer { color: var(--muted); font-size: 11px; margin-top: 28px; padding-top: 10px; border-top: 1px solid var(--border); }
</style>
</head>
<body>

<h1>🧭 服务商分类诊断</h1>
<p class="muted">范围：{城市/区县} · 时间窗 {N} 个月 · 生成：{时间}</p>

<!-- 核心结论 一句话 -->
<div class="lead">
  本周期 <strong>{N} 家</strong>重要客户漏跑（涉货值 <strong>{M} 万</strong>），
  另有 <strong>{X} 次</strong>跑动属于无效投入。
  最紧急要处理的是<strong>{业务员}</strong>负责的<strong>{N} 家流失风险客户</strong>。
</div>

<!-- ────────── Q1 ────────── -->
<h2>🚨 哪些重要客户没跑动</h2>
<p class="section-lead">
  「重要客户」指 🏆 核心活跃 + ⚠️ 流失风险（高价值）。
  「没跑动」指流失风险超 {risk_th} 天没拜访 或 核心活跃超 {cham_th} 天没拜访。
</p>

<table>
  <thead><tr>
    <th>客户</th><th>区县</th><th>类别</th>
    <th class="num">累计货值(万)</th><th class="num">距今未拜访(天)</th>
    <th>应负责业务员</th>
  </tr></thead>
  <tbody>
    <!-- 漏跑 TOP 20 行：流失风险标 .tag-risk，核心活跃标 .tag-core -->
    <!-- 货值高的标 .hot；距今天数大的（>60）标 .hot -->
    <tr>
      <td>杭州 XX 安防</td>
      <td>滨江区</td>
      <td><span class="tag-risk">⚠️ 流失风险</span></td>
      <td class="num hot">38.5</td>
      <td class="num hot">75</td>
      <td>张三</td>
    </tr>
    ...
  </tbody>
</table>

<!-- ────────── Q2 ────────── -->
<h2>💸 哪些跑动是没效果的</h2>

<h3>A. 跑错对象 — 在已流失客户上花了太多时间</h3>
<p class="section-lead">
  这些客户已经 R &gt; 60 天 + F &lt; 6 + M &lt; 1万（🪦 已流失），但本季度仍被拜访 ≥ 3 次。
  建议把这部分时间转到 🚨 紧急派单 / ⚠️ 核心客户漏跑 表里的高价值客户。
</p>
<table>
  <thead><tr>
    <th>客户</th><th>区县</th>
    <th class="num">累计货值(万)</th><th class="num">本季拜访(次)</th>
    <th>主要拜访人</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>余杭 XX 商行</td>
      <td>余杭区</td>
      <td class="num">0.3</td>
      <td class="num hot">6</td>
      <td>王五</td>
    </tr>
    ...
  </tbody>
</table>

<h3>B. 人员撞车 — 多个业务员同时跑一个客户</h3>
<p class="section-lead">
  30 天内同一客户被 ≥ 2 个业务员拜访 ≥ 3 次。
  建议明确该客户的主责人，避免重复打扰客户 + 内部资源浪费。
</p>
<table>
  <thead><tr>
    <th>客户</th><th>区县</th>
    <th class="num">拜访次数</th><th class="num">业务员人数</th>
    <th>涉及业务员</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>萧山 YY 电子</td>
      <td>萧山区</td>
      <td class="num">5</td>
      <td class="num hot">3</td>
      <td>李四, 赵六, 钱七</td>
    </tr>
    ...
  </tbody>
</table>

<!-- ────────── 行动 ────────── -->
<h2>🎯 本周该做的事（直接派活）</h2>

<div class="action">
  <b>1. 紧急救援</b> · 张三（杭州 BD）<br>
  本周拜访 <b>5 家 ⚠️ 流失风险</b>客户（杭州 XX 安防、宁波 YY 商行 等），合计 <b>112 万</b>累计货值。
  目标：让其中 ≥ 3 家激活，回到 🏆 核心活跃。
</div>

<div class="action">
  <b>2. 止损</b> · 王五（余杭 BD）<br>
  停止对余杭 XX 商行 等 <b>3 家已流失客户</b>的拜访（季度内已跑 18 次没出货）。
  把这 18 次拜访资源转到本表第一组的核心客户漏跑名单。
</div>

<div class="action">
  <b>3. 分工</b> · 李四 / 赵六 / 钱七（萧山）<br>
  萧山 YY 电子等 <b>4 家撞车客户</b>明确主责人。建议李四接手（拜访次数最多）。
</div>

<!-- ────────── 页脚 ────────── -->
<div class="footer">
  方法论：RFM 5 类（R=60d / F=6 / M=10000）· 红包扫码口径 · 漏跑判定：流失风险 &gt;14 天 / 核心活跃 &gt;30 天 · 数据：install_redpack + visit_record_v + salesperson_scope
</div>

</body>
</html>
```

### 5.3 Python 生成框架

```python
from datetime import datetime
from pathlib import Path
import sqlite3, html

OUT = Path("/sandbox/output")
OUT.mkdir(parents=True, exist_ok=True)

# 1. 解析 context 拿 filters / dispatch_tables 总数
ctx = {...}   # 从 prompt 头部解析

# 2. 跑 4 段 SQL
conn = sqlite3.connect("/sandbox/db.sqlite")
miss = conn.execute("§A SQL").fetchall()          # 漏跑 TOP 20
waste = conn.execute("§B SQL").fetchall()         # 资源错配 TOP 10
clash = conn.execute("§C SQL").fetchall()         # 人员撞车 TOP 10
by_sp = conn.execute("§D SQL").fetchall()         # 按业务员聚合（写行动建议用）

def esc(x): return html.escape(str(x)) if x is not None else "—"

# 3. 算结论一句话
n_miss = len(miss)
m_miss_total = sum(r['累计货值_万'] for r in miss)
top_sp = by_sp[0] if by_sp else None
lead_text = (
    f"本周期 <strong>{n_miss} 家</strong>重要客户漏跑"
    f"（涉货值 <strong>{m_miss_total:.0f} 万</strong>）"
    + (f"，最紧急要处理的是<strong>{esc(top_sp['应负责业务员'])}</strong>"
       f"负责的 <strong>{top_sp['待跑客户数']} 家</strong>客户。"
       if top_sp else '。')
)

# 4. 生成 HTML（按 §5.2 模板填充）
html_doc = TEMPLATE.format(...)

# 5. 写文件
filename = f"服务商分类诊断_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
out_path = OUT / filename
out_path.write_text(html_doc, encoding='utf-8')
print(f"✅ 报告: {out_path}")
```

### 5.4 Chat 简述（最后 assistant text，200 字内）

```
✅ 杭州市本期诊断完成（窗口 12 个月）

🚨 重要客户漏跑：{N} 家 / {M} 万累计货值
   TOP 3: {客户 A} / {客户 B} / {客户 C}
   主责人：{业务员 X}（{N1} 家）、{业务员 Y}（{N2} 家）

💸 跑动无效：{X} 次拜访
   跑错对象 {a} 起（主要是 {王五} 在已流失客户上）
   人员撞车 {b} 家（{李四/赵六} 同时跑）

🎯 本周该做：
1. {业务员 X} 救援 {N} 家流失风险（详见 HTML）
2. {王五} 调整对象：从已流失转向核心活跃
3. {撞车业务员} 明确分工

📄 完整报告：/sandbox/output/{filename}
```

**写作风格**：
- 用具体业务员姓名 / 客户名，不用 "某业务员" "部分客户" 这种空话
- 数字加粗，名字加颜色
- 短句多于长句

---

## 六、工作流 SOP

```
1. Read system-overview SKILL
2. Read 本 SKILL
3. 解析 prompt 头部 【上下文】 → filters / dispatch_tables 总数
4. 跑 §四.A B C D 四段 SQL（套 context.filters）
5. 按 §五.2 模板组装 HTML
6. 写到 /sandbox/output/服务商分类诊断_YYYYMMDD_HHMMSS.html
7. 最后 assistant text 贴 §五.4 简述
```

---

## 七、🚨 易踩坑

| ❌ 错的 | ✅ 对的 |
|--------|--------|
| 报告里放健康度评分、5 类 KPI 卡 | 业务员要的是该跑谁，不是体检报告 — **删掉所有装饰** |
| 推荐"加强 X 区域" | 必须落到**业务员姓名 + 具体客户**（不能写"加强滨江区"）|
| 没把 应负责业务员 join 进漏跑表 | join `salesperson_scope` 取业务员，否则行动建议没法点名 |
| 用 `product_flow_v` 算 M | 用 `install_redpack.产品现有分销价`（红包扫码口径）|
| SQL 不套 context.filters | 报告全错（变成全省数据，跟用户在屏幕上看的对不上）|
| 输出 markdown 不是 HTML | 单文件 HTML，双击可看 |
| 自己 SQL 算的分布 ≠ context | 信 context（V2 公司标准口径）|
| 报告里列了 60 家漏跑全名单 | TOP 20 就够；明细业务员要 → 让他点 page 02 的导出 CSV |
| 用 visit_record 算拜访量 | 用 visit_record_v 加 `_打卡异常无效=0 AND _真异常打卡=0`|
| `pp.所属区县` / `pp.客户名称` | `pp.区县` / `pp.公司名称` |
| 把 ❓ 未采购客户 放进诊断 | 它没红包 → 不在 RFM 分类里，本诊断不展开 |

---

## 八、典型对话示例

**输入 prompt**：
```
【上下文：来自 V2 标准报表】
## 当前页面筛选
{"城市": ["杭州市"], "RFM 时间窗_月": 12, "流失风险超期阈值_天": 14, "核心活跃漏跑阈值_天": 30}
## RFM 阈值
{"R 高/低分界_天": 60, "F 高/低分界_次": 6, "M 高/低分界_元": 10000}
## RFM 5 类分布
{"🏆 核心活跃": 188, "⚠️ 流失风险": 95, "🌱 培育客户": 220, "🪦 已流失": 510}
## 5 张派单表数量
{"🚨 紧急派单": {"数量": 42, ...}, "⚠️ 核心客户漏跑": {"数量": 18, ...},
 "💸 资源错配": {"数量": 8, ...}, "🔁 人员重复": {"数量": 22, ...}, ...}
```

**期望执行**：
```
1. Read 两份 SKILL
2. sqlite3 跑 §A 漏跑名单（42+18=60 家 → TOP 20）
3. sqlite3 跑 §B 资源错配（8 家 → TOP 8）
4. sqlite3 跑 §C 人员撞车（22 家 → TOP 10）
5. sqlite3 跑 §D 按业务员聚合
6. 生成 HTML → /sandbox/output/服务商分类诊断_20260513_182300.html
7. text 输出 §五.4 简述（200 字）
```

✅ 完。
