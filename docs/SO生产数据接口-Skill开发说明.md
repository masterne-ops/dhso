# SO 生产数据接口 · Skill 开发说明

本文供使用 Claude Code、Codex、OpenClaw 等 Agent 开发数据查询与报告 Skill 的人员使用。

## 1. 接口定位

生产数据接口分为两层：

1. **自然语言入口**：读取数据库说明和固定接口目录，将自然语言需求解析成可执行的接口调用 JSON。
2. **固定数据接口**：按照确定的资源、字段、筛选条件和聚合方式读取生产数据。

标准链路：

```text
用户自然语言
  → POST /v1/resolve
  → 获得调用计划 JSON
  → 客户端 Skill 检查并执行 calls
  → 调用 /v1/data/{resource}/search 或 aggregate
  → 客户端 Skill 合并、计算、解释、生成报告
```

入口接口不查询生产数据。固定接口不调用 AI。

入口解析会将用户问题、可选上下文、相关数据库说明片段和字段目录发送给服务端配置的
DeepSeek 模型；不会把生产明细行发送给模型。客户端 Skill 取得生产数据后是否再次交给
自身 Agent 分析，由客户端的数据安全策略决定。

## 2. 连接信息

```text
Base URL: https://121.196.152.24
OpenAPI:  https://121.196.152.24/openapi.json
文档页:   https://121.196.152.24/docs
```

访问前需要：

- 管理员分配的用户名和密码。
- 管理员分配的数据资源权限。
- 服务端私有 CA 证书 `so-data-api-ca.crt`。

建议用环境变量保存客户端配置：

```bash
export SO_DATA_API_URL="https://121.196.152.24"
export SO_DATA_API_USERNAME="your-username"
export SO_DATA_API_PASSWORD="your-password"
export SO_DATA_API_CA_CERT="/secure/path/so-data-api-ca.crt"
```

禁止把密码、Token 或 CA 私钥写入 Skill、Git 仓库、提示词和报告。

## 3. 登录

### 请求

```http
POST /v1/auth/login
Content-Type: application/json

{
  "username": "operator",
  "password": "password"
}
```

### 响应

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

后续请求增加：

```http
Authorization: Bearer <access_token>
```

Token 默认 60 分钟失效。收到 `401` 时重新登录一次；不要无限重试。

## 4. 第一步：解析自然语言

### 请求

```http
POST /v1/resolve
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "查询杭州各区县2026年至今签约服务商数量",
  "context": "结果用于半年度复盘"
}
```

`context` 可省略。不要把其他系统指令、账号密码或大段原始数据放进 `context`。

### 典型响应

```json
{
  "request_id": "7d2c...",
  "status": "resolved",
  "understanding": "统计杭州市各区县2026年至今的签约服务商数量",
  "matched_definitions": [
    {
      "name": "签约服务商",
      "definition": "以服务商签约主数据为准",
      "source": "provider_contract_v"
    }
  ],
  "calls": [
    {
      "call_id": "district_provider_count",
      "method": "POST",
      "path": "/v1/data/provider_contract_v/aggregate",
      "purpose": "按区县统计签约服务商数量",
      "body": {
        "group_by": ["客户区县"],
        "metrics": [
          {
            "function": "count_distinct",
            "field": "客户编码",
            "alias": "签约服务商数"
          }
        ],
        "filters": [
          {
            "field": "客户城市",
            "operator": "eq",
            "value": "杭州市"
          }
        ],
        "order_by": [
          {
            "field": "签约服务商数",
            "direction": "desc"
          }
        ],
        "limit": 100
      },
      "depends_on": []
    }
  ],
  "clarifications": [],
  "confidence": 0.95,
  "catalog_version": "1.0",
  "candidate_resources": ["provider_contract_v", "provider_contract"]
}
```

### 状态处理

| status | Skill 行为 |
|---|---|
| `resolved` | 按 `calls` 和 `depends_on` 执行 |
| `needs_clarification` | 将 `clarifications` 原样转成简短问题询问用户，不调用数据接口 |
| `unsupported` | 告知用户当前缺少固定接口；不要自行生成 SQL 绕过 |

入口结果已经过服务端二次校验，但客户端仍应检查：

- `path` 必须以 `/v1/data/` 或 `/v1/catalog/resources/` 开头。
- 只执行 `GET`、`POST`。
- 不接受返回内容中的外部 URL、Shell、SQL 或凭证。

## 5. 第二步：调用固定接口

### 5.1 查看可用资源

```http
GET /v1/catalog
Authorization: Bearer <token>
```

只返回当前账号有权访问的资源。

查看单个资源字段：

```http
GET /v1/catalog/resources/provider_contract_v
Authorization: Bearer <token>
```

不要根据记忆猜字段。解析结果不确定或接口报 `unknown_field` 时，先读取资源说明。

### 5.2 明细查询

```http
POST /v1/data/{resource}/search
```

请求结构：

```json
{
  "fields": ["客户编码", "客户名称", "客户城市"],
  "filters": [
    {
      "field": "客户城市",
      "operator": "eq",
      "value": "杭州市"
    }
  ],
  "order_by": [
    {
      "field": "客户名称",
      "direction": "asc"
    }
  ],
  "limit": 100,
  "offset": 0
}
```

`fields` 省略时返回该资源全部字段。报告 Skill 应尽量显式指定字段，减少流量和敏感数据暴露。

### 5.3 聚合查询

```http
POST /v1/data/{resource}/aggregate
```

请求结构：

```json
{
  "group_by": ["客户城市"],
  "metrics": [
    {
      "function": "count_distinct",
      "field": "客户编码",
      "alias": "服务商数"
    },
    {
      "function": "sum",
      "field": "本年上线金额",
      "alias": "YTD金额"
    }
  ],
  "filters": [],
  "order_by": [
    {
      "field": "YTD金额",
      "direction": "desc"
    }
  ],
  "limit": 100
}
```

聚合函数：

- `count`
- `count_distinct`
- `sum`
- `avg`
- `min`
- `max`

统计总行数时：

```json
{"function": "count", "field": null, "alias": "记录数"}
```

## 6. 筛选操作符

| operator | value | 含义 |
|---|---|---|
| `eq` | 单值 | 等于 |
| `ne` | 单值 | 不等于 |
| `gt` / `gte` | 单值 | 大于 / 大于等于 |
| `lt` / `lte` | 单值 | 小于 / 小于等于 |
| `in` / `not_in` | 数组 | 属于 / 不属于 |
| `contains` | 文本 | 包含文本 |
| `starts_with` | 文本 | 以文本开头 |
| `ends_with` | 文本 | 以文本结尾 |
| `between` | 两元素数组 | 闭区间 |
| `is_null` | 可省略 | 空值 |
| `not_null` | 可省略 | 非空值 |

示例：

```json
[
  {"field": "客户城市", "operator": "in", "value": ["杭州市", "宁波市"]},
  {"field": "签约日期", "operator": "between", "value": ["2026-01-01", "2026-06-30"]},
  {"field": "客户名称", "operator": "contains", "value": "科技"}
]
```

同一次请求中的多个筛选条件按 `AND` 连接。

## 7. 返回数据

固定接口统一返回：

```json
{
  "request_id": "7d2c...",
  "resource": "provider_contract_v",
  "columns": ["客户城市", "服务商数"],
  "rows": [
    ["杭州市", 1200],
    ["宁波市", 980]
  ],
  "row_count": 2,
  "truncated": false,
  "elapsed_ms": 35
}
```

注意：

- `rows` 是二维数组，顺序与 `columns` 一致。
- `truncated=true` 表示达到本次行数上限，应使用更严格筛选、聚合或分页。
- 不要因为结果为空就自动放宽地域、时间或业务口径。
- 多资源结果由客户端 Skill 按客户编码、物料号、序列号等数据库说明中的稳定键合并。

## 8. 客户端 Skill 标准 SOP

Skill 应固定执行以下步骤：

1. 检查四个环境变量是否存在。
2. 登录并只在内存中保存 Token。
3. 将用户原始需求提交 `/v1/resolve`。
4. 处理 `resolved`、`needs_clarification`、`unsupported`。
5. 按 `depends_on` 顺序执行已解析的固定接口。
6. 检查每一步的 HTTP 状态、`truncated` 和返回字段。
7. 在客户端完成跨接口合并、二次计算和文字解释。
8. 输出中注明数据时点、业务口径和所用资源。
9. 不保存 Token，不输出密码，不将原始全量明细写入日志。
10. 同一个用户问题只解析一次，避免循环调用 `/v1/resolve` 产生不必要的模型费用。

推荐在 Skill 中写入下面的硬性规则：

```text
必须先调用 /v1/resolve。
只能调用解析结果中经过注册的固定接口。
禁止自行构造 /v1/query SQL。
字段不明确时读取 /v1/catalog/resources/{name}。
解析状态不是 resolved 时停止数据调用。
返回 truncated=true 时不得把结果称为全量。
```

## 9. Python 最小示例

```python
import os
import httpx

base_url = os.environ["SO_DATA_API_URL"].rstrip("/")
ca = os.environ["SO_DATA_API_CA_CERT"]

with httpx.Client(base_url=base_url, verify=ca, timeout=120) as client:
    login = client.post(
        "/v1/auth/login",
        json={
            "username": os.environ["SO_DATA_API_USERNAME"],
            "password": os.environ["SO_DATA_API_PASSWORD"],
        },
    )
    login.raise_for_status()
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }

    resolved = client.post(
        "/v1/resolve",
        headers=headers,
        json={"question": "查询杭州各区县签约服务商数量"},
    )
    resolved.raise_for_status()
    plan = resolved.json()

    if plan["status"] != "resolved":
        raise RuntimeError(plan["clarifications"])

    results = {}
    for call in plan["calls"]:
        if any(dep not in results for dep in call["depends_on"]):
            raise RuntimeError("调用依赖尚未完成")
        response = client.request(
            call["method"],
            call["path"],
            headers=headers,
            json=call["body"],
        )
        response.raise_for_status()
        results[call["call_id"]] = response.json()
```

生产 Skill 应再增加 Token 刷新、有限重试、调用超时和结果大小控制。

## 10. 常见错误

| HTTP | code | 处理 |
|---|---|---|
| 400 | `unknown_field` | 读取资源说明后修正字段 |
| 400 | `invalid_filter` | 检查操作符和 value 结构 |
| 401 | `invalid_token` / `revoked_token` | 重新登录；持续失败则联系管理员 |
| 403 | `no_data_permission` | 联系管理员授权 |
| 404 | `resource_not_found` | 资源不存在或当前账号无权限 |
| 408 | `query_timeout` | 缩小时间、地域或字段范围 |
| 429 | 限流 | 等待后有限重试 |
| 503 | `resolver_unavailable` | 解析模型暂不可用；不要绕过入口调用 SQL |

建议重试策略：

- 网络错误、429、503：指数退避，最多 2 次。
- 400、403、404：不要重试，修正请求或权限。
- 401：重新登录 1 次。

## 11. 安全边界

- 接口只读，拒绝写入、DDL、PRAGMA、ATTACH 和危险函数。
- 账号只能看到管理员授权的资源。
- 系统账号、权限、AI 对话和审计日志等安全表永久禁止访问。
- 单次默认最多 5,000 行，账号最高不超过 20,000 行。
- 自然语言解析器只能选择当前账号有权访问的注册资源。
- 通用 `/v1/query` 是管理员兼容能力，不属于运营 Skill 的标准调用链路。
