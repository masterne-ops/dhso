#!/usr/bin/env bash
# ────────────────────────────────────────────────
# 服务器端运维一键安装 / 更新
# 跑在服务器上（由 deploy.sh --ops 触发）
# 幂等：可反复跑
#
# 环境变量：
#   WITH_V3=1   —— 同时装 Docker + 构建 V3 沙箱镜像 so-codeagent:latest
#                  （首次开 V3 时设；之后更新代码不需要重设，镜像不变）
# ────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { printf "${CYAN}── %s ──${NC}\n" "$*"; }
ok()   { printf "${GREEN}✅ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}⚠️  %s${NC}\n" "$*"; }

APP_ROOT=/opt/so-data-analytics
WITH_V3="${WITH_V3:-0}"

# ─── 1. systemd unit ────────────────────────────
log "更新 systemd unit"
cp -f so-data-analytics.service /etc/systemd/system/so-data-analytics.service
systemctl daemon-reload
systemctl enable so-data-analytics.service >/dev/null 2>&1 || true
ok "systemd unit 就位"

# ─── 2. logrotate ───────────────────────────────
log "安装 logrotate 配置"
cp -f logrotate.conf /etc/logrotate.d/so-data-analytics
# 空跑测一下配置语法
logrotate -d /etc/logrotate.d/so-data-analytics >/dev/null 2>&1 && ok "logrotate 配置 OK"

# ─── 3. backup.sh + cron ────────────────────────
log "安装备份脚本"
cp -f backup.sh "$APP_ROOT/backup.sh"
chmod +x "$APP_ROOT/backup.sh"

CRON_LINE="0 3 * * * /opt/so-data-analytics/backup.sh"
# 写到 root 的 crontab（如果还没的话）
if ! (crontab -l 2>/dev/null | grep -Fq "$CRON_LINE"); then
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    ok "cron 已加：每天 03:00 备份"
else
    ok "cron 已存在，跳过"
fi

# ─── 4. 确保日志目录在 ───────────────────────
mkdir -p "$APP_ROOT/logs" "$APP_ROOT/backups"
mkdir -p "$APP_ROOT/v3/sandbox-output"
chmod 755 "$APP_ROOT/logs" "$APP_ROOT/backups"
# sandbox-output 要让容器里 sandbox(uid=1000) 能写
chmod 777 "$APP_ROOT/v3/sandbox-output" 2>/dev/null || true

# ─── 4b. 视图层（V2/V3 共享口径）─────────────────
# DB 已存在但视图没建（首次升级 v3.1+） → 跑一次 _views.py 建视图
if [ -f "$APP_ROOT/db/product_flow.db" ] && [ -f "$APP_ROOT/src/_views.py" ]; then
    log "重建 SQL 视图层（V2/V3 共享口径）"
    if [ -x "$APP_ROOT/venv/bin/python" ]; then
        "$APP_ROOT/venv/bin/python" "$APP_ROOT/src/_views.py" "$APP_ROOT/db/product_flow.db" \
            || warn "视图重建失败（不影响主流程，下次导入 Excel 会自动重建）"
    else
        python3 "$APP_ROOT/src/_views.py" "$APP_ROOT/db/product_flow.db" 2>/dev/null \
            || warn "视图重建失败（venv 不在 / Python 缺包）"
    fi
fi

# ─── 5. V3：装 Docker + 构建沙箱镜像 ──────────────
if [ "$WITH_V3" = "1" ]; then
    log "V3 模式：检查 Docker"
    if ! command -v docker >/dev/null 2>&1; then
        # 国内服务器 get.docker.com 经常被墙，先试官方源，失败回退到 apt
        log "装 Docker — 先试官方脚本"
        if curl -fsSL --max-time 20 https://get.docker.com -o /tmp/get-docker.sh 2>/dev/null && \
           sh /tmp/get-docker.sh 2>/dev/null; then
            ok "Docker 装好（官方脚本）"
        else
            warn "官方脚本失败（中国网络环境常见），回退到 apt-get"
            # Ubuntu/Debian: 用发行版自带的 docker.io（版本稍旧但能用）
            if command -v apt-get >/dev/null 2>&1; then
                apt-get update -qq
                DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
            elif command -v yum >/dev/null 2>&1; then
                yum install -y docker
            elif command -v dnf >/dev/null 2>&1; then
                dnf install -y docker
            else
                err "无法装 Docker — 请手动安装"
                exit 1
            fi
            ok "Docker 装好（系统包）"
        fi
        systemctl enable docker
        systemctl start docker
    else
        ok "Docker 已就绪：$(docker --version)"
    fi

    if ! systemctl is-active --quiet docker; then
        systemctl start docker
        sleep 2
    fi

    # 配镜像加速器（中国网络拉 Docker Hub 经常超时）
    DAEMON_JSON=/etc/docker/daemon.json
    if [ ! -f "$DAEMON_JSON" ] || ! grep -q "registry-mirrors" "$DAEMON_JSON" 2>/dev/null; then
        log "配置 Docker 镜像加速器（阿里云 / 中科大 / DockerProxy）"
        mkdir -p /etc/docker
        cat > "$DAEMON_JSON" <<'JSON'
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://mirror.baidubce.com"
  ]
}
JSON
        systemctl daemon-reload
        systemctl restart docker
        sleep 3
        ok "镜像加速器配置完成"
    else
        ok "Docker daemon.json 已配过加速器"
    fi

    # 构建沙箱镜像
    if [ -d "$APP_ROOT/v3" ] && [ -f "$APP_ROOT/v3/Dockerfile" ]; then
        log "构建 V3 沙箱镜像 so-codeagent:latest（首次约 5-10 分钟）"
        if docker image inspect so-codeagent:latest >/dev/null 2>&1; then
            warn "镜像 so-codeagent:latest 已存在"
            warn "如要强制重建：docker rmi so-codeagent:latest && WITH_V3=1 bash $0"
        else
            ( cd "$APP_ROOT/v3" && docker build -t so-codeagent:latest . )
            ok "沙箱镜像构建完成"
        fi
    else
        warn "$APP_ROOT/v3/Dockerfile 不存在，跳过沙箱镜像构建"
        warn "确认 deploy.sh / sync-v3.sh 已把 v3/ 同步过来"
    fi
else
    log "跳过 V3 装备（要装就 WITH_V3=1 bash install-ops.sh）"
fi

# ─── 6. 健康检查 ─────────────────────────────
log "运维状态汇总"
echo "  systemd:    $(systemctl is-active so-data-analytics.service 2>/dev/null || echo unknown)"
echo "  logrotate:  $(test -f /etc/logrotate.d/so-data-analytics && echo 'installed' || echo 'missing')"
echo "  backup.sh:  $(test -x $APP_ROOT/backup.sh && echo 'installed' || echo 'missing')"
echo "  cron:       $(crontab -l 2>/dev/null | grep -F backup.sh | head -1)"
echo "  日志:       $APP_ROOT/logs/"
echo "  备份:       $APP_ROOT/backups/"
if command -v docker >/dev/null 2>&1; then
    echo "  Docker:     $(docker --version 2>/dev/null | cut -d' ' -f1-3)"
    if docker image inspect so-codeagent:latest >/dev/null 2>&1; then
        SIZE=$(docker image inspect so-codeagent:latest --format '{{.Size}}' 2>/dev/null | awk '{printf "%.1fGB", $1/1024/1024/1024}')
        echo "  V3 镜像:    so-codeagent:latest ($SIZE)"
    else
        echo "  V3 镜像:    未构建（要 V3 就 WITH_V3=1 bash install-ops.sh）"
    fi
else
    echo "  Docker:     未安装（V3 不可用）"
fi

ok "运维脚本全部就位"
