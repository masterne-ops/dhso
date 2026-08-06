# SO生产数据只读API

## 定位

给Claude Code、Codex及其Skill提供受控的生产数据读取能力。系统分为：

1. 自然语言入口：根据《数据库说明》和固定接口目录，将需求解析成调用方法JSON。
2. 固定数据接口：按注册资源执行字段查询与聚合，不接受客户端生成的SQL。
3. 通用只读SQL：保留为管理员兼容接口，不建议运营Skill直接使用。

API不提供任何生产数据库写入接口。

## 安全边界

- 用户名和密码只通过HTTPS登录端点提交。
- 登录成功返回60分钟Bearer Token。
- 密码使用Argon2哈希，JWT密钥独立保存在服务器。
- 每个账号必须显式授权表名或glob模式；没有授权时不能查询。
- SQLite使用`mode=ro&immutable=1`、`query_only`和authorizer三层只读限制。
- 只允许`SELECT`/`WITH`；写操作、PRAGMA、ATTACH、DDL和危险函数全部拒绝。
- 永久拒绝`app_user`、权限表、AI对话和系统日志等安全表。
- 单次查询默认最多5,000行、最高20,000行，默认8秒超时。
- 审计只保存SQL哈希、访问表、行数、耗时和用户，不保存密码、Token或完整SQL。
- 每次操作新开只读连接；生产导入事务完成并checkpoint/落盘后，后续请求读取新版本，不读取尚未落盘的WAL中间状态。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz` | 健康检查 |
| POST | `/v1/auth/login` | 账号密码换Token |
| GET | `/v1/me` | 查看当前账号权限 |
| GET | `/v1/schema/tables` | 可访问表和视图 |
| GET | `/v1/schema/tables/{name}` | 字段与外键 |
| GET | `/v1/catalog` | 当前账号可调用的固定接口目录 |
| GET | `/v1/catalog/resources/{name}` | 注册资源、字段和调用方法 |
| POST | `/v1/data/{name}/search` | 固定资源明细查询 |
| POST | `/v1/data/{name}/aggregate` | 固定资源聚合查询 |
| GET | `/v1/resolver/status` | Claude Code解析器状态 |
| POST | `/v1/resolve` | 自然语言解析为固定接口调用JSON |
| POST | `/v1/query` | 参数化只读SQL，返回JSON |
| POST | `/v1/query.csv` | 参数化只读SQL，返回CSV |
| GET | `/docs` | OpenAPI交互文档 |
| GET | `/openapi.json` | Agent可读取的接口规格 |

## 调用示例

首次使用需要拿到：

- API地址：`https://121.196.152.24`
- CA证书：`so-data-api-ca.crt`
- 管理员分配的用户名和密码

```bash
BASE_URL="https://121.196.152.24"
CA="./so-data-api-ca.crt"

TOKEN=$(curl --silent --show-error --fail \
  --cacert "$CA" \
  -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"从管理员获取"}' \
  "$BASE_URL/v1/auth/login" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl --silent --show-error --fail \
  --cacert "$CA" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/v1/schema/tables"

# 推荐：让入口接口解析本次应该调用哪些固定接口
curl --silent --show-error --fail \
  --cacert "$CA" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "查询杭州各区县2026年至今签约服务商数量"
  }' \
  "$BASE_URL/v1/resolve"

# 按解析JSON调用确定的聚合接口
curl --silent --show-error --fail \
  --cacert "$CA" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "group_by": ["客户区县"],
    "metrics": [
      {
        "function": "count_distinct",
        "field": "客户编码",
        "alias": "签约服务商数"
      }
    ],
    "filters": [
      {"field": "客户城市", "operator": "eq", "value": "杭州市"}
    ],
    "order_by": [
      {"field": "签约服务商数", "direction": "desc"}
    ],
    "limit": 100
  }' \
  "$BASE_URL/v1/data/provider_contract_v/aggregate"

# 管理员兼容能力：直接提交参数化只读SQL
curl --silent --show-error --fail \
  --cacert "$CA" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "sql": "SELECT 客户城市, COUNT(*) AS 服务商数 FROM provider_contract WHERE 客户省区 = :province GROUP BY 客户城市 ORDER BY 服务商数 DESC",
    "params": {"province": "浙江省"},
    "max_rows": 100
  }' \
  "$BASE_URL/v1/query"
```

## 自然语言入口工作方式

`POST /v1/resolve`只做接口发现，不查询生产数据：

1. 服务端按用户问题检索《数据库说明》。
2. 只将相关说明和当前账号有权访问的注册资源交给Claude Code。
3. Claude Code通过`resolve-data-api` Skill生成结构化调用计划。
4. 服务端再次校验接口、资源权限和请求参数。
5. 客户端Skill读取JSON，调用确定的`search`或`aggregate`接口。

Claude Code调用时禁用Read、Bash、网络等工具，只能根据服务端提供的受控上下文输出JSON。

生产部署会从现有 AI 代码助手配置
`/root/.so_data_analytics/anthropic_config.json`安全同步模型端点、Token 和模型名到
`/etc/so-data-api/resolver.env`。同步过程不打印 Token。

如果服务器没有现有配置，才需要由管理员在`resolver.env`配置专用模型凭证：

```bash
ANTHROPIC_API_KEY=由管理员配置的专用API密钥
```

配置后执行：

```bash
systemctl restart so-data-api.service
```

不要把密钥写入Skill、Git仓库、请求体或账号数据库。

不要在Skill、Git仓库或提示词中写死密码和Token。使用环境变量：

```bash
export SO_DATA_API_URL="https://121.196.152.24"
export SO_DATA_API_USERNAME="..."
export SO_DATA_API_PASSWORD="..."
export SO_DATA_API_CA_CERT="/secure/path/so-data-api-ca.crt"
```

## 管理账号

```bash
cd /opt/so-data-api
sudo -u so-data-api -H venv/bin/python -m app.cli user-add operator \
  --full-name "运营人员" --max-rows 5000

# 授权全部业务表（安全表仍永久拒绝）
sudo -u so-data-api -H venv/bin/python -m app.cli grant operator --tables '*'

# 只授权部分业务域
sudo -u so-data-api -H venv/bin/python -m app.cli grant operator \
  --tables 'provider_*,product_flow_v,install_redpack_v,visit_record_v'

sudo -u so-data-api -H venv/bin/python -m app.cli user-list
sudo -u so-data-api -H venv/bin/python -m app.cli revoke operator
sudo -u so-data-api -H venv/bin/python -m app.cli disable operator
```

## 数据口径

使用前先阅读同项目的`docs/数据库说明.md`。重点：

- `product_flow`/`product_flow_v`：全量感知口径。
- `install_redpack`/`install_redpack_v`：红包扫码上线口径。
- `visit_record_v`：已统一修正拜访时间、打卡方和异常口径。
- `provider_contract`：服务商签约主数据。
- 优先读取`_v`视图，避免重复实现派生口径。

## V1明确不做

- 不写生产库。
- 不提供无限行全库下载。
- 不在任意SQL上自动注入地市/区县行级条件。
- 不把现有Streamlit登录会话直接复用为API Token。
- 不允许通过公网HTTP传输密码。
