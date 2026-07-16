#!/bin/bash
# SDPRS Central Server Provisioning Script
# 在 Raspberry Pi 5 + NVMe SSD 上執行
#
# 用法: sudo ./setup_server.sh [--static-ip 192.168.1.100]
# 範例: sudo ./setup_server.sh
#        sudo ./setup_server.sh --static-ip 192.168.1.100
#
# 功能:
# 1. 系統配置 (hostname, 時區, mDNS)
# 2. 依賴安裝 (Python, Mosquitto, Nginx)
# 3. 應用部署
# 4. systemd 服務安裝
# 5. Nginx 配置
# 6. 驗證

set -euo pipefail

# ===== 顏色定義 =====
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ===== 參數解析 =====
STATIC_IP=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --static-ip)
            STATIC_IP="$2"
            shift 2
            ;;
        *)
            echo -e "${YELLOW}未知參數: $1${NC}"
            shift
            ;;
    esac
done

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}SDPRS 中央伺服器佈建腳本${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# ===== Step 1/6: 系統配置 =====
echo -e "${GREEN}[Step 1/6] 系統配置...${NC}"

# 設定 hostname
hostnamectl set-hostname sdprs-server
echo "設定 hostname 為 sdprs-server"

# 設定時區
timedatectl set-timezone Asia/Macau
echo "設定時區為 Asia/Macau"

# 安裝 avahi-daemon (mDNS)
apt-get update
apt-get install -y avahi-daemon
echo "已安裝 Avahi (mDNS)，可通過 sdprs.local 存取"

# 設定靜態 IP (可選)
if [[ -n "${STATIC_IP}" ]]; then
    echo "設定靜態 IP: ${STATIC_IP}..."
    # 注意：實際配置需根據網路環境調整
    # 這裡僅提供範例
    cat > /etc/dhcpcd.conf << EOF
# Static IP configuration
interface eth0
static ip_address=${STATIC_IP}/24
static routers=$(echo ${STATIC_IP} | cut -d. -f1-3).1
static domain_name_servers=8.8.8.8 8.8.4.4
EOF
    echo "請重啟網路服務以套用靜態 IP"
fi

# ===== Step 2/6: 依賴安裝 =====
echo -e "${GREEN}[Step 2/6] 安裝依賴...${NC}"

apt-get update
apt-get install -y \
    python3-pip python3-venv \
    mosquitto mosquitto-clients \
    nginx \
    git avahi-daemon \
    sqlite3 \
    openssl

# 建立 sdprs 用戶
if ! id -u sdprs &>/dev/null; then
    useradd -r -m -s /bin/bash sdprs
    echo "已建立 sdprs 用戶"
fi

# 建立目錄結構
mkdir -p /opt/sdprs/central_server
mkdir -p /opt/sdprs/data
mkdir -p /opt/sdprs/storage/events
mkdir -p /var/log/sdprs

# 建立 Python venv
echo "建立 Python 虛擬環境..."
python3 -m venv /opt/sdprs/central_server/venv

# 安裝 Python 依賴
echo "安裝 Python 套件..."
/opt/sdprs/central_server/venv/bin/pip install --upgrade pip
/opt/sdprs/central_server/venv/bin/pip install \
    fastapi uvicorn paho-mqtt httpx pyyaml \
    python-multipart jinja2 python-multipart apscheduler

# ===== Step 3/6: 應用部署 =====
echo -e "${GREEN}[Step 3/6] 應用部署...${NC}"

# 若有 git 可 clone
if [[ -d /opt/sdprs/central_server/.git ]]; then
    echo "更新現有 repository..."
    cd /opt/sdprs/central_server && git pull
elif command -v git &>/dev/null && [[ -n "${GIT_REPO:-}" ]]; then
    echo "Clone repository..."
    git clone "${GIT_REPO}" /opt/sdprs/central_server
else
    echo "假設檔案已在 /opt/sdprs/central_server/"
fi

# ===== 隨機憑證產生 (SECURITY) =====
# 舊版寫入 `changeme-*` 佔位值並僅印警告，允許以已知憑證登入或偽造
# session cookie。config.py:validate_settings 現於啟動時 fail-closed。
echo "產生隨機憑證 (openssl rand)..."
RANDOM_PASS=$(openssl rand -base64 24 | tr -d '\n=' | head -c 24)
RANDOM_EDGE_KEY=$(openssl rand -hex 32)
RANDOM_SECRET=$(openssl rand -hex 32)

# 建立 .env 文件
cat > /opt/sdprs/.env << EOF
# SDPRS Central Server Environment Variables
# 憑證由 setup_server.sh 於 $(date -u +%Y-%m-%dT%H:%M:%SZ) 隨機產生。
# 服務啟動時會拒絕含 "changeme" 或短於 32 字元的秘密 (see MIGRATION.md)。

# Dashboard 登入
DASHBOARD_USER=admin
DASHBOARD_PASS=${RANDOM_PASS}

# Edge Node API Key (每個邊緣節點必須使用此金鑰認證)
EDGE_API_KEY=${RANDOM_EDGE_KEY}

# Session Secret (簽署 session cookie；輪換會強制所有使用者重登)
SECRET_KEY=${RANDOM_SECRET}

# MQTT
MQTT_BROKER=localhost
MQTT_PORT=1883

# Database
DB_PATH=/opt/sdprs/data/sdprs.db
# 儲存根目錄 (上傳與保留清理共用)。舊名 STORAGE_DIR 已棄用，
# 伺服器仍有回退支援但會記錄警告。
STORAGE_PATH=/opt/sdprs/storage

# Retention
RETENTION_DAYS=30
EOF

# 設定權限 (.env 是機密：root:sdprs 0600)
chown -R sdprs:sdprs /opt/sdprs
chmod 600 /opt/sdprs/.env

# 儲存初始 dashboard 密碼供操作員初次登入 (root-only 讀取)
INIT_CREDS_FILE=/root/sdprs_credentials.INITIAL.txt
cat > "$INIT_CREDS_FILE" << EOF
SDPRS 初始 Dashboard 憑證
產生時間: $(date -u +%Y-%m-%dT%H:%M:%SZ)

登入頁面: http://<server>/
使用者名稱: admin
初始密碼:   ${RANDOM_PASS}

首次登入後:
  1. 立即在 dashboard 或 /opt/sdprs/.env 中將 DASHBOARD_PASS 換成
     操作員記憶得住的新密碼 (至少 8 字元)。
  2. 將本檔內容備份至密碼管理器。
  3. 刪除本檔:  rm ${INIT_CREDS_FILE}

其他隨機憑證:
  EDGE_API_KEY  已寫入 .env；每個邊緣節點須配置相同值。輪換會斷開
                所有邊緣節點直到 re-flash。
  SECRET_KEY    已寫入 .env；輪換會使所有現有 session cookie 失效，
                所有使用者必須重新登入。

除非確有必要，請勿更動 EDGE_API_KEY / SECRET_KEY。
EOF
chmod 600 "$INIT_CREDS_FILE"

echo -e "${GREEN}已產生 .env 及隨機憑證${NC}"
echo "檔案位置: /opt/sdprs/.env (0600)"
echo -e "${YELLOW}初始 dashboard 密碼已寫入: ${INIT_CREDS_FILE}${NC}"
echo -e "${YELLOW}請立即記下並在首次登入後刪除該檔${NC}"
echo ""
echo "======================================"
echo "  初始 dashboard 密碼: ${RANDOM_PASS}"
echo "======================================"

# ===== Step 4/6: MQTT 配置 =====
echo -e "${GREEN}[Step 4/6] MQTT 配置...${NC}"

# 啟用 Mosquitto
systemctl enable mosquitto
systemctl start mosquitto

echo "Mosquitto 已啟動 (port 1883)"

# ===== Step 5/6: Nginx 配置 =====
echo -e "${GREEN}[Step 5/6] Nginx 配置...${NC}"

# 複製 nginx 配置
if [[ -f /opt/sdprs/deploy/nginx.conf ]]; then
    cp /opt/sdprs/deploy/nginx.conf /etc/nginx/sites-available/sdprs
    ln -sf /etc/nginx/sites-available/sdprs /etc/nginx/sites-enabled/sdprs
    rm -f /etc/nginx/sites-enabled/default
fi

# 測試並重載 nginx
nginx -t && systemctl reload nginx
systemctl enable nginx

echo "Nginx 已配置並啟動 (port 80)"

# ===== Step 6/6: systemd 服務安裝 =====
echo -e "${GREEN}[Step 6/6] 安裝 systemd 服務...${NC}"

# 複製服務檔案
cp /opt/sdprs/central_server/systemd/sdprs-server.service /etc/systemd/system/

# 重新載入 systemd
systemctl daemon-reload

# 啟用並啟動服務
systemctl enable sdprs-server
systemctl start sdprs-server

# ===== 驗證 =====
echo ""
echo "服務狀態:"
echo "  sdprs-server: $(systemctl is-active sdprs-server || echo 'inactive')"
echo "  mosquitto: $(systemctl is-active mosquitto || echo 'inactive')"
echo "  nginx: $(systemctl is-active nginx || echo 'inactive')"

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}佈建完成！${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo "存取儀表板:"
echo "  http://sdprs.local"
if [[ -n "${STATIC_IP}" ]]; then
    echo "  http://${STATIC_IP}"
fi
echo ""
echo "下一步:"
echo "1. 從 ${INIT_CREDS_FILE} 或上方輸出取得初始密碼並登入"
echo "2. 首次登入後立即修改 DASHBOARD_PASS: nano /opt/sdprs/.env"
echo "3. 重啟服務: systemctl restart sdprs-server"
echo "4. 記下密碼後刪除初始憑證檔: rm ${INIT_CREDS_FILE}"
echo "5. 查看日誌: journalctl -u sdprs-server -f"
echo ""
echo "登入帳號: admin"
echo "初始密碼: 請見上方 '初始 dashboard 密碼' 區塊"
echo ""