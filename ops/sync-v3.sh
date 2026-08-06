#!/usr/bin/env bash
# ────────────────────────────────────────────────
# V3 沙箱独立同步 + 构建脚本
#
# deploy.sh 不同步 v3/ 目录，要部署 V3 时用这个：
#   export SO_DA_SERVER=root@your-ip          # 必填：目标服务器
#   export SO_DA_REMOTE=/opt/so-data-analytics  # 可选：远程路径，有默认
#   bash ops/sync-v3.sh
#
# 这个脚本会：
#   1. rsync v3/ 到服务器（不含 sandbox-output 运行时数据）
#   2. 服务器上跑 install-ops.sh + WITH_V3=1（装 Docker + build 镜像）
#   3. 重启 streamlit 服务
# ────────────────────────────────────────────────
set -euo pipefail

# ─── 入参检查 ──────────────────────────────────
if [ -z "${SO_DA_SERVER:-}" ]; then
    cat <<'EOF'
❌ 请先设置 SO_DA_SERVER，例如：

    export SO_DA_SERVER=root@1.2.3.4
    bash ops/sync-v3.sh

参数：
  SO_DA_SERVER   必填：ssh 目标，如 root@1.2.3.4
  SO_DA_REMOTE   可选：远程路径（默认 /opt/so-data-analytics）

注意：这个脚本会在服务器上跑 docker build（约 5 分钟）+ 重启 streamlit。
EOF
    exit 1
fi

SERVER="$SO_DA_SERVER"
REMOTE_ROOT="${SO_DA_REMOTE:-/opt/so-data-analytics}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { printf "${CYAN}── %s ──${NC}\n" "$*"; }
ok()   { printf "${GREEN}✅ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}⚠️  %s${NC}\n" "$*"; }

echo "════════════════════════════════════════════"
echo "  V3 沙箱同步 → $SERVER:$REMOTE_ROOT"
echo "════════════════════════════════════════════"

SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ControlPath=none)
RSYNC_OPTS=(-avz --timeout=60 --partial -e "ssh ${SSH_OPTS[*]}")

# ─── 1. 同步 v3/ ──────────────────────────────
log "rsync v3/ → $SERVER:$REMOTE_ROOT/v3/"
rsync "${RSYNC_OPTS[@]}" \
    --exclude='sandbox-output/*' \
    --exclude='_claude_state/' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    "$LOCAL_ROOT/v3/" "$SERVER:$REMOTE_ROOT/v3/"
ok "v3/ 同步完成"

# ─── 2. 同步 ops/（保证 install-ops.sh 是最新的）─
log "rsync ops/ → $SERVER:$REMOTE_ROOT/ops/"
rsync "${RSYNC_OPTS[@]}" \
    --exclude='__pycache__' \
    "$LOCAL_ROOT/ops/" "$SERVER:$REMOTE_ROOT/ops/"
ok "ops/ 同步完成"

# ─── 3. 服务器上跑 install-ops.sh WITH_V3=1 ────
log "服务器执行 WITH_V3=1 bash install-ops.sh（含装 Docker + 构建镜像）"
log "  首次约 5 分钟，之后镜像缓存命中会很快"
ssh "${SSH_OPTS[@]}" "$SERVER" "cd $REMOTE_ROOT/ops && WITH_V3=1 bash install-ops.sh"

# ─── 4. 确保 sandbox-output 目录权限（容器里 uid=1000 要能写）─
log "确保 sandbox-output 目录权限"
ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p $REMOTE_ROOT/v3/sandbox-output && chmod 777 $REMOTE_ROOT/v3/sandbox-output"

# ─── 5. 重启 streamlit ─────────────────────────
log "重启 streamlit 服务（让 page 09 重新检测 docker 状态）"
ssh "${SSH_OPTS[@]}" "$SERVER" "systemctl restart so-data-analytics.service && sleep 4 && systemctl is-active so-data-analytics.service"
ok "服务重启完成"

# ─── 6. 验证 ───────────────────────────────────
log "服务器侧 V3 状态自检"
ssh "${SSH_OPTS[@]}" "$SERVER" "
    echo '  Docker daemon: '\$(systemctl is-active docker 2>/dev/null);
    echo '  V3 镜像: '\$(docker image inspect so-codeagent:latest --format '{{.RepoTags}} {{.Created}}' 2>/dev/null || echo 'NOT FOUND');
    echo '  sandbox-output: '\$(ls -ld $REMOTE_ROOT/v3/sandbox-output | awk '{print \$1, \$3, \$4}');
    echo '  streamlit: '\$(systemctl is-active so-data-analytics.service);
"

echo
echo "════════════════════════════════════════════"
echo "  ✅ V3 部署完成"
echo "════════════════════════════════════════════"
echo
echo "下一步："
echo "  1. 浏览器进入主页 page 09 「🛠️ AI 代码助手」"
echo "  2. 左侧栏配 Anthropic 端点（Base URL / Auth Token / Model）"
echo "  3. 试问一个问题，看沙箱能不能跑通"
echo
