#!/bin/bash
# ============================================================
# SDPRS 備份腳本：從 Zeabur 伺服器拉取資料到 Raspberry Pi
# Smart Disaster Prevention Response System
#
# 安裝：
#   sudo cp backup_from_zeabur.sh /opt/sdprs/scripts/
#   sudo chmod +x /opt/sdprs/scripts/backup_from_zeabur.sh
#
# 設定 Cron（以 sdprs 用戶執行）：
#   sudo crontab -u sdprs -e
#   加入：0 3 * * * /opt/sdprs/scripts/backup_from_zeabur.sh >> /var/log/sdprs_backup.log 2>&1
#
# 前置條件：
#   1. Pi 已生成 SSH Key：
#      ssh-keygen -t ed25519 -f /home/sdprs/.ssh/zeabur_backup -N ""
#   2. 公鑰已加入 Zeabur 伺服器：
#      cat /home/sdprs/.ssh/zeabur_backup.pub
#      → 複製輸出，貼入 Zeabur 伺服器的 ~/.ssh/authorized_keys
#   3. 已填入下方設定變數
# ============================================================

set -euo pipefail

# ── 設定區（部署後填入實際值）─────────────────────────────────
ZEABUR_HOST=""            # Zeabur 伺服器 IP（部署後填入）
ZEABUR_USER="root"        # Zeabur VPS 登入用戶
SSH_KEY="/home/sdprs/.ssh/zeabur_backup"
DATABASE_URL=""           # PostgreSQL 連線串（部署後填入）
BACKUP_DIR="/opt/backup/sdprs"
RETAIN_DAYS=30
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
# ─────────────────────────────────────────────────────────────

# 驗證必填設定
if [ -z "$ZEABUR_HOST" ] || [ -z "$DATABASE_URL" ]; then
    echo "$LOG_PREFIX 錯誤：請先填入 ZEABUR_HOST 和 DATABASE_URL" >&2
    exit 1
fi

# 建立備份目錄
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/storage"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/db/sdprs_${TIMESTAMP}.sql.gz"

echo "$LOG_PREFIX ===== SDPRS 備份開始 ====="

# ── 1. 備份 PostgreSQL ──────────────────────────────────────────
echo "$LOG_PREFIX [1/3] 備份 PostgreSQL 資料庫..."

ssh -i "$SSH_KEY" \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=30 \
    "$ZEABUR_USER@$ZEABUR_HOST" \
    "pg_dump '$DATABASE_URL' | gzip" \
    > "$DUMP_FILE"

DB_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo "$LOG_PREFIX       完成：$DUMP_FILE ($DB_SIZE)"

# ── 2. 同步 MP4 影片（增量）────────────────────────────────────
echo "$LOG_PREFIX [2/3] 同步 MP4 影片..."

rsync -avz --delete \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30" \
    "$ZEABUR_USER@$ZEABUR_HOST:/app/storage/" \
    "$BACKUP_DIR/storage/"

MP4_COUNT=$(find "$BACKUP_DIR/storage" -name "*.mp4" 2>/dev/null | wc -l)
echo "$LOG_PREFIX       完成：共 $MP4_COUNT 個 MP4 檔案"

# ── 3. 清理超過保留期的舊備份 ───────────────────────────────────
echo "$LOG_PREFIX [3/3] 清理 ${RETAIN_DAYS} 天前的舊備份..."

DELETED=$(find "$BACKUP_DIR/db" -name "*.sql.gz" -mtime +"$RETAIN_DAYS" -print -delete | wc -l)
echo "$LOG_PREFIX       已刪除 $DELETED 個舊備份"

# ── 摘要 ────────────────────────────────────────────────────────
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
BACKUP_COUNT=$(find "$BACKUP_DIR/db" -name "*.sql.gz" | wc -l)
echo "$LOG_PREFIX ===== 備份完成 ====="
echo "$LOG_PREFIX 備份數量：$BACKUP_COUNT 個 DB 快照"
echo "$LOG_PREFIX 總佔用空間：$TOTAL_SIZE"
echo ""
