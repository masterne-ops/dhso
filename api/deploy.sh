#!/usr/bin/env bash
set -euo pipefail

SERVER="${SO_DATA_API_SERVER:-root@121.196.152.24}"
REMOTE_ROOT="${SO_DATA_API_REMOTE_ROOT:-/opt/so-data-api}"
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATABASE_DOC="$LOCAL_ROOT/../docs/数据库说明.md"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

log() { printf '\n── %s ──\n' "$*"; }

log "Create remote directories and service account"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  if ! id so-data-api >/dev/null 2>&1; then
    useradd --system --home /var/lib/so-data-api --create-home --shell /usr/sbin/nologin so-data-api
  fi
  install -d -o root -g root -m 0755 \
    $REMOTE_ROOT $REMOTE_ROOT/app $REMOTE_ROOT/ops $REMOTE_ROOT/scripts \
    $REMOTE_ROOT/knowledge $REMOTE_ROOT/.claude $REMOTE_ROOT/.claude/skills
  install -d -o so-data-api -g so-data-api -m 0750 /var/lib/so-data-api /var/log/so-data-api
  install -d -o so-data-api -g so-data-api -m 0750 \
    /var/lib/so-data-api/claude-home \
    /var/lib/so-data-api/claude-home/.claude \
    /var/lib/so-data-api/claude-home/.claude/skills
  install -d -o root -g so-data-api -m 0750 /etc/so-data-api
  install -d -o root -g root -m 0750 /etc/so-data-api/tls
"

log "Sync API source"
rsync -az --delete -e "$RSYNC_SSH" \
  --exclude='__pycache__' --exclude='*.pyc' \
  "$LOCAL_ROOT/app/" "$SERVER:$REMOTE_ROOT/app/"
rsync -az -e "$RSYNC_SSH" \
  "$LOCAL_ROOT/requirements.txt" "$LOCAL_ROOT/README.md" \
  "$SERVER:$REMOTE_ROOT/"
rsync -az --delete -e "$RSYNC_SSH" \
  "$LOCAL_ROOT/ops/" "$SERVER:$REMOTE_ROOT/ops/"
rsync -az --delete -e "$RSYNC_SSH" \
  "$LOCAL_ROOT/scripts/" "$SERVER:$REMOTE_ROOT/scripts/"
rsync -az --delete -e "$RSYNC_SSH" \
  "$LOCAL_ROOT/skills/" "$SERVER:$REMOTE_ROOT/.claude/skills/"
rsync -az -e "$RSYNC_SSH" \
  "$DATABASE_DOC" "$SERVER:$REMOTE_ROOT/knowledge/数据库说明.md"

log "Install isolated Python environment"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  if [ ! -x $REMOTE_ROOT/venv/bin/python ]; then
    python3 -m venv $REMOTE_ROOT/venv
  fi
  $REMOTE_ROOT/venv/bin/pip install --disable-pip-version-check -q -r $REMOTE_ROOT/requirements.txt
  chown -R root:root \
    $REMOTE_ROOT/app $REMOTE_ROOT/ops $REMOTE_ROOT/scripts \
    $REMOTE_ROOT/knowledge $REMOTE_ROOT/.claude \
    $REMOTE_ROOT/requirements.txt $REMOTE_ROOT/README.md
  chmod -R a+rX \
    $REMOTE_ROOT/app $REMOTE_ROOT/scripts $REMOTE_ROOT/knowledge $REMOTE_ROOT/.claude
  rsync -a --delete \
    $REMOTE_ROOT/.claude/skills/ \
    /var/lib/so-data-api/claude-home/.claude/skills/
  chown -R so-data-api:so-data-api /var/lib/so-data-api/claude-home
  chmod 0750 \
    /var/lib/so-data-api/claude-home \
    /var/lib/so-data-api/claude-home/.claude \
    /var/lib/so-data-api/claude-home/.claude/skills
"

log "Write environment and initialize auth store"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  if [ ! -f /etc/so-data-api/api.env ]; then
    cat > /etc/so-data-api/api.env <<'EOF'
SO_DATA_API_DB_PATH=/opt/so-data-analytics/db/product_flow.db
SO_DATA_API_AUTH_DB=/var/lib/so-data-api/auth.db
SO_DATA_API_JWT_SECRET_FILE=/etc/so-data-api/jwt-secret
SO_DATA_API_DATABASE_DOC=/opt/so-data-api/knowledge/数据库说明.md
SO_DATA_API_CATALOG_FILE=/opt/so-data-api/knowledge/api-catalog.json
SO_DATA_API_CLAUDE_BINARY=/usr/bin/claude
SO_DATA_API_CLAUDE_WORKDIR=/opt/so-data-api
SO_DATA_API_TOKEN_MINUTES=60
SO_DATA_API_QUERY_TIMEOUT_MS=8000
SO_DATA_API_DEFAULT_MAX_ROWS=5000
SO_DATA_API_HARD_MAX_ROWS=20000
SO_DATA_API_RESOLVER_TIMEOUT_SECONDS=110
SO_DATA_API_RESOLVER_QUEUE_SECONDS=30
SO_DATA_API_RESOLVER_CONCURRENCY=2
SO_DATA_API_RESOLVER_MAX_RESOURCES=10
SO_DATA_API_RESOLVER_DOC_CHARS=8000
SO_DATA_API_RESOLVER_MAX_BUDGET_USD=0.80
EOF
  fi
  for setting in \
    'SO_DATA_API_RESOLVER_TIMEOUT_SECONDS=110' \
    'SO_DATA_API_RESOLVER_QUEUE_SECONDS=30' \
    'SO_DATA_API_RESOLVER_CONCURRENCY=2' \
    'SO_DATA_API_RESOLVER_MAX_RESOURCES=10' \
    'SO_DATA_API_RESOLVER_DOC_CHARS=8000'
  do
    key=\${setting%%=*}
    if grep -q \"^\${key}=\" /etc/so-data-api/api.env; then
      sed -i \"s/^\${key}=.*/\${setting}/\" /etc/so-data-api/api.env
    else
      printf '%s\n' \"\${setting}\" >> /etc/so-data-api/api.env
    fi
  done
  if grep -q '^SO_DATA_API_RESOLVER_MAX_BUDGET_USD=' /etc/so-data-api/api.env; then
    sed -i 's/^SO_DATA_API_RESOLVER_MAX_BUDGET_USD=.*/SO_DATA_API_RESOLVER_MAX_BUDGET_USD=0.80/' \
      /etc/so-data-api/api.env
  else
    printf '%s\n' 'SO_DATA_API_RESOLVER_MAX_BUDGET_USD=0.80' \
      >> /etc/so-data-api/api.env
  fi
  if [ -f /root/.so_data_analytics/anthropic_config.json ]; then
    PYTHONPATH=$REMOTE_ROOT $REMOTE_ROOT/venv/bin/python \
      $REMOTE_ROOT/scripts/sync_resolver_config.py \
      --source /root/.so_data_analytics/anthropic_config.json \
      --output /etc/so-data-api/resolver.env
  elif [ ! -f /etc/so-data-api/resolver.env ]; then
    cat > /etc/so-data-api/resolver.env <<'EOF'
# Add a dedicated Anthropic key without quotes:
# ANTHROPIC_API_KEY=sk-ant-...
EOF
  fi
  chown root:so-data-api /etc/so-data-api/api.env
  chmod 0640 /etc/so-data-api/api.env
  chown root:so-data-api /etc/so-data-api/resolver.env
  chmod 0640 /etc/so-data-api/resolver.env
  if [ ! -f /etc/so-data-api/jwt-secret ]; then
    umask 077
    openssl rand -hex 64 > /etc/so-data-api/jwt-secret
  fi
  chown so-data-api:so-data-api /etc/so-data-api/jwt-secret
  chmod 0600 /etc/so-data-api/jwt-secret
  set -a
  . /etc/so-data-api/api.env
  set +a
  cd $REMOTE_ROOT
  runuser -u so-data-api -- $REMOTE_ROOT/venv/bin/python -m app.cli init
  PYTHONPATH=$REMOTE_ROOT $REMOTE_ROOT/venv/bin/python \
    $REMOTE_ROOT/scripts/generate_catalog.py \
    --database /opt/so-data-analytics/db/product_flow.db \
    --database-doc $REMOTE_ROOT/knowledge/数据库说明.md \
    --output $REMOTE_ROOT/knowledge/api-catalog.json
  chown root:root $REMOTE_ROOT/knowledge/api-catalog.json
  chmod 0644 $REMOTE_ROOT/knowledge/api-catalog.json
"

log "Create private CA and IP certificate when missing"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  TLS=/etc/so-data-api/tls
  if [ ! -f \$TLS/ca.crt ] || [ ! -f \$TLS/server.crt ] || [ ! -f \$TLS/server.key ]; then
    umask 077
    openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
      -keyout \$TLS/ca.key -out \$TLS/ca.crt \
      -subj '/CN=SO Data API Private CA/O=AI Consulting'
    openssl req -newkey rsa:3072 -nodes \
      -keyout \$TLS/server.key -out \$TLS/server.csr \
      -subj '/CN=121.196.152.24/O=AI Consulting'
    printf 'subjectAltName=IP:121.196.152.24\nextendedKeyUsage=serverAuth\n' > \$TLS/server.ext
    openssl x509 -req -in \$TLS/server.csr \
      -CA \$TLS/ca.crt -CAkey \$TLS/ca.key -CAcreateserial \
      -out \$TLS/server.crt -days 825 -sha256 -extfile \$TLS/server.ext
    rm -f \$TLS/server.csr \$TLS/server.ext
  fi
  chown root:root \$TLS/*
  chmod 0600 \$TLS/ca.key \$TLS/server.key
  chmod 0644 \$TLS/ca.crt \$TLS/server.crt
"

log "Install service, TLS proxy, firewall rule, and log rotation"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  install -o root -g root -m 0644 $REMOTE_ROOT/ops/so-data-api.service /etc/systemd/system/so-data-api.service
  install -o root -g root -m 0644 $REMOTE_ROOT/ops/nginx-so-data-api.conf /etc/nginx/sites-available/so-data-api.conf
  ln -sfn /etc/nginx/sites-available/so-data-api.conf /etc/nginx/sites-enabled/so-data-api.conf
  install -o root -g root -m 0644 $REMOTE_ROOT/ops/logrotate /etc/logrotate.d/so-data-api
  nginx -t
  systemctl daemon-reload
  systemctl enable so-data-api.service >/dev/null
  systemctl restart so-data-api.service
  systemctl reload nginx
  ufw allow 443/tcp >/dev/null
"

log "Health check through TLS"
ssh "${SSH_OPTS[@]}" "$SERVER" "
  set -e
  for i in 1 2 3 4 5; do
    if curl --silent --show-error --fail \
      --cacert /etc/so-data-api/tls/ca.crt \
      --resolve 121.196.152.24:443:127.0.0.1 \
      https://121.196.152.24/healthz; then
      echo
      systemctl is-active so-data-api.service
      exit 0
    fi
    sleep 2
  done
  journalctl -u so-data-api.service -n 80 --no-pager
  exit 1
"

log "Deployment complete"
