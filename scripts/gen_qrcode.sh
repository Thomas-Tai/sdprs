#!/bin/bash
# SDPRS QR Code Generator Script
# 生成 WiFi 和 MQTT 配置 QR Code，方便手機掃描連接
#
# 用法: ./gen_qrcode.sh [options]
# 選項:
#   --wifi        生成 WiFi 連接 QR Code
#   --server      生成伺服器連接 QR Code
#   --pump        生成水泵節點配置 QR Code
#   --all         生成所有 QR Code
#
# 輸出: 終端顯示 QR Code，或保存為 PNG 檔案

set -euo pipefail

# ===== 顏色定義 =====
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ===== 路徑設定 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
OUTPUT_DIR="$PROJECT_ROOT/docs/qrcodes"

# ===== 預設值 =====
WIFI_SSID="SDPRS_IoT"
WIFI_PASS="changeme"
SERVER_HOST="sdprs.local"
SERVER_PORT="80"
MQTT_BROKER="192.168.1.100"
MQTT_PORT="1883"
PUMP_NODE_ID="pump_node_01"

# ===== 從 .env 讀取設定 =====
if [[ -f "$ENV_FILE" ]]; then
    echo -e "${BLUE}從 .env 讀取配置...${NC}"
    # 匯入環境變數（只取 KEY=value 格式）
    set -a
    source <(grep -E '^[A-Za-z_]+=' "$ENV_FILE" | head -20)
    set +a
    
    # 覆寫預設值
    WIFI_SSID="${WIFI_SSID:-$WIFI_SSID}"
    WIFI_PASS="${WIFI_PASSWORD:-$WIFI_PASS}"
    SERVER_HOST="${SERVER_HOST:-$SERVER_HOST}"
    MQTT_BROKER="${MQTT_BROKER:-$MQTT_BROKER}"
    MQTT_PORT="${MQTT_PORT:-$MQTT_PORT}"
fi

# ===== 檢查工具 =====
check_dependencies() {
    local missing=()
    
    # qrencode 用於生成 QR Code
    if ! command -v qrencode &> /dev/null; then
        missing+=("qrencode")
    fi
    
    # libqrencode: sudo apt install qrencode
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}缺少必要工具: ${missing[*]}${NC}"
        echo ""
        echo "安裝方式:"
        echo "  Ubuntu/Debian: sudo apt install qrencode"
        echo "  macOS: brew install qrencode"
        exit 1
    fi
}

# ===== 生成 WiFi QR Code =====
# 格式: WIFI:T:WPA;S:<SSID>;P:<password>;;
generate_wifi_qrcode() {
    echo -e "${GREEN}=== WiFi 連接 QR Code ===${NC}"
    echo "SSID: ${WIFI_SSID}"
    echo ""
    
    local wifi_data="WIFI:T:WPA;S:${WIFI_SSID};P:${WIFI_PASS};;"
    
    # 終端顯示
    echo "掃描此 QR Code 連接 WiFi:"
    qrencode -t ANSIUTF8 "$wifi_data"
    
    # 保存為檔案
    mkdir -p "$OUTPUT_DIR"
    local output_file="$OUTPUT_DIR/wifi_${WIFI_SSID}.png"
    qrencode -o "$output_file" "$wifi_data"
    echo ""
    echo "已保存: $output_file"
}

# ===== 生成伺服器 URL QR Code =====
generate_server_qrcode() {
    echo -e "${GREEN}=== 伺服器連接 QR Code ===${NC}"
    echo "URL: http://${SERVER_HOST}:${SERVER_PORT}"
    echo ""
    
    local url="http://${SERVER_HOST}:${SERVER_PORT}"
    
    # 終端顯示
    echo "掃描此 QR Code 開啟儀表板:"
    qrencode -t ANSIUTF8 "$url"
    
    # 保存為檔案
    mkdir -p "$OUTPUT_DIR"
    local output_file="$OUTPUT_DIR/server_dashboard.png"
    qrencode -o "$output_file" "$url"
    echo ""
    echo "已保存: $output_file"
}

# ===== 生成水泵節點配置 QR Code =====
generate_pump_qrcode() {
    echo -e "${GREEN}=== 水泵節點配置 QR Code ===${NC}"
    echo "Node ID: ${PUMP_NODE_ID}"
    echo "MQTT Broker: ${MQTT_BROKER}:${MQTT_PORT}"
    echo ""
    
    # JSON 格式配置
    local config_json=$(cat <<EOF
{
  "node_id": "${PUMP_NODE_ID}",
  "wifi_ssid": "${WIFI_SSID}",
  "mqtt_broker": "${MQTT_BROKER}",
  "mqtt_port": ${MQTT_PORT},
  "high_threshold": 80,
  "low_threshold": 20
}
EOF
)
    
    # 終端顯示
    echo "水泵節點配置 JSON:"
    echo "$config_json"
    echo ""
    echo "QR Code:"
    qrencode -t ANSIUTF8 "$config_json"
    
    # 保存為檔案
    mkdir -p "$OUTPUT_DIR"
    local output_file="$OUTPUT_DIR/pump_${PUMP_NODE_ID}_config.png"
    qrencode -o "$output_file" "$config_json"
    echo ""
    echo "已保存: $output_file"
}

# ===== 使用說明 =====
usage() {
    echo "SDPRS QR Code 生成器"
    echo ""
    echo "用法: $0 [選項]"
    echo ""
    echo "選項:"
    echo "  --wifi        生成 WiFi 連接 QR Code"
    echo "  --server      生成伺服器連接 QR Code"
    echo "  --pump        生成水泵節點配置 QR Code"
    echo "  --all         生成所有 QR Code"
    echo ""
    echo "環境變數 (可透過 .env 設定):"
    echo "  WIFI_SSID     WiFi 名稱 (預設: ${WIFI_SSID})"
    echo "  WIFI_PASSWORD WiFi 密碼"
    echo "  SERVER_HOST   伺服器位址 (預設: ${SERVER_HOST})"
    echo "  MQTT_BROKER   MQTT Broker 位址 (預設: ${MQTT_BROKER})"
    echo "  MQTT_PORT     MQTT Broker 端口 (預設: ${MQTT_PORT})"
    echo ""
    echo "範例:"
    echo "  $0 --wifi"
    echo "  $0 --server"
    echo "  $0 --all"
}

# ===== 主程式 =====
main() {
    check_dependencies
    
    local generate_wifi=false
    local generate_server=false
    local generate_pump=false
    
    # 解析參數
    while [[ $# -gt 0 ]]; do
        case $1 in
            --wifi)
                generate_wifi=true
                shift
                ;;
            --server)
                generate_server=true
                shift
                ;;
            --pump)
                generate_pump=true
                shift
                ;;
            --all)
                generate_wifi=true
                generate_server=true
                generate_pump=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo -e "${YELLOW}未知參數: $1${NC}"
                shift
                ;;
        esac
    done
    
    # 如果沒有指定任何選項，生成全部
    if [[ "$generate_wifi" == false && "$generate_server" == false && "$generate_pump" == false ]]; then
        generate_wifi=true
        generate_server=true
        generate_pump=true
    fi
    
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}SDPRS QR Code 生成器${NC}"
    echo -e "${GREEN}======================================${NC}"
    echo ""
    
    if [[ "$generate_wifi" == true ]]; then
        generate_wifi_qrcode
        echo ""
    fi
    
    if [[ "$generate_server" == true ]]; then
        generate_server_qrcode
        echo ""
    fi
    
    if [[ "$generate_pump" == true ]]; then
        generate_pump_qrcode
        echo ""
    fi
    
    echo -e "${GREEN}✓ 完成${NC}"
}

main "$@"