# 关键因子指标库 — 设计方案

> 状态：**已开发（首批 1 个因子上线）**，2026-08-01 更新
> 编写时间：2026-07-31

## 实现与本设计的偏差（2026-08-01）

代码在 `api/factors.py`，接口口径与实测数据见 `api/README.md` 的「关键因素（因子库）」一节。三处与下文原设计不同：

1. **auto 因子不走 `fetch_query` 字符串**，改为每个因子在 `factors.FETCHERS` 注册 Python 取数函数。原因：`.format()` 拼 `{geo_key}` 等于把 SQL 注入面存进数据库；且券类因子需要 join + 分子分母两个计数，单条标量 SQL 无法区分「分子为 0」是没发券还是发了没兑。`funnel_factor_defs` 保留为因子清单（名称/单位/目标/排序），`fetch_query` 列留空。
2. **无样本返回 `null` 而非 0**，且 `null` 不写缓存。发了券一张没兑是 0%，一张券都没发是「—」，两者业务含义完全不同。
3. **因子值只按周期算**，不做全年累计。券是行为数据，累计使用率对「本周该催谁」无指导意义。

4. **因子目标一律不预设**（2026-08-01 金总定）。`SEED_DEFS` 里所有 `default_target` 留空，前端也不回落演示值。因子目标库里没有下发值，各地区基数差一个量级（跑动次数尤其），预设等于拍脑袋——空着让用户按地区手填，比给一个看起来权威的假目标好。下文 §7 表格里的默认目标列已作废。

首批落地：`a2t_meet`（业务员红包券使用率）与跑动三档（`a2t_visit` / `_dahua` / `_dealer`）已出真实值；`a2t_fo`（首单礼）因库里无该券种恒为 `null`，见 README。

> **注意区分「因子目标」和「漏斗层级目标」。** 本文只管因子。漏斗三阶（授权/激活/高级）的目标另有一套口径：全年取 `provider_target` 的下发值、**只读不可改**，本期 = 全年 × 当月 `kpi_rhythm` 节奏占比、周按当月周数均分作为**预设值**。见 `api/README.md` 的「下发目标」一节。

---

## 1. 核心概念

每个转化环节（p2i / i2a / a2t / t2v）下挂若干**关键因子**。  
因子分两类：

| 类型 | 标识 | 说明 |
|------|------|------|
| **手动项** | `source = 'manual'` | 没有系统数据，由用户每期手动填入数值 |
| **自动计算项** | `source = 'auto'` | 有取数脚本，页面加载时系统自动计算并填入，用户只读 |

---

## 2. 数据库结构

### `funnel_factor_defs` — 因子定义表

```sql
CREATE TABLE funnel_factor_defs (
  id            TEXT PRIMARY KEY,   -- 唯一标识，如 'a2t_fo'
  conv_key      TEXT NOT NULL,       -- 所属转化段：p2i / i2a / a2t / t2v
  name          TEXT NOT NULL,       -- 显示名称，如 '首单礼红包券使用率'
  unit          TEXT DEFAULT '%',    -- 单位
  source        TEXT NOT NULL,       -- 'auto' | 'manual'
  fetch_query   TEXT,                -- 取数 SQL（仅 auto；支持参数占位符）
  calc_expr     TEXT,                -- 后处理 Python 表达式（可选）
  default_target REAL,              -- 默认目标值
  display_order INT  DEFAULT 0,
  enabled       INT  DEFAULT 1,
  created_at    TEXT DEFAULT (datetime('now')),
  updated_at    TEXT DEFAULT (datetime('now'))
);
```

### `funnel_factor_value_cache` — 自动因子计算结果缓存

```sql
CREATE TABLE funnel_factor_value_cache (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  factor_id   TEXT NOT NULL REFERENCES funnel_factor_defs(id),
  geo_key     TEXT NOT NULL,   -- '浙江省//' 或 '浙江省/杭州/'
  period_key  TEXT NOT NULL,   -- '2026-W31' 或 '2026-07'
  value       REAL,
  computed_at TEXT DEFAULT (datetime('now')),
  UNIQUE (factor_id, geo_key, period_key)
);
```

---

## 3. 取数脚本规范（auto 类型）

### fetch_query 占位符

SQL 中用 `{geo_key}` `{period_start}` `{period_end}` 作占位符：

```sql
-- 示例：首单礼红包券使用率
SELECT
  ROUND(COUNT(CASE WHEN first_coupon_used = 1 THEN 1 END) * 100.0 / COUNT(*), 1)
FROM service_provider_activations
WHERE geo_key LIKE '{geo_key}%'
  AND activation_date BETWEEN '{period_start}' AND '{period_end}'
```

### calc_expr（可选后处理）

Python 表达式，变量 `raw` 代表 SQL 查询的标量结果：

```python
# 示例：限制在 0-100 之间
max(0, min(100, raw))
```

---

## 4. 页面加载行为

```
页面加载
  ├── GET /api/funnel/data/{geo}/{period}      ← 漏斗绝对值（生产库）
  ├── GET /api/funnel/state/{geo}/{period}     ← 用户设置（targets/todos/manual/active）
  └── GET /api/funnel/factor-values/{geo}/{period}
        │
        ├─ 对每个 source='auto' 的因子：
        │     查 factor_value_cache
        │     ├─ 命中且未过期（< 1小时）→ 直接返回缓存值
        │     └─ 未命中/过期 → 执行 fetch_query → 写缓存 → 返回
        │
        └─ 对 source='manual' 的因子：
              从 funnel_state 的 manual 字段中取用户上次填写值
```

---

## 5. 新增自动因子的开发流程

1. **确认取数逻辑**：明确数据来自哪张表，过滤条件，聚合方式
2. **编写并测试 SQL**：在本地数据库验证查询结果合理
3. **写入因子定义**：向 `funnel_factor_defs` 插入一条记录
4. **验证页面展示**：刷新漏斗页面，确认因子值正确显示

每次新增自动因子都需要完成上述四步，无需改动前端代码。

---

## 6. 手动项说明

- 手动项没有 `fetch_query` / `calc_expr`
- 用户每期手填，值存入 `funnel_state.state_json → manual` 字段
- 下一期加载时，手动项默认为空（不跨期继承），需重新填写
- 可配置 `inherit_previous = 1` 让系统在新期首次加载时自动带入上期值（待实现）

---

## 7. 预置因子清单（待填入数据库）

| id | conv_key | name | unit | source | 状态 |
|----|----------|------|------|--------|------|
| `a2t_meet` | a2t | 业务员红包券使用率 | % | auto | ✅ 已上线（`dahua_redpack_grant`） |
| `a2t_fo` | a2t | 首单礼红包券使用率 | % | auto | ⚠️ 已接口，但库里无「首单礼」券种，恒 null |
| `a2t_visit` | a2t | 跑动次数（合计） | 次 | auto | ✅ 已上线（`visit_record`） |
| `a2t_visit_dahua` | a2t | 跑动次数（大华） | 次 | auto | ✅ 已上线（`打卡人所属公司` 为空） |
| `a2t_visit_dealer` | a2t | 跑动次数（代理商） | 次 | auto | ✅ 已上线（`打卡人所属公司` 非空） |
| `p2a_visit` | p2a | 跑动数量 | 次 | auto | 待做（`visit_record`） |
| `a2t_pa` | a2t | 推广会参会率 | % | auto | 待做（`promotion_meeting`） |
| `a2t_p61` | a2t | 推广会6+1红包使用率 | % | manual | 待做 |
| `t2v_ret` | t2v | 回归礼红包使用率 | % | auto | 待做（券种待确认） |

> 原清单里的 `p2i_*` / `i2a_*` 已随漏斗改四阶而合并为 `p2a_*`（意向降为旁注，见 `api/prod_db.py` 模块头）。

---

## 8. 未来扩展（暂不实现）

- **指标库 UI**：在漏斗页面右侧面板开放"从指标库选择"功能，管理员可在 UI 中添加/编辑因子定义
- **跨期对比**：同一因子跨周展示趋势折线
- **告警阈值**：为每个因子配置告警下限，低于阈值自动进入「异常项」
- `inherit_previous` 手动项跨期继承
