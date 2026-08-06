# SKILL: system-overview — SO 数据分析平台 系统功能说明书

> ⭐ **每个 agent 任务开始前必须读完本文档**。这是所有指标计算的唯一真相源。
> 推荐第一步：`Read /sandbox/skills/system-overview/SKILL.md`，理解后再开始任务。
> 口径更新：2026-07-19 · 已纳入市场推广费用、推广会、门头投入及 SO 效率专题。

## 一、平台一句话

帮**大华公司**管理**浙江省**的**渠道服务商网络**：
- 跟踪 SO（销售订单 / 出货）完成情况
- 评估服务商质量与活跃度（RFM、服务商等级 V0-V5 / 货值档）
- 优化业务员跑动与红包投放
- 识别异常（马甲 / 伞形 / 低效服务商）

## 二、数据库结构（首选用视图）

### 🌐 视图层（**推荐用这些**，已 fix 派生字段）

| 视图 | 行数 | 关键派生字段 |
|------|------|-------------|
| `product_flow_v` | ~80 万 | `上线年月`(YYYY-MM) / `上线日期` / `KPI金额`(优先红包价 fallback 主表分销价) / `产品系列_有效` / `流通天数` |
| `install_redpack_v` | ~42 万 | `上线年月` / `上线年份` / `上线月份` |
| `visit_record_v` | ~4 万 | `_打卡方`("🏢 大华"=SMB客户拜访明细/2026换源 · "🏪 代理商"=拜访活动明细表) / `_打卡异常无效` / `_真异常打卡` |
| `provider_tier_v` | ~25k | `服务商等级`(列名误导，实为**货值档**：V4/V3/V2/已激活/v0，基于全历史累计上线货值；**≠** 真正的服务商等级=`provider_contract.服务商等级` 原始 26 年官方 V0-V5)|

### 📦 原表（**懂结构时再查**）

```
product_flow      主表（出货/上线明细）— 全量感知口径
install_redpack   安装红包扫码记录   — 服务商绑定口径
visit_record      业务员拜访打卡（🏪代理商=拜访活动明细表 · 🏢大华=SMB客户拜访明细2026换源;按月全量替换）
provider_profile  服务商档案（沙盘）
provider_contract 签约信息（合同）
vest_account      已确认马甲名单
salesperson_scope 大华业务员负责区县/代理商
salesperson_task  月度跑动任务（V3 新增）
kpi_targets       年度 SO 目标（按区县）
kpi_rhythm        月度节奏比例
dealer_purchase   代理商进货明细流水（2025全年+2026至7月;⚠️全量入库打标区分,查代理商进货必须 WHERE 下单客户类型='一级代理商';无客户编码只能按名称关联;2026整年替换更新 load_dealer_purchase_2026.py;含Discount折让负值行;page40进货指导的进货参考源）

# 🎁 转化红包（大华业务员转化激励，活动 2025-06 起；入库 INSERT OR REPLACE 刷新状态）
dahua_redpack_quota  配额：业务员×月（主键 时间+工号）。红包总额/红包使用金额/发放客户数/红包发放后30天内转化激活客户数/红包配额使用率/服务商红包已解锁金额/服务商红包解锁率
dahua_redpack_grant  发放明细：每张券（主键 卡券编码）。发放人姓名+发放人工号/发放客户编码(→服务商)/发放红包值(50元券=激活5台解锁,另有30/20/10元)/卡券状态(已兑换=激活够台数已解锁领取 / 已失效=没激活够已过期 / 待解锁 / 待兑换)/发放场景/签约一级客户名称(=所属代理商)/红包发放后30天内安装红包上线金额

# 📋 签约客户月度进度 + 会议分析（2026-06-21 周更新增；月度快照 INSERT OR REPLACE 刷新）
signed_customer_monthly  签约客户月度进度（主键 客户编码）：一级分销商(代理商)签约+SI进度月度快照。⭐取代理商SI数值用「累计业绩达成（计任务）」;另有 签约金额/年度业绩达成（计任务）/年度完成率/累计任务完成率;客户所有者=大华业务员
meeting_analysis  会议分析（主键 ADSPID）：会议级转化汇总(vs promotion_meeting=每参会客户一行,本表=每会议一行)。报名公司数/签到公司数/签到公司签约率/签到公司激活率/会议申请费用/参会后安装红包金额/会后新签客户数/会后新激活客户数/夜视王·场景化·无线产品上线台数。Unnamed合并列存为_colN

# 📣 市场推广费用与 SO 效率（按 snapshot_date 快照）
marketing_import_batch      导入审计；source_type 六类，file_sha256 幂等，status='success' 为当前有效批次
marketing_region_snapshot   城市/区县汇总(source_type=city/district)：总费用、费用结构、门头、推广会、关联SO、转化；区域总盘唯一权威表
marketing_dealer_snapshot   代理商已分摊费用；同代理商按业务类型/渠道类型拆多行，查询时按 dealer_code/dealer_name 汇总
marketing_provider_snapshot 服务商门头明细；storefront_invest/active_flag/annual_redpack_output；有 city、无 district
marketing_meeting_snapshot  会议级费用与转化；adsp_id/meeting_expense/post_redpack_output/签到/新签/新激活/券/三专项台数
marketing_meeting_attendee  参会账号/参与人明细；同场同公司多人全部保留，不能假设(adsp_id,customer_code)唯一

旧 promotion_meeting/promotion_meeting_summary/provider_storefront_invest 仅作旧页面兼容；新市场推广专题优先使用 marketing_* 快照表。

# 📥 NP 转入客户（np_transfer_customer, page39 📥NP转入进展 看板；按数据时点快照）
np_transfer_customer  NP流转/一站式转入客户统一表（主键 数据时点+外部客户名称）。漏斗:转入→已报备(客户状态='已报备')→与我司相关(与我司业务相关='Y')→已签约(客户名称=内部签约名 命中 provider_contract)→已激活(签约名 install_redpack 累计上线≥1000)。客户来源+数据时点分批次(NP流转/一站式=新168全字段 / NP流转名单=旧np_customer_pool合并550);⚠️与 gaode_potential_customer(高德潜客,来源=高德)分开
```

### 📥 数据导入口径（data-import 页面「智能批量导入」，`src/_smart_import.py`）

7 张 Excel 自动识别 + 按规则入库：
- **按整月更新**（删该月 + 纯 INSERT，绝不跨月误删）：install_redpack(上线时间) / product_flow·FX601(上线时间) / visit_record 代理商=拜访活动明细表(拜访时间,过滤大华) / visit_record 大华=SMB客户拜访(拜访时间) / distribution_info=铺货明细(提交铺货时间;2026-07新版57列按名映射DIST_COLMAP,上级/下级客户沿用旧库列名,+14新字段存而不用;加数据时点=导出日期)
- **全量替换**（删全表 + 插）：provider_contract=服务商签约明细表；signed_customer_monthly=「11_签约客户月度进度表」(主键客户编码，**≠** dealer_si_snapshot)；provider_profile=「SMB客户沙盘管理表(服务商沙盘)」(客户编码空=外部潜客线索自动剔除)
- **按数据时点快照替换**：gaode_potential_customer=「潜客明细表」(全量潜客池,来源高德/一站式/NP流转;**≠** np_transfer_customer=NP转入表独立维护;导出可能按来源筛选);product_line_balance=「RP10-SMB产品线分析-客户」(双表头行3=字段,45列,本年累计到月;同时点重导替换、跨时点累积季度快照;产品均衡政策:CCTV占比≥70% 且 数通>3%或配套>10% → 1%返点,季度结算年度补齐;page41·⚖️代理商产品均衡)

### 📣 市场推广专题导入（独立于 `_smart_import.py`）

页面 `pages/26·📣 市场推广ROI.py`，解析与建表 `src/_marketing_roi.py`，CLI `migrations/import_marketing_roi.py`。

| 文件 | 目标表/来源类型 | 粒度 |
|---|---|---|
| 市场推广费用_城市维度分析 | `marketing_region_snapshot / city` | 快照×城市 |
| 市场推广费用_区县维度分析 | `marketing_region_snapshot / district` | 快照×城市×区县 |
| 市场推广费用_一级客户维度 | `marketing_dealer_snapshot` | 快照×代理商业务/渠道拆分行 |
| 市场推广费用_服务商维度 | `marketing_provider_snapshot` | 快照×门头服务商 |
| 会议分析 | `marketing_meeting_snapshot` | 快照×ADSPID |
| 会议沙龙参会客户明细表 | `marketing_meeting_attendee` | 快照×会议×客户×账号/参与人 |

- 快照日取文件名最后一个 `YYYYMMDD`；同快照同来源重导整体替换，历史快照保留。
- 六文件先解析和勾稽，再单事务写入；失败整体回滚。相同 SHA-256 文件自动跳过，修订版替换旧批次。
- **不同粒度是同一批费用的不同视角，禁止跨城市/区县/代理商/服务商层级相加。**

### 🚫 无价值客户红线(任何任务/名单生成必须排除)

`provider_contract.管理标签='授牌服务商'` = **无价值客户**(~2,490/9,767 家):不进任何重点任务分配,业务员不跑动。
中央过滤器 `_provider_flags.worthless_codes()/worthless_names()`;已应用: GTM推荐/月度跑动任务/page02派单/待激活看板。
`='授权服务商'` 为正常客户。GTM 冷却期:近 2 个任务月已在 gtm_target 的客户(含「任务推广列表」历史导入,来源='历史导入(任务推广列表)')不重复推荐。

### ⚠️ 字段名易踩坑（**写 SQL 前先 `PRAGMA table_info(表名)` 看一眼**）

| 你想表达 | `provider_profile` 字段 | `provider_contract` 字段 | `install_redpack_v` 字段 |
|---------|----------------------|-------------------------|------------------------|
| 服务商名 | `公司名称` | `客户名称` | `上线客户名称` |
| 城市 | `地市` | `客户城市` | `上线客户地市` |
| 区县 | `区县` | `客户区县` | `上线客户区县` |
| 客户编码 | `客户编码` | `客户编码` | `上线客户编码` |
| 一级代理商 | `上级客户名称` | `上级客户名称` / `所属一级客户` | `所属一级客户` |
| 老板 | `老板姓名` / `老板电话` | — | — |

**禁止猜字段名**。`pp.所属区县` ❌ → `pp.区县` ✅。

## 三、⭐⭐⭐ 双口径 — 最关键的概念

**同一台设备在两个表里货值可能差 5-20%**。混用 = 数字出错。

| 口径 | 视图 | 货值字段 | 含义 | 用在哪 |
|------|------|---------|------|--------|
| **🌐 全量感知** | `product_flow_v` | `最新分销价` | 全市出货（含直销/异省货）| SO 完成率、出货走势、市场盘子 |
| **🎯 红包扫码** | `install_redpack_v` | `产品现有分销价` | 服务商绑定上线的部分 | 服务商行为分析、ROI、RFM、激活率 |
| **绑定率** | 两表关联 | `红包货值 / 全量货值` | 销售链路覆盖度 | 0-100%，反映服务商网络效率 |

### 🚫 禁用字段（**绝不**用作 SO 金额）

| 字段名 | 为什么不用 |
|--------|----------|
| `下单时单价` | 单台价，不是总额 |
| `下单分销价` | 下单时合同价，与实际激活时差异大 |
| `产品分销价` | 老版字段，已不维护 |
| `KPI金额`(派生) | 双口径混合，AI 自己算容易出错 |

**唯二正确公式**：
```sql
-- ✅ 全量感知 SO（用于 KPI 完成率）
SELECT SUM(最新分销价) / 10000 AS SO_万 FROM product_flow_v WHERE ...

-- ✅ 红包扫码 SO（用于服务商行为）
SELECT SUM(产品现有分销价) / 10000 AS SO_万 FROM install_redpack_v WHERE ...
```

### 用哪个口径？

| 问题 | 用哪个 |
|------|-------|
| 「X 城市/区县 SO 完成率」 | 🌐 全量感知 |
| 「X 服务商货值多少」 | 🎯 红包扫码 |
| 「X 代理商旗下 SO」 | 🎯 红包扫码（`product_flow.所属一级客户` 99% 为空，不可用）|
| 「服务商 RFM」 | 🎯 红包扫码 |
| 「项目直销 / 大客户走了多少货」 | 看 全量 - 红包 的差 |

## 四、⭐ 核心指标公式

### 1. SO 完成率（全量感知口径）
```
应达成 = SUM(kpi_targets.SO目标_万 × kpi_rhythm.占比)  -- 按城市/区县过滤
实际 = SUM(product_flow_v.最新分销价) / 10000
完成率 = 实际 / 应达成
```

**节奏比例**：从 `kpi_rhythm` 取 `指标 LIKE '省区SO进度条%'` 的月度 `占比`。
- 2026 年 1-4 月累计占比 = 25.5%（1月 7.5% + 2月 3.0% + 3月 7.5% + 4月 7.5%）

### 2. 服务商等级 vs 货值档（**两个概念，别混**）

| 概念 | 来源 | 取值 | 用途 |
|------|------|------|------|
| **服务商等级**（真正的）| `provider_contract.服务商等级` 原始（**26 年官方评定**）/ master「服务商等级_原始」| v0服务商~v5服务商 → V0-V5 **六档** | 展示「服务商等级」就读这个 |
| **货值档**（≠服务商等级）| `provider_tier_v.服务商等级` 那列（列名误导）/ `_metrics_rfm.tier_of` / 全历史累计上线货值分层 | V4/V3/V2/已激活/v0 **五档** | 派单/激活/漏跑/ROI 等按货值体量挑重点客户 |

**绝不再把货值档叫「服务商等级」**。展示等级 → provider_contract 原始；按货值体量找客户 → 货值档。

#### 货值档阈值（货值口径，**这是货值档不是服务商等级**）

| 货值档 | 阈值（全历史累计 `产品现有分销价`）|
|------|------------------------|
| V4 | ≥ 3 万元 |
| V3 | 1-3 万元 |
| V2 | 1 千 - 1 万元 |
| 已激活 | 0 - 1 千元（有过任何上线）|
| v0 | 从未上线 |

### 3. RFM 三维度（**只算红包扫码口径**）

| 维度 | 计算 | 阈值 |
|------|------|------|
| **R**（最近上线距今）| `(asof - MAX(上线时间)).days` | ≤30🟢 / 30-60🟡 / >60🔴 |
| **F**（12 月内不同上线日数）| `COUNT(DISTINCT DATE(上线时间)) WHERE 上线时间 >= asof - 365` | ≥12🟢 / 3-11🟡 / <3🔴 |
| **M**（12 月内累计货值）| `SUM(产品现有分销价) WHERE 上线时间 >= asof - 365` | （对照货值档 v0/已激活/V2/V3/V4）|

**RFM 9 宫格业务标签**：

| | F🟢 高频 (≥12) | F🟡 中频 (3-11) | F🔴 低频 (<3) |
|---|---|---|---|
| **R🟢 活跃 (≤30)** | 核心客户 | 健康偶发 | 偶发活跃 |
| **R🟡 预警 (30-60)** | 高频疏远 | 预警客户 | 预警低频 |
| **R🔴 沉睡 (>60)** | 历史活跃已沉睡 | 沉睡待救援 | 低价值死户 |

### 4. 业务员跑动评估（page 06 两步框架）

#### Step 1 · 跑动选择合理性（评估**所有**当期拜访）

| 分类 | 判定 |
|------|------|
| ✅ 主动救援预警 | 拜访时客户 R > 30 天 或 F < 3 次 |
| ✅ 健康维护 | 拜访时客户 R ≤ 30 且 F ≥ 3 |
| ✅ 新客探访 | 历史无上线（前 2 次拜访合理）|
| ⚠️ 代理商办公室打卡 | 拜访客户名 = 该业务员负责的代理商名 |
| 🚨 严重漏跑 | scope 内 V3/V4 客户期初 R≤60，期末 R>60，且本期未拜访 |
| ⚠️ 一般漏跑 | scope 内 V2/已激活 同上滑落 |
| 🎭 马甲拜访 | 仅标识，不算选择失误 |

#### Step 2 · 跑动结果（仅评估首拜访 + 30 天 ≤ DB 末日的）

| 分类 | 判定 |
|------|------|
| ✅ A1 救援成功 | 拜访意图=救援 + 30 天内有激活 |
| ❌ B1 救援未果(R 已恶化) | 拜访时 R >60 + 后 30 天仍无激活 |
| ❌ B2 救援未果(R 预警) | 拜访时 R 30-60 + 后 30 天仍无激活 |
| ✅ E0 维护稳定 | 拜访意图=维护 + 后 30 天虽无激活但当期末 R ≤30 |
| ✅ E1 维护成功 | 拜访意图=维护 + 后 30 天有激活 |
| ⚠️ E3 维护不到位 | 拜访时 R 健康，当期末 R 滑到 30-60 |
| ❌ E2 维护失败 | 拜访时 R 健康，当期末 R 滑到 >60 |
| ⏳ ?. 窗口超 DB | 首拜访 + 30 天超过 DB 最新日 → 无法评估 |
| 🌱 D 新客探访 | 暂不评结果 |

### 5. 异常服务商 3 类（统称「低效服务商」，原叫「假服务商」已弃用）

| 类型 | 判定 | 置信度 |
|------|------|-------|
| 🎭 **马甲** | 在 `vest_account` 表里（人工维护清单）| 高 |
| ☂️ **伞形** | 同 `老板姓名` + `老板电话` 在 `provider_profile` 注册 ≥ 2 家 | 中 |
| ❌ **低效签约** | `provider_contract.签约日期` 距 DB 最新日 ≥ 60 天 且 `install_redpack` 累计 ≤ 2 台 | 中（启发式）|
| 🚫 **明确无采购意向** | 在 `closed_provider` 表里（业务方人工标注，例如 wx123.xlsx）| **最高** |

**closed_provider 表字段**：`客户编码 / 客户名称 / 城市 / 区县 / 标记类型 / 上级客户 / 服务商等级_原始 / 年安防采购量_万 / 签约日期 / 联系人 / 联系电话`
**所有任务相关查询都应优先排除 closed_provider 客户**（已被业务方明确放弃，不应再派单/跑动/统计 ROI）。

### ⚔️ 重点开拓客户 — 与「低效」相反

| 类型 | 判定 | 业务含义 |
|------|------|--------|
| ⚔️ **竞品 Top 服务商** | 在 `competitor_top_provider` 表里（业务方人工标注）| 海康/宇视等核心服务商，**资源应优先倾斜**，争取把竞品份额转化为大华 |

**competitor_top_provider 表字段**：`客户编码 / 客户名称 / 省份 / 城市 / 区县 / 责任人角色 / 责任人姓名 / 老板姓名 / 老板手机号 / 客户经营品牌 / 在售大华(0/1) / 竞品体量_万 / 拜访内容 / 转化策略 / 备注`

- **在售大华** = 1 表示该客户已在卖大华+海康（混卖），开拓难度小
- **在售大华** = 0 表示纯卖竞品，开拓难度大但潜力高
- 任务分配 / 派单 应**优先**包含这批客户，给责任人（来自表里）派活

### 6. 红包 ROI

```
全省 ROI 基线 = SUM(全省 中奖金额) / SUM(全省 产品现有分销价)
服务商 ROI = SUM(该服务商 中奖金额) / SUM(该服务商 产品现有分销价)
偏离度 = 服务商 ROI / 全省 ROI

> 1.5x = 高偏离（红包多 / 上线少）→ 跟进施压
< 0.5x = 低偏离（红包少 / 上线高，且上线 ≥ 1 万）→ 忠诚度风险
```

### 7. 市场推广费用与 SO 效率

#### 表选择铁律

| 问题 | 权威表 |
|---|---|
| 全省/城市总费用 | `marketing_region_snapshot WHERE source_type='city'` |
| 区县总费用 | `marketing_region_snapshot WHERE source_type='district'` |
| 代理商费用分摊 | `marketing_dealer_snapshot`，按代理商聚合 |
| 门头服务商明细 | `marketing_provider_snapshot` |
| 单场会议效率 | `marketing_meeting_snapshot` |
| 参会人/公司/券 | `marketing_meeting_attendee` |

**城市、区县、代理商、服务商是同一费用的不同层级，不能相加。**总费用只从一个区域层级取；代理商/服务商用于下钻。

```text
同期区域 SO = SUM(product_flow_v.最新分销价)，当年1月1日至 snapshot_date
费用/SO = marketing_region_snapshot.total_invest / 同期区域 SO
推广会产出投入比 = marketing_meeting_snapshot.post_redpack_output / meeting_expense
门头产出投入比 = marketing_provider_snapshot.annual_redpack_output / storefront_invest
```

- `post_redpack_output`、`annual_redpack_output` 是安装红包关联 SO，不是全量感知 SO。
- 没有毛利、净利润、可比基线，**不能称利润 ROI，不能把关联 SO 全部解释为活动增量**；统一用“产出投入比”。
- 代理商费用源表没有地市字段；服务商门头明细没有区县字段。区县门头只能看 `marketing_region_snapshot source_type='district'` 汇总。
- 每次查询先取 `marketing_import_batch status='success'` 的最新 `snapshot_date`，所有 `marketing_*` 表用同一快照。

### 8. 时段定义（同环比）

| 时段 | 定义 |
|------|------|
| **当期** | 用户选的起止月 |
| **同期** | 去年同期同月数（当期 - 12 个月）|
| **环期** | 紧邻当期前 N 个月，N = 当期月数 |

## 五、业务员 × 区县 × 代理商 关系

```
salesperson_scope 表（仅大华自营业务员有）：
  - 业务员（如 孙鲁江）
  - 市 / 区县 / 代理商  (一对多)
  - 一个业务员可负责多个 (区县, 代理商) 组合
```

**代理商业务员暂无 scope 定义**，所以「漏跑」类指标只对大华业务员有效。

## 六、用户与权限

```
app_user：username / role / enabled
  - role：admin（全权）/ manager（按 scope）/ salesperson（按 scope）/ guest
app_user_scope：username / scope_type / scope_value
  - scope_type：city / district / salesperson / dealer
  - 白名单：勾的越少看的越多（每多勾一个维度 = AND 过滤越严）
```

**admin 自动绕过所有 scope**。manager/salesperson/guest 按 scope 过滤。

## 七、跑动任务（V3 新增）

```
salesperson_task：
  - 任务年月 / 地市 / 业务员 / 客户编码 / 任务类型 / 优先级
  - 确认状态：待确认 / 已确认 / 已剔除 / 业务员替换
  - 完成状态：未完成 / 已拜访 / 已激活 / 逾期
  - 月底 verify_tasks() 比对 visit_record + install_redpack 写回状态
```

**任务分配逻辑**（按 scope）：
- 🚨 高：scope 内 V3/V4 严重漏跑
- ⚠️ 中：scope 内 B 类救援未果 + E3 维护不到位
- 🟢 低：scope 内 V3+ 但 F<3

## 八、所有 page 功能与数据源

| 页面 | 功能 | 主要数据源 |
|------|------|----------|
| 智能搜索 | AI 推荐功能页面 | `_page_registry.PAGES` |
| SO 环比分析 | 任意两月对比 5 维度 | `product_flow_v` |
| 大 PK | 业务员/区域排行 | `visit_record_v` + 各表 |
| 销售机会发现 | 未开发客户 / 增量空间 | `install_redpack_v` + `provider_profile` |
| 红包排行榜 | 红包排名 | `install_redpack_v.中奖金额` |
| 客户分群（RFM）| RFM 9 宫格 | `install_redpack_v` |
| 智能派单台 | 客户跑动指派 | `install_redpack_v` + `visit_record_v` |
| 服务商画像 | 单服务商雷达 | `install_redpack_v` + `provider_master` |
| 服务商行为分析 | 流失 / 混合采购 | `install_redpack_v` |
| 代理商能力评分 | 一级代理 F7 评分 | `install_redpack_v.所属一级客户` |
| 业务员跑动评估 | RFM-Impact 两步评估 | `visit_record_v` + `install_redpack_v` + `salesperson_scope` |
| 跑动任务管理 | 月度任务分配 / 核验 | `salesperson_task` 表 |
| 拜访激励效果 | 拜访 → 上线漏斗 | `visit_record_v` + `install_redpack_v` |
| 假商团伙挖掘 | 马甲 / 伞形 / 低效 | `vest_account` + `provider_profile` + `provider_contract` |
| 产品流向同环比 | 区县同比 | `product_flow_v` |
| 地市/区县/代理商 全景图 | 4 主题汇总 | 上述视图组合 |
| 月度经营报告（AI）| 6 章 docx | 数据汇总 + AI 写 |
| 会议纪要校验（AI）| 抽取声明 + DB 核对 | LLM + tools |
| AI 代码助手 | Docker 沙箱跑 Claude | 全 DB |
| 用户与权限 | admin 管理 | `app_user` / `app_user_scope` |
| 市场推广费用与 SO 效率 | 费用总览、城市/区县、代理商、推广会、门头服务商、数据质量 | `marketing_*` 六张快照/审计表 + `product_flow_v` |

## 九、常用 SQL 模板

### 城市/区县 SO 完成率
```sql
-- 实际（全量感知）
SELECT ROUND(SUM(最新分销价)/10000, 2) AS 实际_万
  FROM product_flow_v
 WHERE 上线城市 = '杭州市' AND 上线年月 BETWEEN '2026-01' AND '2026-04';

-- 应达成
SELECT SUM(t.SO目标_万 * r.占比) AS 应达成_万
  FROM kpi_targets t
  JOIN kpi_rhythm  r ON r.年度 = t.年度
 WHERE t.城市 = '杭州市' AND t.年度 = 2026
   AND r.指标 LIKE '省区SO进度条%'
   AND r.月份 IN (1, 2, 3, 4);
```

### 代理商 SO（**用红包口径**，product_flow 没所属一级客户）
```sql
SELECT ROUND(SUM(产品现有分销价)/10000, 2) AS SO_万
  FROM install_redpack_v
 WHERE 上线客户地市 = '杭州市'
   AND 所属一级客户 = '杭州常业科技有限公司'
   AND 上线年月 BETWEEN '2026-01' AND '2026-04';
```

### 服务商 RFM（asof = DB 最新日）
```sql
WITH asof AS (SELECT MAX(上线时间) AS d FROM install_redpack_v),
     g AS (
        SELECT 上线客户编码,
               julianday((SELECT d FROM asof)) - julianday(MAX(上线时间)) AS R,
               COUNT(DISTINCT date(上线时间)) FILTER (
                 WHERE 上线时间 >= datetime((SELECT d FROM asof), '-365 day')
               ) AS F_12mo,
               SUM(产品现有分销价) FILTER (
                 WHERE 上线时间 >= datetime((SELECT d FROM asof), '-365 day')
               ) AS M_12mo
          FROM install_redpack_v
         WHERE 上线客户编码 IS NOT NULL
         GROUP BY 上线客户编码
     )
SELECT * FROM g;
```

### 市场推广地市效率
```sql
WITH snap AS (
  SELECT MAX(snapshot_date) d FROM marketing_import_batch WHERE status='success'
), so AS (
  SELECT 上线城市 city, SUM(最新分销价) so_ytd
    FROM product_flow_v, snap
   WHERE date(上线时间) BETWEEN substr(snap.d,1,4)||'-01-01' AND snap.d
   GROUP BY 上线城市
)
SELECT r.city,
       r.total_invest AS 市场费用,
       so.so_ytd AS 同期SO,
       r.total_invest/NULLIF(so.so_ytd,0) AS 费用_SO,
       r.meeting_output_30d/NULLIF(r.meeting_invest,0) AS 推广会产出投入比,
       r.provider_storefront_output/NULLIF(r.provider_storefront_invest,0) AS 门头产出投入比
  FROM marketing_region_snapshot r
  JOIN snap ON snap.d=r.snapshot_date
  LEFT JOIN so ON so.city=r.city
 WHERE r.source_type='city';
```

### 市场推广代理商分摊
```sql
WITH snap AS (
  SELECT MAX(snapshot_date) d FROM marketing_import_batch WHERE status='success'
)
SELECT dealer_code, dealer_name,
       SUM(total_invest) AS 已分摊费用,
       SUM(performance_ytd) AS 累计业绩,
       SUM(total_invest)/NULLIF(SUM(performance_ytd),0) AS 费用_累计业绩
  FROM marketing_dealer_snapshot d
  JOIN snap ON snap.d=d.snapshot_date
 GROUP BY dealer_code, dealer_name;
```

## 十、🚨 易踩坑清单（写 SQL 前对照检查）

| ❌ 错的 | ✅ 对的 |
|--------|--------|
| 全量感知用 `KPI金额` / `下单时单价` | 用 `最新分销价` |
| 代理商 SO 用 `product_flow_v` 过滤 `所属一级客户` | 用 `install_redpack_v`（pf 表 99% 缺该字段）|
| `pp.所属区县` / `pp.客户名称` | `pp.区县` / `pp.公司名称` |
| `pc.公司名称` | `pc.客户名称` |
| 直接读 `visit_record` 计拜访量 | 用 `visit_record_v` 加 `_打卡异常无效=0 AND _真异常打卡=0` |
| 当期完成率 = 实际 / 年度目标 | 实际 / (年度目标 × 1-N 月节奏比例累计) |
| RFM 用 `下单时间` 当 R | 用 `上线时间`（红包扫码时刻才是真实激活）|
| 把全历史激活说成 "本期激活" | 区分清楚：全历史 vs 当期窗口 |
| 把城市+区县+代理商+服务商费用相加 | 只选一个区域层级做总盘，代理商/服务商只下钻 |
| 把会后关联 SO / 费用称为利润 ROI | 叫“产出投入比”，注明安装红包关联 SO、非因果增量 |
| 用 `marketing_provider_snapshot` 做区县门头明细 | 该表无区县；区县汇总读 `marketing_region_snapshot source_type='district'` |
| 假设 `(adsp_id,customer_code)` 在参会表唯一 | 同公司可多人参会；明细用 `row_key`，公司数另做 DISTINCT |

## 十一、推荐工作流（任务执行 SOP）

```
1. 读完本 SKILL（含字段表 + 口径选择）
2. 写 SQL 之前先 PRAGMA table_info(表名) 验字段
3. 用户问 SO → 用全量感知口径；问服务商 → 用红包口径
4. 算同环比 → 当期 / 同期 / 环期 三个窗口的 SQL 分别跑
5. 输出 markdown / docx 时，每个引用数字标注口径 + 时段
6. 完成前再读一遍铁律和易踩坑清单 自检
```
