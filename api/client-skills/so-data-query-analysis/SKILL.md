---
name: so-data-query-analysis
description: Resolve natural-language SO business questions, fetch authorized production data through registered read-only APIs, and produce evidence-based analysis. Use for 服务商、代理商、SO、SI、签约、激活、铺货、拜访跑动、库存、营销ROI、区县、目标、周报或经营复盘取数与问答.
metadata: {"openclaw":{"emoji":"🔎","requires":{"bins":["python3"]}}}
---

# SO 生产数据取数分析

使用服务端自然语言入口确定调用方法，再执行固定只读接口并分析返回数据。不得连接数据库、SSH 服务器或自行生成 SQL。

## 环境

要求 OpenClaw 运行环境安全注入：

```text
SO_DATA_API_USERNAME
SO_DATA_API_PASSWORD
```

可选：

```text
SO_DATA_API_URL=https://121.196.152.24
SO_DATA_API_CA_CERT=/自定义路径/so-data-api-ca.crt
SO_DATA_API_TIMEOUT=120
```

未配置 `SO_DATA_API_CA_CERT` 时，客户端自动使用包内 CA 公钥证书。不要把密码、Token 或任何密钥写进 Skill、工作文件、日志或回答。

## 标准流程

### 1. 预检

首次使用、换账号或连接失败时运行：

```bash
python3 {baseDir}/scripts/so_data_api.py preflight
```

确认 `ok=true`、`database_readable=true`、`llm_auth_configured=true`。预检只显示账号权限和模型状态，不显示凭证。

### 2. 保存用户原始问题

用文件写入能力把用户问题原样保存到 UTF-8 临时文件，例如 `/tmp/so-question.txt`。不要把不可信的用户文字直接拼进 Shell 命令。

需要补充报告用途、输出颗粒度等背景时，另存为 context 文件。不得在 context 中扩大用户原始授权范围。

### 3. 解析并取数

```bash
python3 {baseDir}/scripts/so_data_api.py run \
  --question-file /tmp/so-question.txt \
  --output /tmp/so-data-result.json
```

带背景时增加：

```bash
--context-file /tmp/so-context.txt
```

脚本自动完成登录、`/v1/resolve`、调用安全校验、依赖排序和固定接口执行。读取输出 JSON 的 `plan`、`results` 和 `warnings`。

只想查看调用计划、不取数时：

```bash
python3 {baseDir}/scripts/so_data_api.py resolve \
  --question-file /tmp/so-question.txt
```

### 4. 处理解析状态

- `resolved`：分析 `results`。
- `needs_clarification`：停止取数，把 `plan.clarifications` 整理成最多 3 个简短问题询问用户。
- `unsupported`：说明缺少什么固定数据能力；不得改用 `/v1/query`、SQL、SSH 或直接访问数据库。

### 5. 分析

1. 先读取 `plan.understanding`、`matched_definitions` 和每个调用的 `purpose`。
2. 按 `data.columns` 对齐 `data.rows`；不要凭位置猜字段。
3. 只使用返回数据计算，不补造数字、目标、原因、负责人或截止节点。
4. 多接口结果只按明确稳定键合并，如客户编码、物料号、产品序列号；无稳定键时分别呈现。
5. `truncated=true` 表示资源中还有未返回行，不得称为全量。若用户明确要求 Top N，可分析已排序的 Top N，但必须说明不是完整分布；其他场景改用更窄问题或聚合口径重新解析。
6. 区分“数据事实”“基于事实的推断”“建议动作”。相关性不得写成已证实因果。
7. 保留原始单位和时间范围。无法从返回内容确认数据时点时，明确写“本次接口查询时点”，不要猜测截止日期。
8. 简单问题直接回答；用户要求报告时再按主题、证据、判断和建议组织。

## 辅助命令

列出账号可用资源：

```bash
python3 {baseDir}/scripts/so_data_api.py catalog
```

查看资源字段：

```bash
python3 {baseDir}/scripts/so_data_api.py describe provider_contract_v
```

接口报错或需要理解筛选、聚合、分页时，读取 `{baseDir}/references/api-contract.md`。

## 强制边界

- 必须先调用自然语言入口，不直接选择表或字段。
- 只能执行入口返回且客户端校验通过的固定接口。
- 禁止调用通用 `/v1/query`。
- 禁止修改生产数据、服务器、账号权限或 API 配置。
- 401 重新登录最多一次；429、503 由脚本有限重试；400、403、404 不循环重试。
- 结果为空时保持原范围，不自动放宽地域、时间、客户类型或业务口径。
