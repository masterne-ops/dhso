#!/usr/bin/env bash
# ────────────────────────────────────────────────
# SO 数据分析 — 每日备份
#  - DB（用 SQLite .backup 保证一致性，再 gzip）
#  - LLM/Anthropic/Auth 配置文件
# 留最近 7 天，超过自动清理
# ────────────────────────────────────────────────
set -euo pipefail

APP_ROOT=/opt/so-data-analytics
BACKUP_DIR="$APP_ROOT/backups"
DB_PATH="$APP_ROOT/db/product_flow.db"
DATE=$(date +%Y-%m-%d)
LOG="$APP_ROOT/logs/backup.log"

mkdir -p "$BACKUP_DIR"
echo "═══ $(date '+%Y-%m-%d %H:%M:%S') 开始备份 ═══" >> "$LOG"

# ── DB 备份（SQLite .backup 命令保证读写中也能一致快照）──
DB_OUT="$BACKUP_DIR/product_flow-$DATE.db"
if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" ".backup '$DB_OUT'" 2>>"$LOG"
    gzip -f "$DB_OUT"
    SIZE=$(du -h "${DB_OUT}.gz" | cut -f1)
    echo "✅ DB 备份: ${DB_OUT}.gz ($SIZE)" >> "$LOG"
else
    echo "⚠️ DB 不存在: $DB_PATH" >> "$LOG"
fi

# ── 配置文件备份 ──
CFG_OUT="$BACKUP_DIR/configs-$DATE.tar.gz"
if [ -d /root/.so_data_analytics ]; then
    tar czf "$CFG_OUT" -C /root .so_data_analytics 2>>"$LOG"
    echo "✅ 配置备份: $CFG_OUT" >> "$LOG"
else
    echo "⚠️ 无配置目录 /root/.so_data_analytics" >> "$LOG"
fi

# ── 清理 7 天前的备份 ──
DELETED=$(find "$BACKUP_DIR" -type f -mtime +7 -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "🗑  清理 $DELETED 个过期备份" >> "$LOG"
fi

echo "═══ 完成 — 备份目录: $(du -sh $BACKUP_DIR | cut -f1) ═══" >> "$LOG"
