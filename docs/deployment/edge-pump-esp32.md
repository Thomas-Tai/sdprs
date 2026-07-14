# 水泵節點部署（ESP32）

> 刷寫並設定 ESP32 MicroPython 水泵控制節點：水位感測、滯後控制、MQTT 回報。面向具基本硬體與命令列操作經驗者。

← 返回[部署指南](README.md)　·　硬體與網路先看 [../hardware-network.md](../hardware-network.md)

---

> **硬體前提：**
>
> - ESP32 WiFi 峰值電流 300-400mA，**必須外接 5V/2A 電源**，不可僅靠 USB 電腦供電
> - 使用短而粗的 USB 數據線（非充電線），直插主機板 USB 埠（避免 Hub）
> - Windows 需先裝 CP2102 或 CH340 USB 驅動

## 步驟 1：在 EMQX Dashboard 新增認證用戶

EMQX Dashboard → **Access Control → Authentication → Users → Add**

- Username: `pump_node_01`
- Password: 任選（步驟 3 會輸入給腳本）

## 步驟 2：在電腦上安裝刷寫工具

```bash
pip install esptool mpremote
```

> **Windows 提示：** 若 PowerShell 認不到 `esptool`/`mpremote`，改打 `python -m esptool` / `python -m mpremote`，或把 `%APPDATA%\Python\Python3xx\Scripts` 加入 PATH。

## 步驟 3：執行一鍵刷寫腳本

連接 ESP32 → 確認串口（Windows 用 `python -m serial.tools.list_ports`，Linux `ls /dev/ttyUSB*`）→ 跑：

```bash
cd sdprs/scripts
chmod +x setup_esp32.sh
./setup_esp32.sh /dev/ttyUSB0      # Linux
# Windows (Git Bash): ./setup_esp32.sh COM8
```

腳本會**互動式**詢問 WiFi SSID/密碼、MQTT broker/密碼（密碼輸入不回顯），然後依序：

1. 下載 MicroPython 韌體（首次；之後快取在 `firmware/`）
2. 擦除 + 刷寫韌體（DIO mode + 0x1000）
3. 從 `edge_pump/config.py` 模板生成你的設定（**只在記憶體與裝置上**，不寫到磁碟）
4. 上傳 6 個檔案（`config.py` 先，`boot.py` 最後）
5. `mpremote reset` 軟重啟

**全部用參數一次跑完（CI / 重複部署）：**

```bash
./setup_esp32.sh /dev/ttyUSB0 \
    --wifi-ssid "MyWiFi" \
    --wifi-pass "wifi-secret" \
    --mqtt-broker "<emqx-public-ip>" \
    --mqtt-port 32150 \
    --mqtt-username pump_node_01 \
    --mqtt-password "<emqx-password>" \
    --node-id pump_node_01
```

**只更新程式碼（不重刷韌體、不改 WiFi）：**

```bash
./setup_esp32.sh /dev/ttyUSB0 --skip-flash --skip-config
```

> **連不上？** 按住 ESP32 板上的 **BOOT** 鍵再執行；仍失敗就換 USB 線。
> **`flash read err, 1000`** 已在腳本內加 `-fm dio` 解決，不會再遇到。

## 步驟 4：驗證

```bash
mpremote connect /dev/ttyUSB0 repl
# 按 Ctrl+D 軟重啟，應看到：
#   [BOOT] SDPRS Pump Node booting (minimal mode)...
#   [MAIN] Entering main loop...
#   [MQTT] WiFi connected: 192.168.x.x
#   [MQTT] Connected to broker!
#   [MQTT] Published: {"node_id":"pump_node_01","pump_state":"OFF","water_level":0.0}
# 按 Ctrl+] 退出
```

也可在 EMQX Dashboard `/#/clients` 頁面確認 `pump_node_01` 在線。

## 附錄：手動刷寫流程（不使用腳本時）

<details>
<summary>展開：純命令列版本（給特殊環境/教學）</summary>

```powershell
# 1. 偵測串口
python -m serial.tools.list_ports

# 2. 下載 MicroPython 韌體
# https://micropython.org/download/ESP32_GENERIC/

# 3. 擦除 + 燒錄
python -m esptool --port COM8 erase_flash
python -m esptool --chip esp32 --port COM8 --baud 460800 write_flash -z -fm dio 0x1000 ESP32_GENERIC-v1.24.1.bin

# 4. 上傳程式（boot.py 最後！）
cd sdprs/edge_pump
for %f in (config.py main.py control_logic.py sensors.py pump_controller.py mqtt_client.py boot.py) do mpremote connect COM8 cp %f :%f

# 5. 改 WiFi/MQTT — 推薦 Thonny GUI，或 ampy 取下/編輯/上傳
ampy --port COM8 --delay 2 get config.py config.local.py
# (編輯 config.local.py)
ampy --port COM8 --delay 2 put config.local.py config.py
del config.local.py
```

</details>

## 開發模式 vs 生產模式

config.py 中有以下開關：

| 參數            | 開發      | 生產     | 說明                                                 |
| --------------- | --------- | -------- | ---------------------------------------------------- |
| `WDT_ENABLED` | `False` | `True` | 看門狗：生產時啟用防卡死，開發時關閉避免 REPL 被中斷 |

## 故障排除

| 問題                                          | 解決方案                                                                                                |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `flash read err, 1000`                      | 加 `-fm dio` 參數重新燒錄                                                                             |
| COM 埠閃現消失 / brownout 重啟                | USB 供電不足 → 外接 5V/2A 電源；或使用最小化 boot.py（不在開機時連 WiFi）                              |
| `ampy: failed to access COMx`               | 關閉所有串口工具、執行 `powershell Stop-Process -Name python,ampy -Force`、等 3 秒重試                |
| `esptool` 連不上                            | 按住 BOOT 鍵再執行指令                                                                                  |
| WiFi 錯誤 `sta is connecting, return error` | `wlan.disconnect()` 後才呼叫 `wlan.connect()`；確保只呼叫一次 connect()                             |
| MQTT 連線失敗                                 | 確認 EMQX 已新增 `pump_node_01` 用戶                                                                  |
| `umqtt` 找不到                              | MicroPython 官方 ESP32 韌體已內建，無需額外安裝                                                         |
| WDT 導致反覆重啟                              | config.py 中設 `WDT_ENABLED = False`                                                                  |
| `ampy put` 後仍用舊程式碼                   | 遠端路徑不加 `:` 前綴，正確：`ampy put file.py file.py`                                             |
| NTP 顯示 `2000-01-01`                       | 確認 UDP 123 port 未被防火牆封鎖；NTP 在 WiFi 首次連線後自動嘗試（pool.ntp.org → time.cloudflare.com） |
| 乾燥時雨量顯示 100%                           | 雨滴感測器 ADC 反相：需用 `100.0 - (median/4095.0)*100.0`（見 sensors.py）                       |

## 監控牆整合（水泵節點）

監控牆（`/monitor`）除攝像頭快照外，頁面下方會自動顯示水泵節點狀態卡片：

- **雨量感測**：進度條顯示 0–100%（0% = 乾燥，100% = 完全濕潤）
- **水泵狀態**：`運行中`（紅色）/ `待機`（綠色）
- **即時更新**：由 WebSocket `pump_status` 訊息驅動，無需手動刷新
- **初始數據**：頁面載入時從 `/api/nodes` 獲取既有數據（離線節點也顯示）

---

## 下一步

- 另可部署玻璃偵測節點：[edge-glass.md](edge-glass.md)。
- 全部就緒後，執行 [verification.md](verification.md) 完整驗證清單。
