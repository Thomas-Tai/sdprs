#!/bin/bash
# SDPRS ESP32 Pump Node Provisioning Script
# 將 MicroPython 程式刷寫到 ESP32 並上傳水泵控制程式
#
# 用法: ./setup_esp32.sh <serial_port>
# 範例: ./setup_esp32.sh /dev/ttyUSB0
#        ./setup_esp32.sh COM3
#
# 前置條件:
# 1. Python 3.8+ 已安裝
# 2. esptool 已安裝: pip install esptool
# 3. mpremote 已安裝: pip install mpremote
# 4. ESP32 已連接到指定串口
#
# 功能:
# 1. 檢測 ESP32 晶片
# 2. 刷寫 MicroPython 韌體
# 3. 上傳水泵控制程式
# 4. 驗證檔案結構

set -euo pipefail

# ===== 顏色定義 =====
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ===== 參數檢查 =====
if [[ $# -lt 1 ]]; then
    echo -e "${YELLOW}用法: $0 <serial_port>${NC}"
    echo "範例: $0 /dev/ttyUSB0"
    echo "       $0 COM3"
    exit 1
fi

SERIAL_PORT="$1"

# ===== 路徑設定 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PUMP_DIR="$PROJECT_ROOT/edge_pump"
FIRMWARE_DIR="$PROJECT_ROOT/firmware"

# MicroPython 韌體 URL (ESP32)
MICROPYTHON_URL="https://micropython.org/resources/firmware/ESP32_GENERIC-20240105-v1.22.1.bin"
FIRMWARE_FILE="$FIRMWARE_DIR/micropython_esp32.bin"

# ===== 顯示標題 =====
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}SDPRS ESP32 水泵節點刷寫腳本${NC}"
echo -e "${GREEN}======================================${NC}"
echo "串口: ${SERIAL_PORT}"
echo "程式目錄: ${PUMP_DIR}"
echo ""

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

# ===== 下載 MicroPython 韌體 =====
echo -e "${BLUE}[Step 2/6] 準備 MicroPython 韌體...${NC}"

mkdir -p "$FIRMWARE_DIR"

if [[ ! -f "$FIRMWARE_FILE" ]]; then
    echo "下載 MicroPython 韌體..."
    curl -L -o "$FIRMWARE_FILE" "$MICROPYTHON_URL"
    echo -e "${GREEN}✓ 韌體已下載${NC}"
else
    echo -e "${GREEN}✓ 韌體已存在: ${FIRMWARE_FILE}${NC}"
fi

# ===== 檢測 ESP32 =====
echo -e "${BLUE}[Step 3/6] 檢測 ESP32 晶片...${NC}"

esptool_cmd="esptool.py"
if ! command -v esptool.py &> /dev/null; then
    esptool_cmd="esptool"
fi

echo "晶片資訊:"
$esptool_cmd --port "$SERIAL_PORT" chip_id || {
    echo -e "${RED}錯誤: 無法連接到 ESP32，請檢查串口${NC}"
    exit 1
}

echo -e "${GREEN}✓ ESP32 已檢測${NC}"

# ===== 刷寫 MicroPython =====
echo -e "${BLUE}[Step 4/6] 刷寫 MicroPython 韌體...${NC}"
echo "這可能需要幾秒鐘..."

# 擦除快閃記憶體
$esptool_cmd --port "$SERIAL_PORT" erase_flash

# 刷寫韌體
$esptool_cmd --chip esp32 --port "$SERIAL_PORT" --baud 460800 write_flash -z 0x1000 "$FIRMWARE_FILE"

echo -e "${GREEN}✓ MicroPython 已刷寫${NC}"

# ===== 上傳水泵程式 =====
echo -e "${BLUE}[Step 5/6] 上傳水泵控制程式...${NC}"

# 等待 ESP32 重啟
sleep 2

# 要上傳的檔案列表
FILES_TO_UPLOAD=(
    "config.py"
    "boot.py"
    "main.py"
    "water_sensor.py"
    "pump_controller.py"
    "mqtt_client.py"
)

# 使用 mpremote 上傳檔案
for file in "${FILES_TO_UPLOAD[@]}"; do
    src="$PUMP_DIR/$file"
    if [[ -f "$src" ]]; then
        echo "上傳: $file"
        mpremote connect "$SERIAL_PORT" cp "$src" :"$file"
    else
        echo -e "${YELLOW}警告: 檔案不存在: $src${NC}"
    fi
done

echo -e "${GREEN}✓ 程式已上傳${NC}"

# ===== 驗證 =====
echo -e "${BLUE}[Step 6/6] 驗證檔案結構...${NC}"

echo "列出 ESP32 檔案系統:"
mpremote connect "$SERIAL_PORT" ls

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}✓ ESP32 水泵節點刷寫完成！${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo "下一步:"
echo "1. 修改 config.py 中的 WiFi 和 MQTT 設定"
echo "   mpremote connect $SERIAL_PORT edit :config.py"
echo ""
echo "2. 重啟 ESP32 開始運行"
echo "   mpremote connect $SERIAL_PORT reset"
echo ""
echo "3. 查看輸出日誌"
echo "   mpremote connect $SERIAL_PORT repl"