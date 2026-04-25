#!/bin/bash
# SDPRS Edge Node Provisioning Script
# 在 Raspberry Pi OS Bookworm Lite 上執行
#
# 用法（LAN 模式）:
#   sudo ./setup_pi.sh <node_id> <server_ip> [--api-key KEY]
#   sudo ./setup_pi.sh glass_node_01 192.168.1.100 --api-key abc123
#
# 用法（雲端模式）:
#   sudo ./setup_pi.sh <node_id> --mode cloud --cloud-url URL --api-key KEY \
#                      [--mqtt-broker HOST --mqtt-port PORT \
#                       --mqtt-username USER --mqtt-password PASS]
#   sudo ./setup_pi.sh glass_node_01 --mode cloud \
#                      --cloud-url https://sdprs.zeabur.app/api \
#                      --api-key abc123
#
# 功能:
# 1. 系統配置 (hostname, 時區, tmpfs, watchdog)
# 2. 依賴安裝 (Python, FFmpeg, AutoSSH)
# 3. SSH 金鑰配置（LAN 模式才需要反向隧道）
# 4. 應用部署 + 寫入 config（API key、MQTT 等一次到位）
# 5. systemd 服務安裝（依模式啟用 LAN 或 cloud 服務）
# 6. 驗證

set -euo pipefail

# ===== 顏色定義 =====
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    cat <<EOF
用法:
  LAN 模式:    sudo $0 <node_id> <server_ip> [--api-key KEY]
  雲端模式:    sudo $0 <node_id> --mode cloud --cloud-url URL --api-key KEY \\
               [--mqtt-broker HOST --mqtt-port PORT \\
                --mqtt-username USER --mqtt-password PASS]

範例:
  sudo $0 glass_node_01 192.168.1.100 --api-key abc123
  sudo $0 glass_node_01 --mode cloud --cloud-url https://sdprs.zeabur.app/api --api-key abc123
EOF
}

# ===== 參數檢查 =====
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}錯誤: 此腳本需要以 root 權限執行${NC}"
    usage
    exit 1
fi

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

NODE_ID="$1"
shift

# Defaults
MODE="lan"
SERVER_IP=""
CLOUD_URL=""
API_KEY="changeme-random-secret-key"
MQTT_BROKER=""
MQTT_PORT="1883"
MQTT_USERNAME="sdprs"
MQTT_PASSWORD=""

# 第二個位置參數（向後相容）：若不是 --flag，視為 server_ip
if [[ $# -gt 0 && "$1" != --* ]]; then
    SERVER_IP="$1"
    shift
fi

# 解析具名參數
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)            MODE="$2"; shift 2 ;;
        --server-ip)       SERVER_IP="$2"; shift 2 ;;
        --cloud-url)       CLOUD_URL="$2"; shift 2 ;;
        --api-key)         API_KEY="$2"; shift 2 ;;
        --mqtt-broker)     MQTT_BROKER="$2"; shift 2 ;;
        --mqtt-port)       MQTT_PORT="$2"; shift 2 ;;
        --mqtt-username)   MQTT_USERNAME="$2"; shift 2 ;;
        --mqtt-password)   MQTT_PASSWORD="$2"; shift 2 ;;
        -h|--help)         usage; exit 0 ;;
        *)                 echo -e "${RED}未知參數: $1${NC}"; usage; exit 1 ;;
    esac
done

# 模式驗證
if [[ "$MODE" == "lan" ]]; then
    if [[ -z "$SERVER_IP" ]]; then
        echo -e "${RED}錯誤: LAN 模式需要 server_ip${NC}"
        usage
        exit 1
    fi
    [[ -z "$MQTT_BROKER" ]] && MQTT_BROKER="$SERVER_IP"
elif [[ "$MODE" == "cloud" ]]; then
    if [[ -z "$CLOUD_URL" ]]; then
        echo -e "${RED}錯誤: 雲端模式需要 --cloud-url${NC}"
        usage
        exit 1
    fi
else
    echo -e "${RED}錯誤: --mode 必須是 lan 或 cloud${NC}"
    exit 1
fi

# 從 node_id 提取編號 (glass_node_01 -> 01)
NODE_NUM="${NODE_ID##*_}"
HOSTNAME="sdprs-glass-${NODE_NUM}"

# 計算隧道端口 (glass_node_01 -> 18554)
BASE_PORT=18554
TUNNEL_PORT=$((BASE_PORT + 10#$NODE_NUM - 1))

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}SDPRS 邊緣節點佈建腳本${NC}"
echo -e "${GREEN}======================================${NC}"
echo "Node ID:     ${NODE_ID}"
echo "Hostname:    ${HOSTNAME}"
echo "Mode:        ${MODE}"
if [[ "$MODE" == "lan" ]]; then
    echo "Server IP:   ${SERVER_IP}"
    echo "Tunnel Port: ${TUNNEL_PORT}"
else
    echo "Cloud URL:   ${CLOUD_URL}"
    echo "MQTT Broker: ${MQTT_BROKER:-<none>}"
fi
echo ""

# ===== Step 1/6: 系統配置 =====
echo -e "${GREEN}[Step 1/6] 系統配置...${NC}"

# 設定 hostname
echo "設定 hostname 為 ${HOSTNAME}..."
hostnamectl set-hostname "${HOSTNAME}"

# 更新 /etc/hosts
sed -i "s/127.0.1.1.*/127.0.1.1\t${HOSTNAME}/" /etc/hosts

# 設定時區
echo "設定時區為 Asia/Macau..."
timedatectl set-timezone Asia/Macau

# 掛載 tmpfs (保護 SD 卡)
echo "配置 tmpfs..."
if ! grep -q "tmpfs /tmp" /etc/fstab; then
    echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,size=100M 0 0" >> /etc/fstab
    echo "tmpfs /var/log tmpfs defaults,noatime,nosuid,size=50M 0 0" >> /etc/fstab
    mount /tmp 2>/dev/null || true
    mount /var/log 2>/dev/null || true
fi

# 啟用硬體 watchdog
echo "啟用硬體 watchdog..."
if ! grep -q "dtparam=watchdog=on" /boot/firmware/config.txt; then
    echo "dtparam=watchdog=on" >> /boot/firmware/config.txt
fi

apt-get update
apt-get install -y watchdog

cat > /etc/watchdog.conf << EOF
max-load-1 = 24
watchdog-device = /dev/watchdog
watchdog-timeout = 15
realtime = yes
priority = 1
EOF

systemctl enable watchdog
systemctl start watchdog

# ===== Step 2/6: 依賴安裝 =====
echo -e "${GREEN}[Step 2/6] 安裝依賴...${NC}"

apt-get update
apt-get install -y \
    python3-pip python3-venv python3-dev \
    portaudio19-dev libportaudio2 \
    ffmpeg autossh git avahi-daemon \
    libopenjp2-7 libtiff6

# 建立 sdprs 用戶
if ! id -u sdprs &>/dev/null; then
    useradd -r -m -s /bin/bash sdprs
    echo "已建立 sdprs 用戶"
fi

# 確保 sdprs 用戶在 video 和 audio 群組（攝像頭 + 麥克風存取）
usermod -aG video sdprs
usermod -aG audio sdprs
echo "sdprs 用戶已加入 video, audio 群組"

# 建立目錄結構
mkdir -p /opt/sdprs/edge_glass
mkdir -p /opt/sdprs/edge_glass/events
mkdir -p /opt/sdprs/edge_glass/buffer
mkdir -p /opt/mediamtx

# 建立 Python venv
echo "建立 Python 虛擬環境..."
python3 -m venv /opt/sdprs/edge_glass/venv

# 安裝 Python 依賴
echo "安裝 Python 套件..."
/opt/sdprs/edge_glass/venv/bin/pip install --upgrade pip
/opt/sdprs/edge_glass/venv/bin/pip install \
    opencv-python-headless paho-mqtt httpx pyyaml numpy psutil pyaudio

# 下載 mediamtx
echo "下載 MediaMTX..."
MEDIAMTX_VERSION="v1.9.3"
if [[ ! -f /opt/mediamtx/mediamtx ]]; then
    wget -qO- "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64v8.tar.gz" | tar xz -C /opt/mediamtx/
    chmod +x /opt/mediamtx/mediamtx
fi

# ===== Step 3/6: SSH 金鑰配置（雲端模式跳過）=====
if [[ "$MODE" == "lan" ]]; then
    echo -e "${GREEN}[Step 3/6] SSH 金鑰配置（LAN 模式）...${NC}"

    mkdir -p /home/sdprs/.ssh
    chmod 700 /home/sdprs/.ssh
    chown sdprs:sdprs /home/sdprs/.ssh
    if [[ ! -f /home/sdprs/.ssh/id_ed25519 ]]; then
        sudo -u sdprs ssh-keygen -t ed25519 -f /home/sdprs/.ssh/id_ed25519 -N ""
        echo "已生成 SSH 金鑰對"
    fi

    cat > /home/sdprs/.ssh/config << EOF
Host ${SERVER_IP}
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF
    chown sdprs:sdprs /home/sdprs/.ssh/config
    chmod 600 /home/sdprs/.ssh/config

    echo ""
    echo -e "${YELLOW}=== 公鑰（將以下公鑰加入中央伺服器）===${NC}"
    cat /home/sdprs/.ssh/id_ed25519.pub
    echo -e "${YELLOW}建議用法：在中央伺服器執行${NC}"
    echo -e "${YELLOW}  ssh-copy-id -i /home/sdprs/.ssh/id_ed25519.pub sdprs@${SERVER_IP}${NC}"
    echo -e "${YELLOW}（或從中央伺服器執行 ssh-copy-id 到此節點）${NC}"
    echo ""
else
    echo -e "${GREEN}[Step 3/6] 雲端模式 — 跳過 SSH 金鑰配置${NC}"
fi

# ===== Step 4/6: 應用部署 =====
echo -e "${GREEN}[Step 4/6] 應用部署...${NC}"

# 若有 git 可 clone，否則假設檔案已存在
if [[ -d /opt/sdprs/edge_glass/.git ]]; then
    echo "更新現有 repository..."
    cd /opt/sdprs/edge_glass && git pull
elif command -v git &>/dev/null && [[ -n "${GIT_REPO:-}" ]]; then
    echo "Clone repository..."
    git clone "${GIT_REPO}" /opt/sdprs/edge_glass
else
    echo "假設檔案已在 /opt/sdprs/edge_glass/"
fi

if [[ "$MODE" == "lan" ]]; then
    # 建立 config.yaml（LAN 模式）
    cat > /opt/sdprs/edge_glass/config.yaml << EOF
# SDPRS Edge Node Configuration (LAN mode)
# Auto-generated by setup_pi.sh

node_id: "${NODE_ID}"
server:
  api_url: "http://${SERVER_IP}:8000/api"
  api_key: "${API_KEY}"
  mqtt_broker: "${MQTT_BROKER}"
  mqtt_port: ${MQTT_PORT}

camera:
  rtsp_url: "rtsp://localhost:8554/stream"
  fps: 15
  resolution: [1280, 720]

detection:
  visual:
    enabled: true
    model: "yolov8n.pt"
    confidence_threshold: 0.5
  audio:
    enabled: true
    sample_rate: 16000
    frame_duration_ms: 30
    peak_threshold_db: 35

buffer:
  pre_event_seconds: 5
  post_event_seconds: 10
  max_frames: 500

storage:
  events_dir: "./events"
  mp4_filename: "{timestamp}.mp4"

stream:
  enabled: true
  tunnel_port: ${TUNNEL_PORT}
EOF

    # 建立 .env.tunnel
    cat > /opt/sdprs/edge_glass/.env.tunnel << EOF
# SSH Tunnel Configuration
SERVER_HOST=${SERVER_IP}
TUNNEL_PORT=${TUNNEL_PORT}
SSH_USER=sdprs
SSH_KEY=/home/sdprs/.ssh/id_ed25519
EOF
    chmod 600 /opt/sdprs/edge_glass/.env.tunnel
else
    # 改寫 config.zeabur.yaml 中的 placeholder（雲端模式）
    CFG=/opt/sdprs/edge_glass/config.zeabur.yaml
    if [[ ! -f "$CFG" ]]; then
        echo -e "${RED}錯誤: $CFG 不存在，請先 git clone 程式碼${NC}"
        exit 1
    fi
    python3 - "$CFG" "$NODE_ID" "$CLOUD_URL" "$API_KEY" \
        "$MQTT_BROKER" "$MQTT_PORT" "$MQTT_USERNAME" "$MQTT_PASSWORD" <<'PYEOF'
import json, re, sys
path, node_id, cloud_url, api_key, mqtt_broker, mqtt_port, mqtt_user, mqtt_pw = sys.argv[1:9]
with open(path, encoding='utf-8') as f:
    s = f.read()
def q(v): return json.dumps(v, ensure_ascii=False)  # YAML-safe quoting
s = re.sub(r'^node_id:.*$',           f'node_id: {q(node_id)}',                  s, flags=re.M)
s = re.sub(r'^(\s*api_url:).*$',      lambda m: f'{m.group(1)} {q(cloud_url)}',  s, flags=re.M)
s = re.sub(r'^(\s*api_key:).*$',      lambda m: f'{m.group(1)} {q(api_key)}',    s, flags=re.M)
s = re.sub(r'^(\s*mqtt_broker:).*$',  lambda m: f'{m.group(1)} {q(mqtt_broker)}',s, flags=re.M)
s = re.sub(r'^(\s*mqtt_port:).*$',    lambda m: f'{m.group(1)} {int(mqtt_port)}',s, flags=re.M)
s = re.sub(r'^(\s*mqtt_username:).*$',lambda m: f'{m.group(1)} {q(mqtt_user)}',  s, flags=re.M)
s = re.sub(r'^(\s*mqtt_password:).*$',lambda m: f'{m.group(1)} {q(mqtt_pw)}',    s, flags=re.M)
with open(path, 'w', encoding='utf-8') as f:
    f.write(s)
print("已寫入 config.zeabur.yaml")
PYEOF
fi

# 設定權限
chown -R sdprs:sdprs /opt/sdprs

# ===== Step 5/6: systemd 服務安裝 =====
echo -e "${GREEN}[Step 5/6] 安裝 systemd 服務...${NC}"

# 複製服務檔案
cp /opt/sdprs/edge_glass/systemd/sdprs-edge.service /etc/systemd/system/
cp /opt/sdprs/edge_glass/systemd/autossh-tunnel.service /etc/systemd/system/
cp /opt/sdprs/edge_glass/systemd/sdprs-edge-cloud.service /etc/systemd/system/

# 安裝 curl（雲端模式 ExecStartPre 需要）
apt-get install -y curl 2>/dev/null || true

systemctl daemon-reload

if [[ "$MODE" == "lan" ]]; then
    # 確保雲端服務未啟用
    systemctl disable sdprs-edge-cloud 2>/dev/null || true
    systemctl stop sdprs-edge-cloud 2>/dev/null || true

    systemctl enable sdprs-edge
    systemctl enable autossh-tunnel
    systemctl start sdprs-edge
    echo "已啟用 sdprs-edge + autossh-tunnel（autossh 將在公鑰配置後生效）"
else
    # 確保 LAN 服務未啟用
    systemctl disable sdprs-edge autossh-tunnel 2>/dev/null || true
    systemctl stop sdprs-edge autossh-tunnel 2>/dev/null || true

    systemctl enable sdprs-edge-cloud
    systemctl start sdprs-edge-cloud
    echo "已啟用 sdprs-edge-cloud（雲端模式）"
fi

# ===== Step 6/6: 驗證 =====
echo -e "${GREEN}[Step 6/6] 驗證...${NC}"

echo ""
echo "服務狀態:"
if [[ "$MODE" == "lan" ]]; then
    echo "  sdprs-edge:     $(systemctl is-active sdprs-edge || echo 'inactive')"
    echo "  autossh-tunnel: $(systemctl is-active autossh-tunnel || echo 'inactive')"
else
    echo "  sdprs-edge-cloud: $(systemctl is-active sdprs-edge-cloud || echo 'inactive')"
fi
echo "  watchdog:       $(systemctl is-active watchdog || echo 'inactive')"

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}佈建完成！(模式: ${MODE})${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo "下一步:"
if [[ "$MODE" == "lan" ]]; then
    echo "1. 將公鑰加入中央伺服器（建議從中央伺服器執行）:"
    echo "   ssh-copy-id sdprs@$(hostname -I | awk '{print $1}')"
    echo "   或在此節點執行: ssh-copy-id sdprs@${SERVER_IP}"
    echo ""
    echo "2. 啟動 SSH 隧道:"
    echo "   sudo systemctl start autossh-tunnel"
    echo ""
    echo "3. 查看日誌:"
    echo "   journalctl -u sdprs-edge -f"
else
    echo "1. 確認雲端伺服器已部署，環境變數 EDGE_API_KEY 與此節點 --api-key 一致"
    echo ""
    echo "2. 查看日誌:"
    echo "   journalctl -u sdprs-edge-cloud -f"
    echo ""
    echo "3. 中央儀表板應顯示節點上線（透過 snapshot POST）"
fi
echo ""