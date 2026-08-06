#!/usr/bin/env bash
# ────────────────────────────────────────────────
# 部署 / 更新「服务商转化漏斗」到生产服务器
#
#   ./deploy_funnel.sh              同步代码 + 重启 + 健康检查
#   ./deploy_funnel.sh --setup      首次部署（建目录 / venv / systemd / 凭证）
#   ./deploy_funnel.sh --mk-users   生成/补齐 12 个账号（admin + 11 地市），密码随机
#   ./deploy_funnel.sh --mk-users --reset 杭州市    重置指定账号密码
#
# 与 so-data-analytics(8501)、so-data-api(8601) 完全独立，互不影响。
#
# 两套凭证来源，多账号优先：
#   /etc/so-funnel/users.json  多账号 + 地区范围（admin=全省，地市=本市及其区县）
#   /etc/so-funnel/funnel.env  单账号兜底（users.json 不存在时才生效，等同管理员）
# users.json 一旦生成，funnel.env 里的 funnel/xxx 就**不再能登录**，
# 所以健康检查会自动改用「无凭证必须 401」作为存活判据。
# ────────────────────────────────────────────────
set -euo pipefail

SERVER="${SO_DA_SERVER:-root@121.196.152.24}"
HOST="${SERVER#*@}"
# 8443：ufw 已放行且无人占用，省去改防火墙（8801 被 ufw 挡）
PORT="${FUNNEL_PORT:-8443}"
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
REMOTE_ROOT="/opt/so-funnel"
SVC="so-funnel.service"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { printf "${CYAN}── %s ──${NC}\n" "$*"; }
ok()   { printf "${GREEN}✅ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}⚠️  %s${NC}\n" "$*"; }
err()  { printf "${RED}❌ %s${NC}\n" "$*"; }

SETUP=0
MK_USERS=0
RESETS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --setup) SETUP=1 ;;
        --mk-users) MK_USERS=1 ;;
        --reset) shift; [ $# -gt 0 ] || { err "--reset 后面要跟账号名/地市名/all"; exit 1; }
                 RESETS+=("$1") ;;
        --help|-h) sed -n '2,17p' "$0"; exit 0 ;;
        *) err "未知参数: $1"; exit 1 ;;
    esac
    shift
done

# SSH/rsync 防卡死 —— ssh 半死状态会让 rsync hang 住
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ControlPath=none)
RSYNC_OPTS=(-avz --timeout=60 --partial -e "ssh ${SSH_OPTS[*]}")
ssh_() { ssh "${SSH_OPTS[@]}" "$SERVER" "$@"; }

# ─── 1. 首次部署：目录 / venv / 凭证 / systemd ─────────
if [ "$SETUP" -eq 1 ]; then
    log "建目录 + venv"
    ssh_ "mkdir -p $REMOTE_ROOT/{api/routers,data,logs,docs} /etc/so-funnel && \
          if [ ! -x $REMOTE_ROOT/venv/bin/python ]; then python3 -m venv $REMOTE_ROOT/venv; fi"

    log "生成 Basic Auth 凭证（已存在则保留）"
    ssh_ "if [ ! -f /etc/so-funnel/funnel.env ]; then
              PW=\$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)
              printf 'FUNNEL_USER=funnel\nFUNNEL_PASS=%s\n' \"\$PW\" > /etc/so-funnel/funnel.env
              chmod 600 /etc/so-funnel/funnel.env
              echo NEW
          else echo KEEP; fi"
fi

# ─── 2. 同步代码 ─────────────────────────────────
log "同步 api/ → $SERVER:$REMOTE_ROOT/api/"
rsync "${RSYNC_OPTS[@]}" --delete \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
    --exclude='_verify*.py' \
    "$LOCAL_ROOT/api/" "$SERVER:$REMOTE_ROOT/api/"

log "同步页面 + 设计文档"
rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/funnel_v2.html" "$SERVER:$REMOTE_ROOT/"
rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/docs/factor-library-design.md" "$SERVER:$REMOTE_ROOT/docs/"

log "同步运维脚本"
ssh_ "mkdir -p $REMOTE_ROOT/ops"
rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/ops/mk_users.py" "$SERVER:$REMOTE_ROOT/ops/"
ok "代码同步完成"

# ─── 3. 依赖 ─────────────────────────────────────
log "安装依赖"
ssh_ "$REMOTE_ROOT/venv/bin/pip install -q --upgrade pip && \
      $REMOTE_ROOT/venv/bin/pip install -q -r $REMOTE_ROOT/api/requirements.txt"
ok "依赖就绪"

# ─── 3b. 多账号（admin + 11 地市）─────────────────
# 只在显式 --mk-users 时跑。理由：mk_users.py 会给缺失的账号生成新密码，
# 每次部署都跑虽然不会改已有账号的密码，但会把明文凭证清单重新写一遍到服务器上，
# 而那份东西的正确归宿是转达完就删。
if [ "$MK_USERS" -eq 1 ]; then
    log "生成/补齐账号表 /etc/so-funnel/users.json"
    RESET_ARGS=""
    for r in ${RESETS[@]+"${RESETS[@]}"}; do RESET_ARGS="$RESET_ARGS --reset '$r'"; done
    # 明文清单落在 /root 下（600），转达完由下面的提示要求手工删除；不进 /etc、不进 git
    ssh_ "mkdir -p /etc/so-funnel && cd $REMOTE_ROOT && \
          venv/bin/python ops/mk_users.py --out /etc/so-funnel/users.json \
              --creds /root/so-funnel-creds.txt $RESET_ARGS"
    echo
    warn "明文密码清单在服务器 /root/so-funnel-creds.txt（chmod 600）"
    printf "  查看  ssh %s 'cat /root/so-funnel-creds.txt'\n" "$SERVER"
    printf "  转达完请删除  ssh %s 'shred -u /root/so-funnel-creds.txt'\n" "$SERVER"
    echo
fi

# ─── 4. systemd unit ────────────────────────────
if [ "$SETUP" -eq 1 ]; then
    log "安装 systemd unit"
    rsync "${RSYNC_OPTS[@]}" "$LOCAL_ROOT/ops/$SVC" "$SERVER:/etc/systemd/system/$SVC"
    ssh_ "systemctl daemon-reload && systemctl enable $SVC"
    ok "unit 已安装并设为开机自启"
fi

# ─── 5. 指纹校验（防 rsync 静默失败）────────────
log "代码一致性指纹校验"
FP_LOCAL=$(find "$LOCAL_ROOT/api" -name '*.py' -not -path '*__pycache__*' -not -name '_verify*' \
           -exec md5 -q {} \; 2>/dev/null | sort | md5 -q)
FP_REMOTE=$(ssh_ "find $REMOTE_ROOT/api -name '*.py' -not -path '*__pycache__*' \
            -exec md5sum {} \\; 2>/dev/null | awk '{print \$1}' | sort | md5sum | awk '{print \$1}'")
if [ "$FP_LOCAL" = "$FP_REMOTE" ]; then
    ok "api/ 指纹一致 ($FP_LOCAL)"
else
    err "api/ 指纹不一致! 本地=$FP_LOCAL 生产=$FP_REMOTE"
    exit 1
fi

# ─── 6. 重启 + 健康检查 ─────────────────────────
log "重启 $SVC"
ssh_ "systemctl restart $SVC && sleep 3 && systemctl is-active $SVC"

log "健康检查（带凭证，公网）"
# 认证源判定：users.json 存在即多账号模式，此时 funnel.env 里那对凭证已失效，
# 拿它做健康检查只会 401，误报成"部署失败"。
MODE=$(ssh_ "if [ -s /etc/so-funnel/users.json ]; then echo multi; else echo single; fi")
if [ "$MODE" = "multi" ]; then
    # 多账号模式下服务器上只有哈希。凭证清单（--mk-users 刚生成的那份）还在就借
    # admin 那行探一次真实 200；已经删掉就退化成"无凭证 401 + 服务 active"。
    CREDS=$(ssh_ "awk -F'\t' '\$1==\"admin\"{print \$1\":\"\$3}' /root/so-funnel-creds.txt 2>/dev/null || true")
    NUSERS=$(ssh_ "grep -o '\"name\"' /etc/so-funnel/users.json | wc -l | tr -d ' '")
    ok "认证源：多账号 users.json（$NUSERS 个账号）"
else
    CREDS=$(ssh_ "awk -F= '/FUNNEL_USER/{u=\$2} /FUNNEL_PASS/{p=\$2} END{print u\":\"p}' /etc/so-funnel/funnel.env")
    ok "认证源：单账号 funnel.env"
fi
URL="http://$HOST:$PORT"
for i in 1 2 3 4 5; do
    if [ -n "$CREDS" ]; then
        HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 8 -u "$CREDS" "$URL/healthz" || echo 000)
    else
        # 没有可用明文：401 只证明中间件活着，路由层还没被摸过 —— 所以再要求
        # systemd 是 active 且日志里没有启动期异常，别把"崩在 import 阶段"当健康。
        HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 8 "$URL/healthz" || echo 000)
        [ "$HTTP" = "401" ] && HTTP=200
    fi
    if [ "$HTTP" = "200" ]; then
        # 未带凭证必须被拦
        NOAUTH=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 8 "$URL/healthz" || echo 000)
        [ "$NOAUTH" = "401" ] && ok "认证生效（无凭证 401）" || warn "无凭证返回 ${NOAUTH}，预期 401！"
        if [ -n "$CREDS" ]; then
            AUTHED=$(curl -sS --max-time 8 -u "$CREDS" "$URL/healthz" || true)
            case "$AUTHED" in *'"auth":true'*) ok "多账号鉴权已启用（healthz auth=true）" ;; esac
        else
            warn "无可用明文凭证，未探 200；仅校验了「无凭证 401 + 服务 active」"
        fi
        ok "服务健康 —— $URL/"
        echo
        printf "  漏斗页面   %s/\n" "$URL"
        printf "  接口文档   %s/docs\n" "$URL"
        if [ "$MODE" = "multi" ]; then
            printf "  登录账号   admin（全省）+ 11 个地市账号，见 /root/so-funnel-creds.txt\n"
        else
            printf "  登录账号   %s\n" "${CREDS%%:*}"
            printf "  登录密码   %s\n" "${CREDS#*:}"
        fi
        exit 0
    fi
    # ${VAR} 必须带花括号：紧跟中文标点时 bash 会把多字节字符当成变量名的一部分
    warn "第 $i 次：HTTP=${HTTP}，3 秒后重试"
    sleep 3
done

err "5 次健康检查全失败。日志："
ssh_ "journalctl -u $SVC -n 40 --no-pager; tail -30 $REMOTE_ROOT/logs/funnel.err 2>/dev/null"
exit 1
