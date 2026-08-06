# SO 浙江省区数据平台（三件套）

本仓库对齐生产服务器 `121.196.152.24` 上的三套服务代码，不含数据库与密钥。

| 服务 | 生产端口 | 仓库目录 | 生产路径 |
|------|----------|----------|----------|
| Streamlit SO 看板 | **8501** | `src/` | `/opt/so-data-analytics` |
| 转化漏斗 FastAPI | **8443** | `funnel/` | `/opt/so-funnel` |
| 生产数据只读 API | **443** → 8601 | `api/` | `/opt/so-data-api` |

共享数据底座：SQLite `product_flow.db`（本地/生产均不进 Git，体积约 2.5GB）。

---

## 目录结构

```
src/                 # 8501 Streamlit 应用（pages + 业务模块）
api/                 # 443 只读 API（FastAPI + skills + knowledge）
funnel/              # 8443 服务商转化漏斗（FastAPI + funnel_v2.html）
docs/                # 业务/数据库/代码说明
migrations/          # Excel → SQLite 导入脚本
ops/                 # 看板 systemd / 备份 / logrotate
v3/                  # Code Agent skills（无运行时输出）
report_tools/        # 报告渲染工具
si_order_tool/       # SI 配单相关
月度SO作战方案/       # 作战方案数据与说明
requirements.txt     # 看板依赖
deploy.sh            # 看板部署到 8501
funnel/deploy_funnel.sh
api/deploy.sh        # API 部署
```

**刻意不入库：** `venv/`、`db/*.db`、日志、密钥（`*.env` / `auth.json` / `users.json`）、上传 Excel、沙箱运行产物。

---

## 1. SO 看板（8501）

```bash
./install.sh && ./start.sh          # http://localhost:8501
./deploy.sh                         # rsync src/ → 生产并重启
```

- 数据导入：应用内「智能批量导入」或 `migrations/`
- 口径说明：`docs/数据库说明.md`、`docs/代码说明.md`
- 双口径：全量感知 `product_flow_v` / 红包扫码 `install_redpack_v`

---

## 2. 转化漏斗（8443）

```bash
cd funnel
python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt
# 配置 /etc/so-funnel/funnel.env（或本地等价环境变量）后：
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8443
```

- 前端单页：`funnel/funnel_v2.html`
- 单元/线上验收：`funnel/api/_verify_*.py`
- 部署：`funnel/deploy_funnel.sh`，systemd 见 `funnel/ops/so-funnel.service`

---

## 3. 只读数据 API（443）

见 `api/README.md`。

```bash
cd api
# 部署：./deploy.sh
# 自然语言 → 固定接口：POST /v1/resolve
# 固定查询：POST /v1/data/{resource}/search|aggregate
```

无任何写库接口；账号与 JWT 密钥保存在服务器 `/etc/so-data-api/`，不进本仓库。

---

## 数据口径备忘

| 业务概念 | 字段 |
|---|---|
| SO 一台 | 1 行 = 1 台设备上线 |
| SO 金额 | `最新分销价` / 视图 `KPI金额` |
| 月份切片 | `上线时间` → 年-月 |
| 代理商 | `出库客户名称` / `所属一级客户` |
| 服务商 | `上线客户编码`（红包）/ `上线自客户编码`（流向） |
| 授牌服务商 | `管理标签='授牌服务商'` → 无价值，勿进重点任务 |

---

## 与生产对齐方式

```bash
# 看板代码
rsync -avz --delete --exclude='__pycache__' \
  root@SERVER:/opt/so-data-analytics/src/ ./src/

# 漏斗
rsync -avz --delete --exclude='venv' --exclude='data' --exclude='logs' \
  root@SERVER:/opt/so-funnel/ ./funnel/

# API
rsync -avz --delete --exclude='__pycache__' \
  root@SERVER:/opt/so-data-api/app/ ./api/app/
```

本仓库首次提交已按上述路径从生产拉取对齐（漏斗另含本地 `_verify_*.py` 验收脚本与较完整 README）。
