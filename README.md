# SO 转化漏斗（funnel）

本仓库**只保留**转化漏斗服务代码。看板、报表、生产库导入等已从本工程移除，避免交叉改动。

| 项 | 说明 |
|----|------|
| 代码目录 | `funnel/` |
| 生产路径 | `/opt/so-funnel` |
| 端口 | **8443** |
| 自有库 | `funnel/data/funnel.db`（切片 / 目标覆盖 / 因子缓存等，可写） |
| 共享生产库 | `product_flow.db`（经 `SO_PROD_DB` **只读**挂载，禁止写） |

## 本地启动

```bash
cd funnel
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt
# 可选：指向本机只读副本或生产库路径
# export SO_PROD_DB=/path/to/product_flow.db
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8443
```

浏览器打开：`http://127.0.0.1:8443/`（`funnel_v2.html`）或总览页 `overview.html`。

更细的部署、鉴权、验收见 [`funnel/api/README.md`](funnel/api/README.md)。

## 边界（必须遵守）

1. **只改 `funnel/`**，不要再往仓库加看板 / 报表 / migrations。
2. 对共享生产库表：**只读**；需要持久化的 funnel 状态只写 `funnel.db`。
3. 部署用 `funnel/deploy_funnel.sh`，不要用已删除的看板 `deploy.sh`。
