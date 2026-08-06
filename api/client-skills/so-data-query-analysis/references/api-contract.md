# 固定接口契约

## 目录

1. 返回结构
2. 固定接口
3. 筛选与聚合
4. 错误处理
5. 安全约束

## 返回结构

`run` 命令输出：

```json
{
  "ok": true,
  "question": "用户原始问题",
  "plan": {
    "status": "resolved",
    "understanding": "服务端对问题的理解",
    "matched_definitions": [],
    "calls": [],
    "clarifications": [],
    "confidence": 0.95
  },
  "results": {
    "call_id": {
      "call": {
        "method": "POST",
        "path": "/v1/data/provider_contract/aggregate",
        "purpose": "本次调用用途",
        "depends_on": []
      },
      "data": {
        "columns": ["客户城市", "服务商数"],
        "rows": [["杭州市", 1200]],
        "row_count": 1,
        "truncated": false,
        "elapsed_ms": 10
      }
    }
  },
  "warnings": [],
  "executed_at": "UTC时间"
}
```

`rows` 是二维数组，顺序必须用 `columns` 解读。

## 固定接口

- `GET /v1/catalog`：账号可访问的注册资源。
- `GET /v1/catalog/resources/{name}`：资源说明和字段。
- `POST /v1/data/{name}/search`：明细筛选。
- `POST /v1/data/{name}/aggregate`：分组聚合。
- `POST /v1/resolve`：自然语言解析，只返回调用计划。

通用 `/v1/query` 不属于本 Skill 的允许范围。

## 筛选与聚合

筛选操作符：

```text
eq ne gt gte lt lte
in not_in
contains starts_with ends_with
between is_null not_null
```

多个筛选条件按 AND 连接。

聚合函数：

```text
count count_distinct sum avg min max
```

固定接口响应达到行数上限时返回 `truncated=true`。用户明确要求 Top N 时，结果可作为 Top N 使用，但不能代表完整分布；其他情况使用更严格的范围或聚合，不要在客户端假定缺失行。

## 错误处理

| HTTP/code | 含义 | 处理 |
|---|---|---|
| 400 `unknown_field` | 字段错误 | 重新提交自然语言需求或查看资源字段 |
| 400 `invalid_filter` | 筛选结构错误 | 停止并报告，不自行改口径 |
| 401 | Token 失效 | 重新登录一次 |
| 403 `no_data_permission` | 无数据权限 | 联系管理员 |
| 404 `resource_not_found` | 不存在或无权访问 | 联系管理员或重新解析 |
| 408 `query_timeout` | 查询超时 | 缩小问题范围 |
| 429 | 调用过快 | 等待后有限重试 |
| 503 `resolver_unavailable` | 解析模型不可用 | 稍后重试，不绕过入口 |

## 安全约束

- 入口只发送用户问题、相关数据库说明和字段目录给服务端模型，不发送生产明细行。
- 固定接口是只读接口。
- 客户端脚本只允许执行 `/v1/data/` 和 `/v1/catalog/resources/` 下的解析结果。
- 包内 CA 是公开信任证书，不是服务器私钥。
- 账号密码只从环境变量读取，Token 只保存在当前进程内存。
