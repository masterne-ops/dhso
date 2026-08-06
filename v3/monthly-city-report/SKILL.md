# SKILL: monthly-city-report (v4 — 现状 4 章 + 行动 2 章)

## 报告核心定位

**这份报告分两部分：**

- **第一部分（章 1-4）现状与差距** — 把数据摆出来：完成情况 / 客户结构 / 业务员跑动 / 红包 ROI
- **第二部分（章 5-6）行动与责任** — 业务员做了什么 + 解决方案派给谁

## 🚨 四条铁律

**铁律 1：禁止自己算数字。**
后端把所有要用的数字都格式化好放在 `_显示` 字段（对比表里）或者 `数据汇总_xxx` 字段里。直接 copy。

**铁律 2：数字必有出处。**
报告里引用的每个 **数字 / 区县 / 服务商名 / 业务员名**，必须能在 `data_summary.json` 里找到。

**铁律 3：业务员派活按 scope。**
章 6 工作计划的责任方必须是该区县/代理商的负责大华业务员（见 `数据.salesperson_scope`），**不能跨 scope 派活**。
比如要做「滨江区专项」，责任方只能从 `salesperson_scope.区县_to_业务员['滨江区']` 里选。

**铁律 4：数据缺失如实写 N/A。**
N/A 时不要编、不要算「凑」。

**铁律 5：禁止 `Read` 整个 `data_summary.json`。**

JSON 文件约 **30-50k tokens**，超过 Read 单次 25000 上限 — 整文件读必失败。
**必须用 `jq` 按字段提取**，且中文字段名要用方括号语法。

✅ **正确套路（按此顺序）**：

```bash
# Step 1：先看顶层 keys
jq 'keys' /sandbox/output/work/data_summary.json

# Step 2：按需提取每个 section（中文字段必须用 .["xxx"] 不能用 .xxx）
jq '.["对比表"]["so_overview"]' /sandbox/output/work/data_summary.json
jq '.["数据"]["activation_status"]' /sandbox/output/work/data_summary.json
jq '.["数据"]["rfm_distribution"]' /sandbox/output/work/data_summary.json
jq '.["数据"]["abnormal_providers"]' /sandbox/output/work/data_summary.json
jq '.["数据"]["salesperson_eval"]' /sandbox/output/work/data_summary.json
jq '.["数据"]["highlights"]' /sandbox/output/work/data_summary.json
# ... 各 section
```

❌ **错误（必失败）**：
- `Read("/sandbox/output/work/data_summary.json")` — 超 token 上限
- `jq '.地市'` — 中文字段名不支持
- `Read 任何 .py 源码` — 都超 25k tokens
- `Read 自己写的 docx` — 二进制，没意义

✅ **更高效：一次性把所有 section 抽到 /tmp/**

```bash
# 一行抽完，避免来回读
for k in so_overview target_completion district provider activation_status \
         lifecycle rfm_distribution abnormal_providers salesperson_eval \
         redpack_roi product_roi highlights visit visit_efficiency \
         dispatch_alerts keypoint_unvisited salesperson_scope; do
  jq ".[\"数据\"][\"$k\"]" /sandbox/output/work/data_summary.json > /tmp/$k.json
done
# 然后 Read 单个小 json 才合规
```

## 输入 JSON 结构

```json
{
  "地市": "...",
  "当期起止": "...",
  "当期月数": N,
  "时段": {"current": {...}, "yoy": {...}, "mom": {...}},
  "核心结论": "...",
  "数据完整性": {...},
  "数据": {
    // ── 第一部分 现状 ───────────────────
    "so_overview": {...},          // 1.1 SO 总览 (双口径同环比)
    "target_completion": {...},    // 1.1 目标完成 (全量感知口径)
    "district": {...},             // 1.2 区县 top/bottom
    "provider": {...},             // 1.3 服务商画像
    "activation_status": {...},    // 1.3 ⭐应激活/实际激活/漏激活
    "lifecycle": {...},            // 1.3 持续活跃/新激活/沉睡/流失

    "rfm_distribution": {...},     // 2 ⭐RFM 9 宫格（整体+按等级）+ 9宫格定义 + 增长 + 下滑 + 流转
    "abnormal_providers": {...},   // 2.6 ⭐异常服务商（马甲/伞形/假签约）

    "salesperson_eval": {...},     // 3 + 5 ⭐业务员跑动评估（我司 vs 代理商）

    "redpack_roi": {...},          // 4.1 4.2 4.4 全局 ROI / 高低偏离
    "product_roi": {...},          // 4.3 ⭐产品红包 ROI

    "highlights": {...},           // 5.3 ⭐亮点案例 top 5

    // ── 关联数据 ──────────────────
    "visit": {...},                // 业务员跑动同环比汇总
    "keypoint_unvisited": {...},   // 重点客户未跑动
    "visit_efficiency": {...},     // 业务员空跑率
    "dispatch_alerts": {...},      // 派单台
    "salesperson_scope": {...}     // 业务员 scope（章 6 派活用）
  },
  "对比表": {
    "so_overview": [...],
    "provider": [...],
    "provider_tier": [...],
    "visit": [...]
  }
}
```

## 流程

### Step 0：先用 jq 抽数据到 /tmp/（铁律 5 必须执行）

```bash
# 注意：中文字段名必须用 .["字段名"]，不能写 .字段名
for k in so_overview target_completion district provider activation_status \
         lifecycle rfm_distribution abnormal_providers salesperson_eval \
         redpack_roi product_roi highlights visit visit_efficiency \
         dispatch_alerts keypoint_unvisited salesperson_scope; do
  jq ".[\"数据\"][\"$k\"]" /sandbox/output/work/data_summary.json > /tmp/$k.json
done
# 顶层元信息也单独抽
jq '{地市: .["地市"], 当期起止: .["当期起止"], 当期月数: .["当期月数"],
     时段: .["时段"], 核心结论: .["核心结论"], 数据完整性: .["数据完整性"],
     对比表: .["对比表"]}' \
   /sandbox/output/work/data_summary.json > /tmp/meta.json
```

抽完后每个 `/tmp/*.json` 都 < 10k tokens，可以 `Read` 任意一个。

### Step 1：框架 + 字体（Python 加载数据 + 设置宋体）

```python
import json
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn

with open(DATA_PATH, encoding='utf-8') as f:
    D = json.load(f)

doc = Document()
style = doc.styles['Normal']
style.font.name = '宋体'
style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
style.font.size = Pt(10.5)
```

### Step 2：标题 + 元信息（分行）+ 核心结论

```python
title = doc.add_heading(f"{D['地市']} SO 经营月报", level=0)
title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

def add_centered(t):
    p = doc.add_paragraph()
    p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    p.add_run(t)

add_centered(f"报告期：{D['当期起止']}（共 {D['当期月数']} 个月）")
add_centered(f"同比对照：{D['时段']['yoy']['label']}")
add_centered(f"环比对照：{D['时段']['mom']['label']}")

doc.add_heading('核心结论', level=1)
doc.add_paragraph().add_run(D['核心结论']).bold = True
```

---

# 第一部分 · 现状与差距

### Step 3：一、目标达成

#### 1.1 全市 SO 完成率

直接渲染 `D['对比表']['so_overview']`（5 行表，含同环比 %）+ `D['数据']['target_completion']['current']`：
- 年度目标 / 本期应达成 / 实际达成 / 完成率 / 缺口

#### 1.2 区县 SO 完成率

用 `D['数据']['district']['current']`：
- 全量金额 top 5 表
- 完成率 bottom 5 表（完成率 <80% 标红）
- 一句话总结：差距最大的 3 个区县是哪几个

#### 1.3 服务商激活情况

用 `D['数据']['activation_status']`：
- 卡片：应激活 / 实际激活 / 激活率 / 漏激活
- 按等级表：V4 / V3 / V2 / 已激活 各自的应激活 vs 漏激活
- 漏激活 TOP 10 表：客户名称 / 区县 / 等级 / 累计货值 / 最近上线月份 / 所属代理商 / 🎭

辅助：`D['数据']['provider']` 看活跃服务商数、`D['数据']['lifecycle']` 看流失/沉睡。

#### 1.4 综合诊断（AI 写一段）

100-200 字总结：本期最大的 3 个差距是什么、影响 SO 多少。

### Step 4：二、客户结构与 RFM

R/F 阈值：R ≤30🟢 / 30-60🟡 / >60🔴；F ≥12🟢 / 3-11🟡 / <3🔴
M 维度用服务商等级替代：V4≥3 万 / V3 1-3 万 / V2 0.1-1 万 / 已激活 0-0.1 万 / v0 = 未激活

#### 2.1 整体 R-F 9 宫格（含业务标签）

用 `D['数据']['rfm_distribution']['dist_整体']`：3×3 表 [R 档 × F 档]，每格含：
- 客户数 / 累计货值_万 / **业务标签**（已在数据里）

下方加 9 宫格业务定义表（用 `D['数据']['rfm_distribution']['9宫格定义']` 渲染）：

| | F🟢 高频 (≥12) | F🟡 中频 (3-11) | F🔴 低频 (<3) |
|---|---|---|---|
| **R🟢 活跃 (≤30)** | 核心客户 | 健康偶发 | 偶发活跃 |
| **R🟡 预警 (30-60)** | 高频疏远 | 预警客户 | 预警低频 |
| **R🔴 沉睡 (>60)** | 历史活跃已沉睡 | 沉睡待救援 | 低价值死户 |

#### 2.2 按等级分层 R-F 9 宫格（M = 等级）

用 `D['数据']['rfm_distribution']['dist_按等级']`，每个等级一个小节，内置 9 宫格：

- **V4 (≥3 万)**：累计货值 X 万 / Y 家 / 9 宫格
- **V3 (1-3 万)**：同上
- **V2 (0.1-1 万)**：同上
- **已激活 (0-0.1 万)**：同上

同时给出 v0 数量（来自 `D['数据']['rfm_distribution']['count_v0']`，仅 count，无 R-F 数据）。

#### 2.3 增长客户 TOP 20

用 `D['数据']['rfm_distribution']['增长_top10']`：
- 列：客户名称 / 区县 / 上期货值 / 本期货值 / 增长率 / R_end / F_12mo

#### 2.4 下滑客户 TOP 20

用 `D['数据']['rfm_distribution']['下滑_top10']`：
- 列：客户名称 / 区县 / 上期货值 / 本期货值 / 增长率 / R 期初 → 期末 / 问题
- 「问题」列已经标好：M 下滑 / R 滑落 / 二者都有

#### 2.5 健康度流转矩阵

用 `D['数据']['rfm_distribution']['健康度流转']`：3×3 [期初 R 状态 → 期末 R 状态]。

#### 2.6 异常服务商明细 🚨

用 `D['数据']['abnormal_providers']`，三块分别写：

**🎭 马甲服务商**（来源 vest_account 表）
- 总数 `D['数据']['abnormal_providers']['马甲']['总数']`
- TOP 10 表：客户编码 / 客户名称 / 所属一级 / 累计货值_万 / 累计台数

**☂️ 伞形服务商**（同老板姓名+电话 注册 ≥2 家不同客户）
- 总数：`老板组数` 组 / `涉及客户数` 户
- TOP 10 表：老板姓名 / 电话（脱敏）/ 客户数 / 客户清单 / 伞形累计货值_万

**❌ 假签约服务商**（签约 60+ 天但累计上线 ≤2 台）
- 总数：`D['数据']['abnormal_providers']['假签约']['总数']`
- TOP 10 表：客户编码 / 客户名称 / 区县 / 签约日 / 签约后天数 / 累计上线台数 / 累计货值_万 / 最近上线

### Step 5：三、业务员跑动分析（我司 vs 代理商）

#### 3.1 我司业务员（大华自营）

用 `D['数据']['salesperson_eval']['我司明细']`：
- 表：业务员 / 负责区县 / 拜访客户数 / 救援成功 / 救援未果 / 维护成功 / 维护稳定 / 维护不到位 / 维护失败 / 新客探访 / 严重漏跑 / 一般漏跑

#### 3.2 代理商业务员

用 `D['数据']['salesperson_eval']['代理商明细']`：列同上。

#### 3.3 我司 vs 代理商对比

用 `D['数据']['salesperson_eval']['对比表']`：
- 对比表：维度 × [我司 / 代理商]
- 维度：业务员数 / 人均拜访 / 选择合理率 / 救援成功率 / 维护成功率 / 严重漏跑 / 一般漏跑

⚠️ **重要说明**：「漏跑」指的是 scope 内本期 R 从 ≤60 滑到 >60 的客户。
目前只有大华业务员有 scope 定义（在 `salesperson_scope` 表），代理商业务员无 scope，
所以代理商的漏跑统计恒为 0。**不要把这当作代理商表现好** — 在文字里要明确写明：
"代理商业务员暂无 scope 定义，漏跑数据不可比"。

AI 一段文字（80-120 字）：我司做得好/差在哪、代理商做得好/差在哪。漏跑这条不参与对比。

### Step 6：四、红包 ROI

#### 4.1 全局红包 ROI

用 `D['数据']['redpack_roi']`：
- 当期红包总额 / 上线总额 / ROI 基线 = X%
- 同比 / 环比（如果有）

#### 4.2 服务商红包 ROI 排行

- 高偏离 TOP 10（>1.5x，红包多 vs 上线少）：`高偏离名单_top10`
- 低偏离 TOP 10（<0.5x，红包少 vs 上线高）：`低偏离名单_top10`

#### 4.3 产品红包 ROI

用 `D['数据']['product_roi']['产品ROI_top10_按上线额']`：
- 表：产品 / 笔数 / 红包额 / 上线额 / ROI

#### 4.4 红包浪费清单（AI 关联写）

把 `redpack_roi.高偏离名单_top10` 中 `上线额_万 ≤ 0.5` 的列出来 — 红包烧了但几乎没上线。

---

# 第二部分 · 行动与责任

### Step 7：五、评估周期工作总结

#### 5.1 我司业务员工作量

用 `D['数据']['salesperson_eval']['我司工作量']`：
- 每个业务员一行：拜访客户数 / 救援成功+维护成功 / 严重漏跑

#### 5.2 代理商业务员工作量

用 `D['数据']['salesperson_eval']['代理商工作量']`：同上结构（注意可能人多，列前 15 或者按拜访数排序）。

⚠️ 代理商业务员的「严重漏跑」「一般漏跑」字段为 `null`（无 scope 不统计）— 表里这两列写 "—"。

#### 5.3 亮点案例 TOP 5

用 `D['数据']['highlights']['亮点案例_top5']`：
- 表：业务员 / 所属 / 客户名称 / 区县 / 首拜访日 / 分类 / 拉动 SO_万

### Step 8：六、解决方案

#### 6.1 区域 & 服务商重点投入

写一个表，5 字段：

| 重点对象 | 当前问题 | 投入建议 | 责任人 | 期限 |
|---------|---------|---------|--------|------|

来源：
- **重点区县**：从 1.2 完成率 bottom 5 取 → `district.current.bottom_n_by_完成率`
- **重点服务商**：从 1.3 `activation_status.漏激活名单_top10` 取 V4/V3 + 2.3 `下滑_top10` 取重大下滑
- **责任人**：必须从 `salesperson_scope.区县_to_业务员[区县]` 取（铁律 3）

写 5-8 行，每行严格按字段写。

#### 6.2 员工整改清单

写一个表，5 字段：

| 业务员 | 所属 | 整改原因 | 整改指标 | 跟进期限 |
|--------|------|---------|---------|---------|

来源：从 `salesperson_eval.我司工作量 + 代理商工作量` 挑：

- **严重漏跑 >5 个** → 整改原因「漏跑V3/V4」，整改指标「下期严重漏跑 ≤2」
- **维护失败 >3 个** → 整改原因「维护失败多」，整改指标「下期维护失败 ≤1」
- **代理商办公室打卡 >2 次** → 整改原因「选择失误，去代理商办公室打卡」，整改指标「不再发生」

⚠️ 代理商业务员因无 scope，不能用「漏跑」做整改触发条件，
只能用「救援未果」「维护失败」「代理商办公室打卡」做触发。

最后单独一段「表扬清单」：用 5.3 的亮点案例业务员，加一句"以孙鲁江 / XX 为标杆"。

### Step 9：保存

```python
doc.save(OUT_PATH)
print(f"✅ 报告已保存：{OUT_PATH}")
print(f"   核心结论：{D['核心结论']}")
```

## 自检清单（保存前过一遍）

1. ✅ 元信息**分了 3 段**？（报告期 / 同比对照 / 环比对照）
2. ✅ 报告开头有 **核心结论**？
3. ✅ 章 1.1 / 3.3 / 4.1 数据表里数字都从 `_显示` 或具体字段 copy？没自己算？
4. ✅ 章 1.3 漏激活清单 含「所属代理商」+「🎭马甲」列？
5. ✅ 章 2.1 9 宫格每格都标了**业务标签**（核心客户/低价值死户...）？
6. ✅ 章 2.2 按等级 V4/V3/V2/已激活 都画了独立 9 宫格？v0 给了 count？
7. ✅ 章 2.6 三类异常服务商（🎭马甲/☂️伞形/❌假签约）都列了？
8. ✅ 章 3.3 我司 vs 代理商有对比文字？
9. ✅ 章 5.3 亮点案例是结构化表（不是故事化文字）？
10. ✅ 章 6.1 每行的「责任人」都在 `salesperson_scope.区县_to_业务员` 列表里？没跨 scope？
11. ✅ 章 6.2 每行的「整改原因」「整改指标」具体、可衡量？
12. ✅ 数据缺失（N/A）时如实写，没编？
13. ✅ 保存了，stdout 输出核心结论摘要？
