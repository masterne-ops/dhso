# V3 — Code Agent 沙箱架构

## 这是什么

V2.x 是「硬编码报表 + Function Calling AI 助手」（13 个 page + 13 个工具）。
V3 是「报表保留 + Code Agent 沙箱」—— 让 Claude Code CLI 在 Docker 沙箱里直接写 Python/SQL/画图，解任何用户问的问题。

## 为什么要 V3

V2 的 Function Calling 局限：
- 工具是预定义的 13 个，遇到新问题（"帮我画一张杭州市 V4 服务商按签约月份的留存曲线"）就抓瞎
- 每加一个分析维度就要写新工具
- 没法生成图表/CSV 给用户下载

V3 思路：
- Claude Code CLI 自带 Bash + Python + Read/Write/Edit
- 沙箱里有 pandas/matplotlib/sqlite3
- AI 自己写代码、查 DB、出图、写报告
- 加新能力 = 给沙箱装新库（不改 Python 代码）

## 目录

```
v3/
├── README.md          ← 你在这
├── Dockerfile         ← 沙箱镜像定义
├── .dockerignore
├── build.sh           ← 构建镜像：docker build -t so-codeagent:latest .
├── agent-claude.md    ← 沙箱内置的 CLAUDE.md（系统 prompt + 数据 schema）
└── sandbox-output/    ← 每次会话的输出目录（gitignore）
    └── {ui_session_id}/
        ├── _claude_state/    ← Claude Code CLI 的 session 持久化（多轮对话用）
        ├── *.png / *.csv     ← AI 生成的产物（用户可下载）
        └── ...
```

## 跑通流程

### 1. 装 Docker

- Mac：https://www.docker.com/products/docker-desktop/  
- Linux：`curl -fsSL https://get.docker.com | sh`
- Windows：Docker Desktop（同 Mac）

### 2. 构建沙箱镜像（首次）

```bash
cd scenarios/so数据分析/v3
./build.sh
```

约 5 分钟，包含 Python 3.11 + Node 20 + Claude Code CLI + pandas/matplotlib + 中文字体。

### 3. 配 Anthropic 兼容端点

启动 streamlit，进 `09·🛠️ AI 代码助手` 页面，左侧栏填：
- `Base URL` —— 国产 LLM 的 Anthropic 兼容端点
- `Auth Token` —— 对应 token
- `Model` —— 模型名（如 `claude-sonnet-4` 或厂商映射的实际模型名）

保存后存到 `~/.so_data_analytics/anthropic_config.json`（chmod 600）。

### 4. 提问

输入框里直接问，沙箱启动 Claude Code 解题，输出实时流到页面。生成的图/CSV 会列在「📂 文件」区。

### 5. 多轮对话（V3 beta 新增）

第一轮提问后，Claude 的 session_id 自动捕获。在同一个 streamlit 会话里继续问，会自动 `--resume`，AI 看得到上一轮的代码 / 结果 / 输出。

要重新开始：左栏「🆕 新建会话」。

## 安全边界

| 维度 | 限制 |
|------|------|
| DB | 只读挂载 `db/product_flow.db` → `/sandbox/db.sqlite:ro` |
| 输出 | 读写挂载 `v3/sandbox-output/{ui_session_id}/` → `/sandbox/output/` |
| Claude state | 读写挂载 `v3/sandbox-output/{ui_session_id}/_claude_state/` → `/home/sandbox/.claude/`（跨轮持久化） |
| 内存 | `--memory=2g`（可调） |
| CPU | `--cpus=2`（可调） |
| 网络 | `--network=bridge`（默认，能访问 LLM 端点；后续可白名单收紧） |
| 用户 | 容器内 `sandbox` 非 root（uid 1000） |
| 生命周期 | `--rm` 用完即销，没持久化 root 状态 |
| 超时 | 默认 10 分钟，可调 |

宿主机的 Claude Code CLI 跟容器里的 CLI **完全隔离**：容器里 `~/.claude/`、`ANTHROPIC_*` 都是新建的，互不干扰。

## V3 进度

- [x] **alpha** —— 沙箱镜像 + Streamlit 页面 + 流式输出
- [x] **beta** —— 多轮对话上下文（claude --resume + 持久化 _claude_state）
- [x] **beta** —— 自动检测/列出生成的文件（含图/CSV/markdown 预览）
- [x] **beta** —— 修 stderr pipe 死锁（threading 异步收）
- [x] **rc** —— 工具调用日志持久化到 `code_agent_log` 表
- [x] **rc** —— 用户反馈（👍/👎），未来用于 prompt 调优
- [ ] beta+ —— 网络白名单（仅放行 LLM 端点 IP）
- [ ] rc+ —— 沙箱内预装 Skill（poster-generator 等）

## 服务器部署

主代码用 `deploy.sh` 同步；V3 沙箱用 `ops/sync-v3.sh` 单独部署：

```bash
# 在本地仓库根目录：
export SO_DA_SERVER=root@<your-ip>
bash ops/sync-v3.sh
```

会做的：
1. rsync `v3/` 到服务器 `/opt/so-data-analytics/v3/`
2. 服务器跑 `WITH_V3=1 ops/install-ops.sh`：
   - 装 Docker（如未装），enable + start daemon
   - `cd /opt/so-data-analytics/v3 && docker build -t so-codeagent:latest .`
3. 重启 `so-data-analytics.service`

之后服务器上 streamlit 进程就能调 docker，page 09 「🛠️ AI 代码助手」可用。

## 关键日志

```sql
-- 全量记录（可在 streamlit 里读）
SELECT * FROM code_agent_log ORDER BY timestamp DESC LIMIT 50;

-- 失败的轮次
SELECT * FROM code_agent_log WHERE returncode != 0;

-- 用户给 👎 的轮次（用来改 prompt）
SELECT user_question, answer_text FROM code_agent_log WHERE feedback = -1;

-- 多轮对话归到一起
SELECT * FROM code_agent_log WHERE ui_session_id = 'xxx' ORDER BY timestamp;
```
