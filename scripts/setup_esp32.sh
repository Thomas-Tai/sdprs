#!/bin/bash
# SDPRS ESP32 Pump Node Provisioning Script
# 將 MicroPython 程式刷寫到 ESP32 並上傳水泵控制程式
#
# 用法:
#   ./setup_esp32.sh <serial_port> [options]
#
# 選項（任一未提供則互動式詢問；密碼讀取不回顯）:
#   --wifi-ssid SSID
#   --wifi-pass PASS
#   --mqtt-broker HOST
#   --mqtt-port PORT             (預設 1883)
#   --mqtt-username USER         (預設 pump_node_01)
#   --mqtt-password PASS
#   --node-id ID                 (預設 pump_node_01)
#   --skip-flash                 跳過韌體刷寫（更新程式時用）
#   --skip-config                跳過 config.py 生成
#
# 範例:
#   ./setup_esp32.sh /dev/ttyUSB0
#   ./setup_esp32.sh COM3 --wifi-ssid MyWiFi --mqtt-broker emqx.zeabur.app
#
# 前置條件: Python 3.8+ + esptool + mpremote (pip install esptool mpremote)

set -euo pipefail

# ===== 顏色定義 =====
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \?//'
}

# ===== 參數檢查 =====
if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

SERIAL_PORT="$1"
shift

WIFI_SSID=""
WIFI_PASS=""
MQTT_BROKER=""
MQTT_PORT="1883"
MQTT_USERNAME="pump_node_01"
MQTT_PASSWORD=""
NODE_ID="pump_node_01"
SKIP_FLASH=0
SKIP_CONFIG=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --wifi-ssid)       WIFI_SSID="$2"; shift 2 ;;
        --wifi-pass)       WIFI_PASS="$2"; shift 2 ;;
        --mqtt-broker)     MQTT_BROKER="$2"; shift 2 ;;
        --mqtt-port)       MQTT_PORT="$2"; shift 2 ;;
        --mqtt-username)   MQTT_USERNAME="$2"; shift 2 ;;
        --mqtt-password)   MQTT_PASSWORD="$2"; shift 2 ;;
        --node-id)         NODE_ID="$2"; shift 2 ;;
        --skip-flash)      SKIP_FLASH=1; shift ;;
        --skip-config)     SKIP_CONFIG=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *)                 echo -e "${RED}未知參數: $1${NC}"; usage; exit 1 ;;
    esac
done

# ===== 路徑設定 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PUMP_DIR="$PROJECT_ROOT/edge_pump"
FIRMWARE_DIR="$PROJECT_ROOT/firmware"

# MicroPython 韌體 URL (ESP32)
MICROPYTHON_URL="https://micropython.org/resources/firmware/ESP32_GENERIC-20250415-v1.25.0.bin"
FIRMWARE_FILE="$FIRMWARE_DIR/micropython_esp32.bin"

# ===== 顯示標題 =====
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}SDPRS ESP32 水泵節點刷寫腳本${NC}"
echo -e "${GREEN}======================================${NC}"
echo "串口:     ${SERIAL_PORT}"
echo "程式目錄: ${PUMP_DIR}"
echo "Node ID:  ${NODE_ID}"
[[ $SKIP_FLASH -eq 1 ]] && echo "(略過韌體刷寫)"
[[ $SKIP_CONFIG -eq 1 ]] && echo "(略過 config 生成)"
echo ""

# ===== 互動式收集 WiFi/MQTT 設定（若未經參數提供）=====
if [[ $SKIP_CONFIG -eq 0 ]]; then
    echo -e "${BLUE}[設定] WiFi / MQTT 配置${NC}"
    if [[ -z "$WIFI_SSID" ]]; then
        read -r -p "  WiFi SSID: " WIFI_SSID
    fi
    if [[ -z "$WIFI_PASS" ]]; then
        read -r -s -p "  WiFi 密碼（不顯示）: " WIFI_PASS; echo
    fi
    if [[ -z "$MQTT_BROKER" ]]; then
        read -r -p "  MQTT Broker (IP 或 hostname): " MQTT_BROKER
    fi
    if [[ -z "$MQTT_PASSWORD" ]]; then
        read -r -s -p "  MQTT 密碼（不顯示）: " MQTT_PASSWORD; echo
    fi
    echo ""
fi

# ===== 檢查工具 =====
echo -e "${BLUE}[Step 1/6] 檢查必要工具...${NC}"

if ! command -v esptool.py &> /dev/null && ! command -v esptool &> /dev/null; then
    echo -e "${RED}錯誤: esptool 未安裝${NC}"
    echo "安裝: pip install esptool"
    exit 1
fi

if ! command -v mpremote &> /dev/null; then
    echo -e "${RED}錯誤: mpremote 未安裝${NC}"
    echo "安裝: pip install mpremote"
    exit 1
fi

echo -e "${GREEN}✓ esptool 已安裝${NC}"
echo -e "${GREEN}✓ mpremote 已安裝${NC}"

esptool_cmd="esptool.py"
if ! command -v esptool.py &> /dev/null; then
    esptool_cmd="esptool"
fi

# ===== 韌體刷寫（可略過）=====
if [[ $SKIP_FLASH -eq 0 ]]; then
    echo -e "${BLUE}[Step 2/6] 準備 MicroPython 韌體...${NC}"
    mkdir -p "$FIRMWARE_DIR"
    if [[ ! -f "$FIRMWARE_FILE" ]]; then
        echo "下載 MicroPython 韌體..."
        curl -L -o "$FIRMWARE_FILE" "$MICROPYTHON_URL"
        echo -e "${GREEN}✓ 韌體已下載${NC}"
    else
        echo -e "${GREEN}✓ 韌體已存在: ${FIRMWARE_FILE}${NC}"
    fi

    echo -e "${BLUE}[Step 3/6] 檢測 ESP32 晶片...${NC}"
    $esptool_cmd --port "$SERIAL_PORT" chip_id || {
        echo -e "${RED}錯誤: 無法連接到 ESP32，請檢查串口或按住 BOOT 鍵${NC}"
        exit 1
    }
    echo -e "${GREEN}✓ ESP32 已檢測${NC}"

    echo -e "${BLUE}[Step 4/6] 刷寫 MicroPython 韌體（這可能需要 30-60 秒）...${NC}"
    $esptool_cmd --port "$SERIAL_PORT" erase_flash
    $esptool_cmd --chip esp32 --port "$SERIAL_PORT" --baud 460800 write_flash -z -fm dio 0x1000 "$FIRMWARE_FILE"
    echo -e "${GREEN}✓ MicroPython 已刷寫${NC}"
    sleep 2
else
    echo -e "${YELLOW}[Step 2-4/6] 跳過韌體刷寫${NC}"
fi

# ===== 生成 config.py（從 template + 用戶輸入）=====
if [[ $SKIP_CONFIG -eq 0 ]]; then
    echo -e "${BLUE}[Step 5/6] 產生 config.py...${NC}"
    TMP_CONFIG="$(mktemp)"
    trap 'rm -f "$TMP_CONFIG"' EXIT

    python3 - "$PUMP_DIR" "$WIFI_SSID" "$WIFI_PASS" "$MQTT_BROKER" \
        "$MQTT_PORT" "$MQTT_USERNAME" "$MQTT_PASSWORD" "$NODE_ID" \
        > "$TMP_CONFIG" <<'PYEOF'
import os, re, sys
pump_dir, ssid, wifi_pass, broker, port, user, mqtt_pw, node_id = sys.argv[1:9]
with open(os.path.join(pump_dir, "config.py"), encoding="utf-8") as f:
    s = f.read()
def set_str(s, key, val):
    return re.sub(rf'^{key}\s*=.*$', lambda m: f'{key} = {val!r}', s, count=1, flags=re.M)
def set_int(s, key, val):
    return re.sub(rf'^{key}\s*=.*$', lambda m: f'{key} = {int(val)}', s, count=1, flags=re.M)
s = set_str(s, "SSID", ssid)
s = set_str(s, "WIFI_PASS", wifi_pass)
s = set_str(s, "MQTT_BROKER", broker)
s = set_int(s, "MQTT_PORT", port)
s = set_str(s, "MQTT_USERNAME", user)
s = set_str(s, "MQTT_PASSWORD", mqtt_pw)
s = set_str(s, "NODE_ID", node_id)
sys.stdout.write(s)
PYEOF
    echo -e "${GREEN}✓ config.py 已產生（敏感字串只在記憶體+裝置上）${NC}"
else
    TMP_CONFIG="$PUMP_DIR/config.py"
    echo -e "${YELLOW}[Step 5/6] 跳過 config 生成，使用 repo 中的 config.py${NC}"
fi

# ===== 上傳水泵程式 =====
echo -e "${BLUE}[Step 6/6] 上傳水泵控制程式...${NC}"

# config.py 先上傳（其他模組 import 它），boot.py 最後（避免半成品開機）
mpremote connect "$SERIAL_PORT" cp "$TMP_CONFIG" :config.py
echo "  ✓ config.py"

for file in main.py water_sensor.py pump_controller.py mqtt_client.py boot.py; do
    src="$PUMP_DIR/$file"
    if [[ -f "$src" ]]; then
        mpremote connect "$SERIAL_PORT" cp "$src" :"$file"
        echo "  ✓ $file"
    else
        echo -e "${YELLOW}  ! 檔案不存在: $src${NC}"
    fi
done

echo -e "${GREEN}✓ 程式已上傳${NC}"

# ===== 驗證 =====
echo ""
echo "ESP32 檔案系統:"
mpremote connect "$SERIAL_PORT" ls

# 軟重啟
echo ""
echo "軟重啟 ESP32..."
mpremote connect "$SERIAL_PORT" reset || true

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}✓ ESP32 水泵節點佈建完成！${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo "查看即時日誌:"
echo "  mpremote connect $SERIAL_PORT repl"
echo "（按 Ctrl+] 退出 REPL）"