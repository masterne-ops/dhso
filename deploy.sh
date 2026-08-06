#!/usr/bin/env bash
# ────────────────────────────────────────────────
# 一键部署 / 更新到生产服务器
# 用法： ./deploy.sh           （同步代码 + 重启服务）
#       ./deploy.sh --ops    （还顺带更新运维脚本：systemd / logrotate / cron）
#       ./deploy.sh --db     （还顺带同步 DB —— 慢，1.4G 压缩到 ~95M）
# ────────────────────────────────────────────────
set -euo pipefail

SERVER="${SO_DA_SERVER:-root@121.196.152.24}"
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
REMOTE_ROOT="/opt/so-data-analytics"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { printf "${CYAN}── %s ──${NC}\n" "$*"; }
ok()   { printf "${GREEN}✅ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}⚠️  %s${NC}\n" "$*"; }
err()  { printf "${RED}❌ %s${NC}\n" "$*"; }

WITH_OPS=0
WITH_DB=0
for arg in "$@"; do
    case "$arg" in
        --ops) WITH_OPS=1 ;;
        --db)  WITH_DB=1 ;;
        --help|-h) sed -n '2,10p' "$0"; exit 0 ;;
        *) err "未知参数: $arg"; exit 1 ;;
    esac
done

# SSH/rsync 防卡死 ── 一定要带，否则 ssh 半死状态会让 rsync hung 几小时
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ControlPath=none)
RSYNC_OPTS=(-avz --timeout=60 --partial -e "ssh ${SSH_OPTS[*]}")

# ─── 1. 同步代码 ─────────────────────────────────
log "同步代码 → $SERVER:$REMOTE_ROOT/src/"
rsync "${RSYNC_OPTS[@]}" --delete \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
    --exclude='venv/' --exclude='*.bak' \
    "$LOCAL_ROOT/src/" "$SERVER:$REMOTE_ROOT/src/"
ok "代码同步完成"

# ─── 2. 同步 requirements（应对依赖变化）──────
log "同步 requirements.txt"
rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/requirements.txt" "$SERVER:$REMOTE_ROOT/"

# ─── 3. 可选：装运维脚本 ────────────────────────
if [ "$WITH_OPS" -eq 1 ]; then
    log "同步 ops/ → $SERVER:$REMOTE_ROOT/ops/"
    rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/ops/" "$SERVER:$REMOTE_ROOT/ops/"
    log "服务器执行 install-ops.sh"
    ssh "${SSH_OPTS[@]}" "$SERVER" "cd $REMOTE_ROOT/ops && bash install-ops.sh"
    ok "运维脚本就位"
fi

# ─── 4. 可选：同步 DB ──────────────────────────
if [ "$WITH_DB" -eq 1 ]; then
    LOCAL_DB="$LOCAL_ROOT/db/product_flow.db"
    if [ ! -f "$LOCAL_DB" ]; then
        err "本地 DB 不存在: $LOCAL_DB"
        exit 1
    fi
    log "压缩本地 DB"
    gzip -c -6 "$LOCAL_DB" > /tmp/product_flow.db.gz
    SIZE_GZ=$(du -h /tmp/product_flow.db.gz | cut -f1)
    log "上传 ($SIZE_GZ)"
    scp "${SSH_OPTS[@]}" /tmp/product_flow.db.gz "$SERVER:$REMOTE_ROOT/db/"
    log "服务器解压 + 校验"
    LOCAL_SHA=$(shasum -a 256 "$LOCAL_DB" | awk '{print $1}')
    ssh "${SSH_OPTS[@]}" "$SERVER" "cd $REMOTE_ROOT/db && \
        gunzip -f product_flow.db.gz && \
        REMOTE_SHA=\$(sha256sum product_flow.db | awk '{print \$1}') && \
        if [ \"\$REMOTE_SHA\" = \"$LOCAL_SHA\" ]; then echo '✅ sha256 匹配'; else echo '❌ sha256 不匹配'; exit 1; fi"
    rm -f /tmp/product_flow.db.gz
    ok "DB 同步完成"
fi

# ─── 4b. 代码一致性指纹校验 ──────────────────
# 防 scp/rsync 静默失败 —— 尤其 pages/ 下中文+emoji 文件名(macOS NFD vs Linux NFC)
# 字节不匹配、没真正覆盖。校验 pages/ 所有 .py 的内容指纹(只看内容,不看文件名)。
log "代码一致性指纹校验(pages/)"
FP_LOCAL=$(find "$LOCAL_ROOT/src/pages" -name '*.py' -not -path '*__pycache__*' -exec md5 -q {} \; 2>/dev/null | sort | md5 -q)
FP_REMOTE=$(ssh "${SSH_OPTS[@]}" "$SERVER" "find $REMOTE_ROOT/src/pages -name '*.py' -not -path '*__pycache__*' -exec md5sum {} \\; 2>/dev/null | awk '{print \$1}' | sort | md5sum | awk '{print \$1}'")
if [ "$FP_LOCAL" = "$FP_REMOTE" ]; then
    ok "pages/ 指纹一致 ($FP_LOCAL) — 本地=生产"
else
    err "pages/ 指纹不一致! 本地=$FP_LOCAL 生产=$FP_REMOTE"
    err "rsync 可能漏传(NFD/NFC 文件名)。用 ASCII 临时名 + 生产端 glob 覆盖修复:"
    err "  scp 本地page → :/tmp/p.py ; ssh 'cp /tmp/p.py /opt/.../pages/NN*.py'"
    exit 1
fi

# ─── 5. 重启服务 + 健康检查 ──────────────────
log "重启 streamlit 服务"
ssh "${SSH_OPTS[@]}" "$SERVER" "systemctl restart so-data-analytics.service && sleep 4 && systemctl is-active so-data-analytics.service"

log "健康检查（公网）"
HEALTH_URL="http://121.196.152.24:8501/_stcore/health"
for i in 1 2 3 4 5; do
    HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 6 "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$HTTP" = "200" ]; then
        ok "服务健康（HTTP 200）—— 浏览器可访问 http://121.196.152.24:8501/"
        exit 0
    fi
    warn "第 $i 次：HTTP=$HTTP，3 秒后重试"
    sleep 3
done

err "5 次健康检查全失败。日志："
ssh "${SSH_OPTS[@]}" "$SERVER" "journalctl -u so-data-analytics -n 50 --no-pager"
exit 1
