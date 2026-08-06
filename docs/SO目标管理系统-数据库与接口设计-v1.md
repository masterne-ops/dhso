# SO 目标管理系统 — 数据库结构与读写接口设计（v1）

> **交付对象**：外部开发者（独立系统，不在现有 SO 数据分析平台内）
> **版本**：v1 / 2026-06-02
> **作者**：JARVIS（为金先生整理）
> **范围**：第一版只做「服务商 SO 目标」主线——省/地市/代理商三级目标分解 → 服务商承接 → 资源/动作计划 → 汇总。

---

## 给金先生的导读（非技术）

这份文档是**给开发者照着建数据库、写后端用的**。核心讲三件事：

1. **要建哪些表**（数据怎么存）——第 3、4 章。
2. **这些表怎么读怎么写**（后端要提供哪些操作）——第 5 章。
3. **你（大华）要给这个系统喂什么数据**（组织架构 + 历史销售）——第 6 章。

你审的时候重点看 **第 1 章流程**（是不是你要的业务）和 **第 7 章待确认项**（我替你做了假设，需要你拍板的地方）。

---

## 1. 业务流程（系统要支撑的闭环）

每月一轮，分三段：

### 第一段：三级目标分解（省 → 地市 → 代理商）

```
省区总目标(金额+台数)
   │  ① 系统按历史 SO 占比，自动拆出"建议值"
   ▼
地市目标(11 个) ──② 下发给地市主管 → ③ 主管填报 → ④ 省区确认
   │  ⑤ 系统按历史占比，把每个地市的确认值再拆到代理商
   ▼
代理商目标(49 家)──下发给代理商对接人 → 填报 → 地市确认
```

- **三态值**：每一级每个对象都有三个数 → `系统建议值` / `负责人填报值` / `最终确认值`。
- **一致性铁律**：Σ(下级确认值) 必须 = 上级确认值（金额、台数各自校验）。系统要能随时校验、并高亮对不上的地方。
- **层级**：严格树形——一个代理商只属一个地市，一个地市只属省区。

### 第二段：拆到服务商（承接层）

代理商目标确认后，继续往下拆到该代理商旗下的服务商：

- 每个服务商：**承接目标**(要做多少) / **已确认**(确认能做多少) / **缺口** / **风险等级**。
- 每个服务商：要投入的**营销资源**(预算) + 要做的**营销动作**(拜访/推广会/铺货/红包…)。

### 第三段：汇总决策视图

按**地市**或**代理商**维度，一张表看清：下月这个地市/代理商 → 目标多少、已确认多少、缺口多少、要投多少资源、做哪些动作、谁负责。

---

## 2. 系统边界与数据流

这是**独立系统**（自己的库、自己的后端），不直接连大华生产库。数据关系：

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│  大华 SO 数据分析平台      │  同步   │  SO 目标管理系统（本系统）       │
│  (product_flow.db)       │ ──────▶ │                                │
│                          │  组织树  │  基础数据(只读消费)：           │
│  - 服务商/代理商/地市档案  │  历史SO │    dim_org / dim_provider      │
│  - 历史出货 SO 明细       │         │    fact_so_history             │
└─────────────────────────┘         │                                │
                                     │  业务数据(读写)：               │
         每月同步一次(导出/接口)       │    so_period                   │
                                     │    so_target                   │
                                     │    so_provider_target          │
                                     │    so_provider_action          │
                                     └──────────────────────────────┘
```

- **基础数据**（组织树、历史 SO）：由大华侧**定期同步**进来，本系统**只读**消费（用于自动拆解建议、风险判断）。同步格式见第 6 章。
- **业务数据**（目标、承接、动作）：本系统**读写**，是系统的核心资产。
- DDL 以 **SQLite** 为例（与大华现有平台一致），可平移到 PostgreSQL / MySQL；`JSON`、`CHECK`、自增主键按目标库语法微调。

---

## 3. 数据模型总览（ER）

```
dim_org (省/地市/代理商, 自引用树)
  ▲ parent_id
  │
  ├── so_target          (period × org_id 的三级目标, 三态值)
  │
dim_provider (服务商, 挂在 dealer 下)
  │
  ├── so_provider_target (period × provider 的承接目标/风险/资源)
  │     │
  │     └── so_provider_action (period × provider 的营销动作明细, 一对多)
  │
fact_so_history (period × provider 的历史SO, 供拆解建议)

so_period (周期主表, 被上面的 period 字段引用)
```

| 表 | 类型 | 行级含义 | 读写 |
|---|---|---|---|
| `dim_org` | 维度(同步) | 一个组织节点(省/地市/代理商) | 只读 |
| `dim_provider` | 维度(同步) | 一个服务商 | 只读 |
| `fact_so_history` | 事实(同步) | 一个服务商某月的历史 SO | 只读 |
| `so_period` | 业务 | 一个任务周期(年月) | 读写 |
| `so_target` | 业务 | 一个组织节点某周期的目标(三态) | 读写 |
| `so_provider_target` | 业务 | 一个服务商某周期的承接 | 读写 |
| `so_provider_action` | 业务 | 一个服务商某周期的一条动作 | 读写 |

---

## 4. 表结构（DDL + 字段说明）

### 4.1 dim_org — 组织树（省 / 地市 / 代理商）

```sql
CREATE TABLE dim_org (
  org_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  org_level   TEXT NOT NULL,              -- 'province' | 'city' | 'dealer'
  org_code    TEXT NOT NULL UNIQUE,       -- 业务编码(代理商=客户编码; 地市/省用约定码)
  org_name    TEXT NOT NULL,              -- 名称
  parent_id   INTEGER,                    -- 父节点 org_id; 省的 parent_id = NULL
  owner_user  TEXT,                        -- 默认填报负责人(大华内部 username)
  active      INTEGER NOT NULL DEFAULT 1,  -- 是否启用
  synced_at   TEXT,                        -- 最近同步时间
  FOREIGN KEY (parent_id) REFERENCES dim_org(org_id)
);
CREATE INDEX idx_org_level  ON dim_org(org_level);
CREATE INDEX idx_org_parent ON dim_org(parent_id);
```

- 三层共用一张表，用 `org_level` + `parent_id` 表达树。省 1 行、地市 ~11 行、代理商 ~49 行。
- `owner_user`：该节点的默认负责人；下发任务时默认派给他（可在 so_target.assignee 覆盖）。

### 4.2 dim_provider — 服务商（承接层）

```sql
CREATE TABLE dim_provider (
  provider_code  TEXT PRIMARY KEY,         -- 服务商客户编码(业务唯一键)
  provider_name  TEXT NOT NULL,
  dealer_id      INTEGER NOT NULL,         -- 所属代理商 org_id (→ dim_org, level=dealer)
  city_id        INTEGER NOT NULL,         -- 所属地市 org_id (冗余, 便于按地市聚合)
  grade          TEXT,                     -- 服务商等级: v0未激活/已激活/v2/v3/v4
  tags           TEXT,                     -- 标签(逗号分隔): 批发/工程/夫妻店…
  active         INTEGER NOT NULL DEFAULT 1,
  synced_at      TEXT,
  FOREIGN KEY (dealer_id) REFERENCES dim_org(org_id),
  FOREIGN KEY (city_id)   REFERENCES dim_org(org_id)
);
CREATE INDEX idx_prov_dealer ON dim_provider(dealer_id);
CREATE INDEX idx_prov_city   ON dim_provider(city_id);
```

- `city_id` 冗余存（树形下可由 dealer 推出），是为了汇总视图按地市聚合时不用多跳一层。
- ⚠️ 树形假设下，服务商的地市 = 其代理商的地市。若实际有服务商跨地市经营，以**所属代理商的地市**为准（见第 7 章待确认）。

### 4.3 fact_so_history — 历史 SO（供自动拆解建议）

```sql
CREATE TABLE fact_so_history (
  period        TEXT NOT NULL,             -- '2026-05'(历史月份)
  provider_code TEXT NOT NULL,
  so_amount     REAL,                       -- 该月 SO 金额(万元)
  so_qty        INTEGER,                    -- 该月 SO 台数
  synced_at     TEXT,
  PRIMARY KEY (period, provider_code),
  FOREIGN KEY (provider_code) REFERENCES dim_provider(provider_code)
);
CREATE INDEX idx_sohist_period ON fact_so_history(period);
```

- 服务商粒度的历史 SO。系统用它算"各服务商/代理商/地市占全省的历史占比"，作为自动拆解的依据。
- 至少同步近 6~12 个月，供算占比。

### 4.4 so_period — 任务周期

```sql
CREATE TABLE so_period (
  period            TEXT PRIMARY KEY,       -- '2026-06'
  status            TEXT NOT NULL DEFAULT 'drafting',
       -- drafting(拆解中) | dispatched(已下发) | confirming(填报/确认中) | confirmed(已确认) | closed(已归档)
  prov_target_amount REAL,                   -- 省区总目标-金额(万元), 整轮的源头
  prov_target_qty    INTEGER,                -- 省区总目标-台数
  created_by        TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at        TEXT,
  note              TEXT
);
```

- 一个年月一行。`prov_target_*` 是整轮分解的源头数字（省区总盘子），由省区管理员录入。

### 4.5 so_target — 分级目标（省/地市/代理商，核心表）

```sql
CREATE TABLE so_target (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  period          TEXT NOT NULL,            -- → so_period.period
  org_id          INTEGER NOT NULL,         -- → dim_org.org_id
  org_level       TEXT NOT NULL,            -- 冗余: province|city|dealer (便于按层级查/校验)
  parent_org_id   INTEGER,                  -- 冗余父节点(便于一致性校验聚合)

  -- ① 系统建议值(按历史占比拆出)
  suggest_amount  REAL,    suggest_qty  INTEGER,
  -- ② 负责人填报值
  report_amount   REAL,    report_qty   INTEGER,
  -- ③ 最终确认值(一致性校验以此为准)
  confirm_amount  REAL,    confirm_qty  INTEGER,

  -- 流程
  assignee        TEXT,                     -- 填报负责人(username); 默认取 dim_org.owner_user
  status          TEXT NOT NULL DEFAULT 'draft',
       -- draft(草稿) | dispatched(已下发待填) | reported(已填报待确认) | confirmed(已确认)
  dispatched_at   TEXT,
  reported_at     TEXT,
  confirmed_at    TEXT,
  updated_by      TEXT,
  updated_at      TEXT,
  note            TEXT,

  UNIQUE (period, org_id),
  FOREIGN KEY (period) REFERENCES so_period(period),
  FOREIGN KEY (org_id) REFERENCES dim_org(org_id)
);
CREATE INDEX idx_tgt_period_level ON so_target(period, org_level);
CREATE INDEX idx_tgt_parent       ON so_target(period, parent_org_id);
CREATE INDEX idx_tgt_assignee     ON so_target(assignee, status);
```

**三态值设计说明**（关键）：
- `suggest_*`：系统拆出来的建议，给负责人参考。
- `report_*`：负责人实际填的（可能 ≠ 建议）。
- `confirm_*`：上级拍板的最终值，**一致性校验和后续服务商拆解都以 confirm 为准**。
- 三个值都分 `amount`(金额万元) + `qty`(台数) 两个口径。

### 4.6 so_provider_target — 服务商承接

```sql
CREATE TABLE so_provider_target (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  period            TEXT NOT NULL,
  provider_code     TEXT NOT NULL,          -- → dim_provider
  dealer_id         INTEGER NOT NULL,       -- 冗余所属代理商(承接归集到代理商目标)
  city_id           INTEGER NOT NULL,       -- 冗余所属地市

  -- 承接目标(分配给该服务商要做的)
  carry_amount      REAL,    carry_qty      INTEGER,
  -- 已确认(确认能做到的)
  confirmed_amount  REAL,    confirmed_qty  INTEGER,
  -- 缺口 = carry - confirmed (可由视图算; 此处冗余便于排序筛选)
  gap_amount        REAL,    gap_qty        INTEGER,

  -- 风险 & 资源
  risk_level        TEXT,                   -- 高 | 中 | 低 | 无
  risk_note         TEXT,                   -- 风险说明
  resource_budget   REAL,                   -- 计划投入营销资源(万元, 汇总; 明细在 action 表)

  assignee          TEXT,                   -- 负责该服务商的大华业务员
  status            TEXT NOT NULL DEFAULT 'draft',  -- draft | confirmed
  updated_by        TEXT,
  updated_at        TEXT,
  note              TEXT,

  UNIQUE (period, provider_code),
  FOREIGN KEY (period)        REFERENCES so_period(period),
  FOREIGN KEY (provider_code) REFERENCES dim_provider(provider_code),
  FOREIGN KEY (dealer_id)     REFERENCES dim_org(org_id)
);
CREATE INDEX idx_pt_period_dealer ON so_provider_target(period, dealer_id);
CREATE INDEX idx_pt_period_city   ON so_provider_target(period, city_id);
CREATE INDEX idx_pt_risk          ON so_provider_target(period, risk_level);
```

- **承接一致性**：Σ(某代理商旗下所有服务商的 carry) 应 = 该代理商的 so_target.confirm。系统要能校验。

### 4.7 so_provider_action — 营销动作计划

```sql
CREATE TABLE so_provider_action (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  period          TEXT NOT NULL,
  provider_code   TEXT NOT NULL,
  action_type     TEXT NOT NULL,            -- 拜访 | 推广会 | 铺货 | 红包激励 | 培训 | 陈列 | 其他
  action_desc     TEXT,                     -- 动作说明
  resource_type   TEXT,                     -- 费用 | 红包 | 物料 | 人力
  resource_amount REAL,                     -- 资源量(万元 或 数量)
  assignee        TEXT,                     -- 负责人(大华业务员)
  plan_date       TEXT,                     -- 计划完成日
  status          TEXT NOT NULL DEFAULT 'planned',  -- planned | doing | done | cancelled
  done_date       TEXT,
  result_note     TEXT,
  updated_by      TEXT,
  updated_at      TEXT,

  FOREIGN KEY (period)        REFERENCES so_period(period),
  FOREIGN KEY (provider_code) REFERENCES dim_provider(provider_code)
);
CREATE INDEX idx_act_period_prov ON so_provider_action(period, provider_code);
CREATE INDEX idx_act_assignee    ON so_provider_action(assignee, status);
```

- 一个服务商一个周期可有多条动作（一对多）。`so_provider_target.resource_budget` 是这些动作 resource_amount 的汇总（或单独录，见待确认）。

---

## 5. 读写接口规格

接口按业务动作组织，给出**伪签名 + 用途 + 入/出参 + 核心逻辑/SQL**。开发者据此实现后端（REST / RPC 皆可）。

### 5.A 周期与自动拆解

#### `create_period(period, prov_target_amount, prov_target_qty, by) → period`
开一个新月度周期。写 `so_period`（status='drafting'）。

#### `generate_suggestions(period) → {city: n, dealer: n, provider: n}`
**自动拆解的核心算法接口**。按历史 SO 占比，把省区总目标逐级拆成 `suggest` 值：
1. 取 `fact_so_history` 近 N 月（默认 3 月）汇总，算每个**地市**占全省的金额/台数占比。
2. `地市 suggest = 省区总目标 × 地市占比` → upsert 进 `so_target`(level=city)。
3. 每个地市内，算各**代理商**占该地市的占比 → `代理商 suggest = 地市 suggest × 占比` → upsert `so_target`(level=dealer)。
4. （承接层）每个代理商内，算各**服务商**占比 → `服务商 carry 建议` → upsert `so_provider_target.carry_*`。
- 末位差额归并到最大节点，保证 Σ 子 = 父（避免四舍五入丢量）。
- 返回各级生成行数。**只写 suggest/carry，不动 report/confirm**。

### 5.B 三级目标流程（下发 → 填报 → 确认）

#### `dispatch_targets(period, level, by) → count`
把某层级目标下发给负责人。`UPDATE so_target SET status='dispatched', dispatched_at=now WHERE period=? AND org_level=? AND status='draft'`。默认 `assignee = dim_org.owner_user`。

#### `report_target(period, org_id, report_amount, report_qty, assignee, note) → ok`
负责人填报。写 `report_*`，`status='reported'`, `reported_at=now`。校验调用者 = assignee 或 admin。

#### `confirm_target(period, org_id, confirm_amount, confirm_qty, by) → {ok, consistency}`
上级确认。写 `confirm_*`, `status='confirmed'`, `confirmed_at=now`。
- 确认前**建议先跑 `check_consistency`**：若 Σ(直接下级 confirm) ≠ 本次 confirm，返回告警（可强确认，留 note）。

#### `batch_confirm(period, level, by)`
按建议值一键确认整层（confirm = report，缺 report 时 = suggest），用于快速走完流程。

### 5.C 一致性校验（用户强调的"加回来要一致"）

#### `check_consistency(period, level=null) → [{org_id, org_name, level, self_confirm, children_sum, diff_amount, diff_qty, ok}]`
逐节点校验 Σ(子确认) vs 自身确认：
```sql
-- 以代理商→地市为例
SELECT c.org_id, c.org_name,
       c.confirm_amount AS self_amt,
       SUM(d.confirm_amount) AS child_amt,
       c.confirm_amount - SUM(d.confirm_amount) AS diff_amt
FROM so_target c
JOIN so_target d ON d.period=c.period AND d.parent_org_id=c.org_id
WHERE c.period=? AND c.org_level='city'
GROUP BY c.org_id
HAVING ABS(diff_amt) > 0.001;  -- 容差
```
金额、台数各校验一次。`level=null` 时校验全树（省↔地市、地市↔代理商、代理商↔服务商承接）。返回所有对不上的节点供前端高亮。

### 5.D 服务商承接 & 动作

#### `list_provider_targets(period, dealer_id|city_id) → rows`
某代理商/地市旗下服务商承接清单（join dim_provider 带名称/等级/标签）。

#### `upsert_provider_target(period, provider_code, {carry_amount, carry_qty, confirmed_amount, confirmed_qty, risk_level, risk_note, resource_budget, assignee}, by)`
新增/更新服务商承接。`gap_* = carry_* - confirmed_*` 由服务端算后写入。`UNIQUE(period, provider_code)` 冲突则更新。

#### `add_provider_action(period, provider_code, {action_type, action_desc, resource_type, resource_amount, assignee, plan_date}, by) → action_id`
加一条营销动作。

#### `update_action(action_id, {status, done_date, result_note}, by)`
更新动作状态/结果。

### 5.E 读 & 汇总

#### `get_period_overview(period) → {status, 省目标, 各层级完成填报/确认进度, 一致性是否全通过}`
周期看板数据。

#### `get_summary(period, dim) → rows`  ( dim ∈ {city, dealer} )
**第三段要的汇总表**。按地市或代理商聚合：
```sql
SELECT o.org_id, o.org_name,
       t.confirm_amount AS 目标金额, t.confirm_qty AS 目标台数,
       SUM(pt.confirmed_amount) AS 已确认金额,
       SUM(pt.carry_amount) - SUM(pt.confirmed_amount) AS 缺口金额,
       SUM(CASE WHEN pt.risk_level='高' THEN 1 ELSE 0 END) AS 高风险服务商数,
       SUM(pt.resource_budget) AS 计划资源投入,
       COUNT(DISTINCT a.id) AS 动作数,
       GROUP_CONCAT(DISTINCT pt.assignee) AS 负责人
FROM dim_org o
JOIN so_target t          ON t.period=? AND t.org_id=o.org_id
LEFT JOIN so_provider_target pt ON pt.period=? AND pt.{dealer_id|city_id}=o.org_id
LEFT JOIN so_provider_action a  ON a.period=? AND a.provider_code=pt.provider_code
WHERE o.org_level = ?      -- 'dealer' 或 'city'
GROUP BY o.org_id;
```
建议固化成视图 `v_summary_city` / `v_summary_dealer`。

#### `get_my_tasks(username, period) → {targets:[...], providers:[...], actions:[...]}`
某负责人本周期待填/待做清单（按 assignee 过滤三张业务表）。

---

## 6. 输入数据契约（大华侧每月需同步给本系统）

本系统不连大华生产库，靠大华**每月同步**这三类数据（CSV / JSON / 直接灌库均可）：

| 数据 | 灌入表 | 字段 | 频率 |
|---|---|---|---|
| 组织树(省/地市/代理商) | `dim_org` | org_level, org_code, org_name, parent(用 org_code 表达), owner_user | 变更时 |
| 服务商档案 | `dim_provider` | provider_code, provider_name, 所属代理商 org_code, 所属地市, grade, tags | 月度 |
| 历史 SO | `fact_so_history` | period, provider_code, so_amount(万元), so_qty | 月度(近6-12月) |

> 大华侧这三类数据在现有平台都有现成来源（组织树 = 代理商档案 + 地市映射；服务商 = provider 档案；历史 SO = product_flow 出货按服务商×月汇总）。同步脚本由大华侧或开发者按约定格式实现。

---

## 7. 待确认项（需金先生拍板，已先做合理假设）

1. **服务商跨地市/跨代理商**：树形假设下一个服务商只挂一个代理商。实际有服务商从多个代理商进货——本版按**所属（签约）代理商**归集，跨渠道进货不影响目标归属。是否认可？
2. **营销资源的计量**：现按"金额(万元)"统一计。是否还要分红包额度/物料数量等多种类型单独统计？（影响 action 表是否要拆更细）
3. **"已确认"由谁确认**：服务商承接的 `confirmed_*`，是大华业务员代填，还是要服务商侧反馈？（本版假设大华业务员代填，因你说"都是大华内部人填"）
4. **风险等级判定**：`risk_level` 是人工填，还是系统按"承接目标 vs 历史能力"自动算一个建议？（本版留人工填 + 可加自动建议）
5. **台数口径**：台数目标和金额目标是否独立填报（可能只重点管金额，台数仅参考）？还是两个都硬性分解+校验？
6. **历史占比的窗口**：自动拆解默认用"近 3 个月"历史 SO 占比。是否改为去年同期/近 6 月加权？

---

## 8. 建表顺序（开发者建库参考）

```
1. dim_org
2. dim_provider      (依赖 dim_org)
3. fact_so_history   (依赖 dim_provider)
4. so_period
5. so_target         (依赖 so_period, dim_org)
6. so_provider_target(依赖 so_period, dim_provider, dim_org)
7. so_provider_action(依赖 so_period, dim_provider)
8. 视图 v_summary_city / v_summary_dealer
```

---

*本文档为 v1 设计稿。第 7 章确认后出 v2（锁定字段 + 补充示例数据 + 接口出入参 JSON 样例）。*
