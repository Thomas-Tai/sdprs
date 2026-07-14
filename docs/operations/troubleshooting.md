# 故障排除

本文件依常見問題分類，提供診斷與解法命令，供運維與部署人員排查現場異常。日常操作命令請參閱 [日常運維手冊](runbook.md)。

← 返回[文件索引](../README.md)

## 問題 1：儀表板打不開 (HTTP 502 Bad Gateway)

```bash
# 1. 檢查 FastAPI 是否在運行
sudo systemctl status sdprs-server
# 如果是 failed，查看原因：
journalctl -u sdprs-server --since "10 minutes ago" --no-pager

# 2. 檢查 Nginx 配置
sudo nginx -t

# 3. 手動啟動試試
cd /opt/sdprs
sudo -u sdprs /opt/sdprs/central_server/venv/bin/uvicorn central_server.main:app --port 8000
# 看看有什麼錯誤訊息
```

## 問題 2：邊緣節點顯示離線

```bash
# 在邊緣節點上檢查
sudo systemctl status sdprs-edge

# 檢查網路是否通
ping 192.168.1.100

# 檢查 MQTT 連線
mosquitto_sub -h 192.168.1.100 -t "sdprs/#" -v
# 如果連不上，檢查中央伺服器的 Mosquitto
```

## 問題 3：串流沒有畫面

```bash
# 1. 檢查 SSH 隧道是否正常
sudo systemctl status autossh-tunnel

# 2. 檢查 mediamtx 是否在運行
ps aux | grep mediamtx

# 3. 檢查隧道端口是否在中央伺服器上監聽
# 在中央伺服器上執行：
ss -tlnp | grep 18554
# 應該看到 LISTEN 狀態
```

## 問題 4：WebSocket 斷連（儀表板頂列 Live 秒數不再重置）

- 確認你已經**登入**（WebSocket 需要 Session Cookie）
- V2 SPA 會自動重連（指數退避，最長 15 秒），可先靜候
- 重新整理頁面（按 F5）
- 檢查 FastAPI 服務是否在運行

## 問題 5：水泵不動作

> **重要澄清：** ESP32 水泵節點目前**只發佈狀態、不訂閱任何指令主題**
> （見 `edge_pump/mqtt_client.py` — 純 publish-only）。
> 因此無法透過 MQTT 遠端下達啟停命令；水泵完全由本地 `control_logic` 依水位／雨感／浮球
> 自動決策（滯後 20%/80%）。以下為正確診斷路徑：

```bash
# 1. 確認 ESP32 是否有發佈 pump_status（監聽 MQTT）
mosquitto_sub -h <中央伺服器IP> -t "sdprs/edge/pump_node_01/pump_status" -v
# 期望每 10 秒收到一筆 JSON（含 pump_state、water_level、raining、reason...）
# 若完全無訊息 → 檢查 ESP32 WiFi 與 MQTT 連線（下述步驟 3）

# 2. 從儀表板 / API 讀取水泵最新狀態
curl -u admin:密碼 http://<中央伺服器>:8000/api/nodes | \
    python3 -c "import json,sys; [print(n['node_id'],n['status'],n.get('pump_state'),n.get('water_level')) for n in json.load(sys.stdin)]"
# 或直接在儀表板「監看牆／節點狀態」頁面查看 pump_state / water_level / reason

# 3. 若無 pump_status → 檢查 broker 連通與 ESP32 REPL 記錄
mpremote connect /dev/ttyUSB0 repl
# 期望看到：
#   [MQTT] WiFi connected: 192.168.x.x
#   [MQTT] Connected to broker!
#   [MQTT] Published: {...}
# 若卡在 "[MQTT] WiFi timeout" → 檢查 SSID/密碼
# 若卡在 "[MQTT] MQTT error" → 檢查 MQTT_BROKER IP 與帳密

# 4. 現場實體排查（軟體無問題卻不動）：
#    - 繼電器指示燈是否亮（GPIO 26 訊號到位？）
#    - 水位感測器讀值是否 >= HIGH_THRESHOLD 但泵仍未啟動 → 檢查 sensor_conflict、dry_run_protect 旗標
#    - 檢查 12V/24V 泵供電、保險絲、繼電器 NO/NC 接線

# 5. 需要「重置」節點時只能物理斷電（無遠端重啟通道）
```

> **備註：** MQTT 主題 `sdprs/edge/{node_id}/cmd/*` 目前僅由 **玻璃節點（edge_glass）** 訂閱
> （用於 stream_start / stream_stop / simulate_trigger / snooze / update），
> 水泵節點的 cmd 通道未來若要新增，需先在 `edge_pump/mqtt_client.py` 加入
> `subscribe()` + `check_msg()` 排程；當前版本不支援。

## 問題 6：Pi 過熱重啟

```bash
# 查看 CPU 溫度
vcgencmd measure_temp
# 如果超過 80 度，檢查散熱器是否安裝正確

# 查看是否有降頻
vcgencmd get_throttled
# 0x0 = 正常，其他值 = 有問題
```

## 問題 7：pip install 極慢或不斷重試 (Connection reset by peer)

Pi OS 預設配置了 piwheels.org 作為 pip 額外索引源，但該伺服器連線不穩定。

```bash
# 檢查是否存在 piwheels 配置
cat /etc/pip.conf

# 移除 piwheels（備份後）
sudo mv /etc/pip.conf /etc/pip.conf.bak

# 重新安裝依賴
/opt/sdprs/central_server/venv/bin/pip install -r /opt/sdprs/central_server/requirements.txt --prefer-binary
```

> **注意：** `deploy_sync.sh init-server` 已自動處理此問題。

## 問題 8：節點之間 WiFi 無法互相 ping 通 (Destination Host Unreachable)

如果同一子網的設備互相 ping 顯示 `Destination Host Unreachable`，但開發電腦能 ping 到所有設備：

**原因：** 路由器啟用了 **AP 隔離（Client Isolation）**，阻止 WiFi 客戶端之間直接通訊。

**解法（選一個）：**

1. **關閉 AP 隔離**（推薦）— 登入路由器管理頁面，找 WiFi 設定 → AP Isolation → 關閉
2. **中央伺服器改用有線連接** — 有線和 WiFi 之間通常不受 AP 隔離限制
3. **兩台都接有線**（最穩定，正式部署建議）
4. **部署中央伺服器至雲端**（場地不允許有線/路由器設定時）— 見 [Zeabur 雲端方案](../deployment/README.md)

**驗證：**

```bash
# 在邊緣節點上 ping 中央伺服器
ping -c 2 <中央伺服器IP>
# 應該看到回應，不是 Unreachable
```

## 問題 9：SD 卡寫入錯誤

邊緣節點已配置 tmpfs，日誌不會寫入 SD 卡。但如果仍有問題：

```bash
# 檢查 SD 卡健康度
sudo dmesg | grep -i "error\|fail\|mmc"

# 檢查 tmpfs 掛載
df -h | grep tmpfs
```

## 問題 10：Zeabur 服務不斷 CRASH

**其一：Build 日誌為空**

檢查 `Dockerfile` 是否在 repo 根目錄：

```bash
ls sdprs/Dockerfile
# 如果不存在，重新推送：
git add Dockerfile && git commit -m "add Dockerfile" && git push
```

**其二：Build 成功但 Runtime crash**

在 Zeabur 面板查看 Runtime Logs，最常見原因：

| 錯誤訊息                                                                                                      | 原因                                            | 解法                                                         |
| ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------ |
| `pydantic ValidationError: 4 errors`                                                                        | 4 個必填環境變數未設定                          | Variables 添加 DASHBOARD_USER/PASS、EDGE_API_KEY、SECRET_KEY |
| Pod 拉取 alpine:latest                                                                                        | `zbpack.json` 缺少 `build_type: dockerfile` | 確認 `zbpack.json` 內容為 `{"build_type": "dockerfile"}` |
| `EXPOSE $PORT` build 失敗 | Dockerfile EXPOSE 不支援變數 | 使用 `EXPOSE 8080`，CMD 中用 `${PORT:-8080}` |                                                 |                                                              |
| 啟動成功但 502                                                                                                | 舊 Pod 仍在路由                                 | 等待舊 Pod 終止或強制重新部署                                |
| Runtime 無錯誤日誌                                                                                            | 缺少 `PYTHONUNBUFFERED=1`                     | Dockerfile 加 `ENV PYTHONUNBUFFERED=1`                     |
| `No module named 'asyncpg'`                                                                                 | 依賴未安裝                                      | 檢查 requirements.txt 含 asyncpg                             |

**其三：EMQX TCP 端口連不上**

```bash
# 在 Pi 上測試連線
nc -zv hkg1.clusters.zeabur.com 34567
# 如果超時，改用 WebSocket 模式：
# mqtt_broker: "your-app.zeabur.app"
# mqtt_port: 443
# mqtt_use_tls: true
```

**其四：儀表板 WebSocket 斷線**

- 確認已登入（WebSocket 需要 Session Cookie）
- 檢查 `SECRET_KEY` 環境變數是否已設定
- Zeabur 自動提供 HTTPS + wss://，确認 URL 使用 `https://`

## 問題 11：Edge 服務啟動後攝影機顯示 "Camera index out of range"

**原因：** `sdprs` 系統用戶沒有 `video` 群組權限，無法存取 `/dev/video0`（權限為 `crw-rw---- root video`）。

**診斷：**

```bash
groups sdprs
# 若輸出不含 video，即為此問題
```

**解法：**

```bash
sudo usermod -aG video sdprs
sudo systemctl restart sdprs-edge-cloud
```

**驗證攝影機裝置：**

```bash
v4l2-ctl --list-devices
# USB webcam 應顯示於 /dev/video0（或 /dev/video2 等）
# Pi Camera Module 需先 sudo modprobe bcm2835-v4l2
```

## 問題 12：Edge 服務啟動後 PyAudio SEGV / "Invalid sample rate"

**症狀：**

- `systemd` 顯示 `Main process exited, code=killed, status=11/SEGV`
- 或 `[Errno -9997] Invalid sample rate`

**原因一：`sdprs` 沒有 `audio` 群組 → SEGV**

```bash
sudo usermod -aG audio sdprs
sudo systemctl restart sdprs-edge-cloud
```

**原因二：`config.zeabur.yaml` 中 `device_index` 錯誤 → SEGV**

PyAudio 嘗試開啟不存在的裝置導致 PortAudio crash。確認正確 index：

```bash
/opt/sdprs/edge_glass/venv/bin/python 2>/dev/null -c "
import pyaudio
pa = pyaudio.PyAudio()
for i in range(pa.get_device_count()):
    d = pa.get_device_info_by_index(i)
    if d['maxInputChannels'] > 0:
        print(f'index={i}, name={d[\"name\"]}')
pa.terminate()
"
# 範例輸出：index=0, name=HD Pro Webcam C920: USB Audio (hw:2,0)
```

然後在 config 設定對應 index（通常為 `0`）。

**原因三：`sample_rate` 不被攝影機支援 → Invalid sample rate**

Logitech C920 麥克風只支援 `16000` 或 `32000` Hz（**不支援 44100 Hz**）：

```bash
arecord -D hw:2,0 --dump-hw-params /dev/null 2>&1 | grep RATE
# RATE: [16000 32000]
```

在 `config.zeabur.yaml` 修改：

```yaml
audio:
  device_index: 0       # PyAudio index（非 ALSA card 號碼）
  sample_rate: 16000    # C920 支援 16000 或 32000
```

## 問題 13：Edge 服務啟動後 `httpx.Timeout` ValueError / TypeError

**症狀：**

```
ValueError: httpx.Timeout must either include a default, or set all four parameters explicitly.
TypeError: Timeout.__init__() got an unexpected keyword argument 'default'
```

**原因：** 不同版本 httpx 的 `Timeout` API 不同。統一使用位置參數語法（所有版本相容）：

```python
# 錯誤（舊語法）：
httpx.Timeout(connect=15, read=60)

# 正確（相容所有版本）：
httpx.Timeout(60, connect=15)  # 第一個位置參數為 default
```

若手動修 Pi 上的檔案：

```bash
python3 -c "
import pathlib
for path in [
    '/opt/sdprs/edge_glass/comms/api_uploader.py',
    '/opt/sdprs/edge_glass/utils/snapshot.py',
]:
    p = pathlib.Path(path)
    c = p.read_text()
    old = 'timeout=httpx.Timeout(\n                connect=self.CONNECT_TIMEOUT,\n                read=self.READ_TIMEOUT,\n            ),'
    new = 'timeout=httpx.Timeout(self.READ_TIMEOUT, connect=self.CONNECT_TIMEOUT),'
    if old in c:
        p.write_text(c.replace(old, new))
        print(f'Fixed: {path}')
"
```
