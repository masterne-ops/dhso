#!/usr/bin/env bash
# ────────────────────────────────────────────────
# 清理 db/ 下的手动备份 product_flow.db.bak-*，只保留最新 KEEP 个
#   - 默认 dry-run(只列出，不删)；加 --apply 才真删
#   - 只匹配 product_flow.db.bak-*，绝不碰当前库 product_flow.db
# 用法:
#   prune_db_backups.sh            # 预览，保留最新 1 个
#   prune_db_backups.sh 2          # 预览，保留最新 2 个
#   prune_db_backups.sh --apply    # 真删，保留最新 1 个
#   prune_db_backups.sh 1 --apply  # 真删，保留最新 1 个
# ────────────────────────────────────────────────
set -euo pipefail
DB_DIR=/opt/so-data-analytics/db
KEEP=1
APPLY=0
for a in "$@"; do
    case "$a" in
        --apply) APPLY=1 ;;
        [0-9]*)  KEEP="$a" ;;
        *) echo "未知参数: $a"; exit 1 ;;
    esac
done

cd "$DB_DIR"
mapfile -t BAKS < <(ls -1t product_flow.db.bak-* 2>/dev/null || true)
TOTAL=${#BAKS[@]}

MODE=$([ "$APPLY" = 1 ] && echo "实删" || echo "预览(未删)")
echo "db/ 手动备份 product_flow.db.bak-* 共 $TOTAL 个 ｜ 保留最新 $KEEP 个 ｜ 模式: $MODE"
echo "────────────────────────────────────────"
if [ "$TOTAL" -le "$KEEP" ]; then
    echo "≤ 保留数，无需清理。"
    exit 0
fi

i=0
freed=0
for f in "${BAKS[@]}"; do
    sz=$(du -h "$f" | cut -f1)
    if [ "$i" -lt "$KEEP" ]; then
        echo "  ✅ 保留  $f  ($sz)"
    else
        echo "  🗑  删除  $f  ($sz)"
        if [ "$APPLY" = 1 ]; then rm -f "$f"; fi
    fi
    i=$((i + 1))
done
echo "────────────────────────────────────────"
if [ "$APPLY" = 1 ]; then
    echo "已删除 $((TOTAL - KEEP)) 个。db 目录现 $(du -sh "$DB_DIR" | cut -f1)，磁盘 $(df -h "$DB_DIR" | tail -1 | awk '{print "已用"$5"，剩"$4}')"
else
    echo "以上为预览。确认无误后加 --apply 执行：bash $0 $KEEP --apply"
fi
