#!/bin/bash
# ============================================================
# SDPRS 資料庫還原腳本
# 從 Pi 本地備份還原到 Zeabur PostgreSQL
#
# 用法：
#   ./restore_to_zeabur.sh                    # 還原最新備份
#   ./restore_to_zeabur.sh sdprs_20260327.sql.gz  # 還原指定備份
# ============================================================

set -euo pipefail

DATABASE_URL=""   # 填入 Zeabur PostgreSQL 連線串
BACKUP_DIR="/opt/backup/sdprs/db"

# 決定還原哪個備份
if [ -n "${1:-}" ]; then
    DUMP_FILE="$BACKUP_DIR/$1"
else
    DUMP_FILE=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
fi

if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
    echo "錯誤：找不到備份檔案：$DUMP_FILE" >&2
    exit 1
fi

echo "還原備份：$DUMP_FILE"
echo "目標資料庫：$DATABASE_URL"
read -p "確認還原？這會覆蓋現有資料 (y/N): " confirm

if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "取消還原"
    exit 0
fi

echo "開始還原..."
gunzip -c "$DUMP_FILE" | psql "$DATABASE_URL"
echo "還原完成：$(date)"
