# MQTT 主題參考

本文件列出邊緣節點、水泵節點與中央伺服器之間的 MQTT 主題、QoS、方向與 payload 欄位。
所有主題／QoS 常數的單一來源為 `shared/mqtt_topics.py`。

← 返回[文件索引](../README.md)

## 主題結構

```
sdprs/edge/{node_id}/{category}
```

- `node_id`：如 `glass_node_01`、`pump_node_01`
- 方向：Pi → Broker（`heartbeat`、`pump_status`、`stream_status`）
  ／ Server → Pi（`cmd/*`）
- 中央伺服器訂閱 `sdprs/edge/+/heartbeat`、`sdprs/edge/+/pump_status`、
  `sdprs/edge/+/stream_status`（`SUB_ALL_*` 常數）。
- 邊緣節點訂閱 `sdprs/edge/{node_id}/cmd/#`（`sub_cmd_all(node_id)`）。

## 主題總覽

| 主題                                            | QoS | 方向           | 說明                                    |
| ----------------------------------------------- | --- | -------------- | --------------------------------------- |
| `sdprs/edge/{node_id}/heartbeat`                | 0   | Edge → Server  | 心跳，30 秒間隔；也承載 LWT offline 訊號 |
| `sdprs/edge/{node_id}/pump_status`              | 0   | Pump → Server  | 水泵狀態＋水位＋旗標，10 秒間隔          |
| `sdprs/edge/{node_id}/stream_status`            | 1   | Edge → Server  | HLS 串流啟動／停止事件                   |
| `sdprs/edge/{node_id}/cmd/stream_start`         | 1   | Server → Edge  | 啟動 HLS 串流                            |
| `sdprs/edge/{node_id}/cmd/stream_stop`          | 1   | Server → Edge  | 停止 HLS 串流                            |
| `sdprs/edge/{node_id}/cmd/update`               | 1   | Server → Edge  | 遠端更新觸發（TODO）                     |
| `sdprs/edge/{node_id}/cmd/simulate_trigger`     | 1   | Server → Edge  | 模擬觸發測試                            |
| `sdprs/edge/{node_id}/cmd/snooze`               | 1   | Server → Edge  | 節點靜音配置（音訊觸發抑制）             |

> 目前只有 `edge_glass`（Pi）訂閱 `cmd/#`。
> **`edge_pump`（ESP32）為 publish-only**，`mqtt_client.py` 沒有任何 `subscribe()` 或
> `set_callback()` 呼叫；欲對水泵新增 cmd 通道時需在該檔案加入排程呼叫 `check_msg()`。

---

## Payload 欄位

### `heartbeat`（Edge → Server）

由 `edge_glass/comms/mqtt_client.py::_publish_heartbeat` 產生（30 秒間隔）；
LWT 訊息由 `will_set` 於連線時登記。

| 欄位                    | 型別           | 說明                                                                                    |
| ----------------------- | -------------- | --------------------------------------------------------------------------------------- |
| `node_id`               | string         | 節點識別碼                                                                              |
| `timestamp`             | ISO-8601 UTC   | 產生時間                                                                                |
| `status`                | `"online"`     | 一般心跳固定 `online`；LWT 訊息為 `"OFFLINE"`                                           |
| `cpu_temp`              | float °C       | 讀 `/sys/class/thermal/thermal_zone0/temp`；不可得回 `50.0`                             |
| `memory_usage_percent`  | float 0–100    | 由 `psutil` 或 `/proc/meminfo` 取得                                                     |
| `uptime_seconds`        | int            | 節點進程啟動至今秒數                                                                    |
| `buffer_health`         | `"ok"` \| `"degraded"` | 相機讀取失敗時為 `degraded`；復原後回 `ok`                                       |
| `visual_health`         | `"ok"` / `"paused"` / `"blinded"` / `"disabled"` / `"stale"` / `"unknown"` | 視覺偵測器狀態     |
| `audio_health`          | 同上           | 音訊偵測器狀態                                                                          |
| `online`                | bool           | 僅在 LWT 訊息出現，值為 `false`；中央伺服器據此立即標為 OFFLINE                        |

### `pump_status`（Pump → Server）

由 `edge_pump/mqtt_client.py::build_payload` 產生（10 秒間隔）。附加欄位（`battery_voltage`、
`power_source`）只在硬體引腳已接線時才會加入 payload；未接線時直接省略欄位（不寫 `null`）。

| 欄位              | 型別                       | 說明                                                                 |
| ----------------- | -------------------------- | -------------------------------------------------------------------- |
| `node_id`         | string                     |                                                                      |
| `timestamp`       | ISO-8601 UTC               | ESP32 本地時間（NTP 同步後才準確）                                   |
| `pump_state`      | `"ON"` \| `"OFF"` \| `"UNKNOWN"` | 目前泵狀態                                                     |
| `water_level`     | float 0–100                | 類比水位百分比（四捨五入 1 位小數）                                  |
| `raining`         | bool \| `null`             | 雨感狀態；`RAIN_ENABLED=false` 時為 `null`                           |
| `float_safe`      | bool \| `null`             | 底部浮球「非乾燒」；`FLOAT_ENABLED=false` 時 `null`                  |
| `high_water`      | bool \| `null`             | 高水位開關；`HIGH_WATER_ENABLED=false` 時 `null`                     |
| `sensor_conflict` | bool                       | 感測器互相矛盾（如「水位 0% + 高水位觸發」）                          |
| `dry_run_protect` | bool                       | 乾燒保護中；泵被壓制 OFF                                             |
| `reason`          | 決策碼字串                 | `STANDBY` / `HYSTERESIS_ON` / `RAIN_TRIGGER` / `HIGH_WATER` / `HOLD` / `CONFLICT_BURST_ON` / `CONFLICT_BURST_REST` / `CONFLICT_LATCH_OFF` / `DRY_RUN_OFF` / `MAX_RUNTIME_REST` |
| `battery_voltage` | float V（選）              | 只在 `BATTERY_ADC_PIN` 接線後出現                                    |
| `power_source`    | `"mains"` \| `"battery"`（選） | 只在 `POWER_SOURCE_PIN` 接線後出現                               |

### `stream_status`（Edge → Server）

| 欄位          | 型別                | 說明                                              |
| ------------- | ------------------- | ------------------------------------------------- |
| `status`      | `"active"` / `"stopped"` | HLS 串流狀態                                 |
| `tunnel_port` | int                 | SSH 反向隧道連接埠（雲端模式為 0 或省略）         |
| `format`      | `"hls"`             | 串流格式                                          |

### `cmd/stream_start` / `cmd/stream_stop`（Server → Edge）

由 `MQTTService.send_stream_command` 送出。

| 欄位        | 型別         | 說明                          |
| ----------- | ------------ | ----------------------------- |
| `timestamp` | ISO-8601 UTC | 中央伺服器送出時間戳          |

### `cmd/snooze`（Server → Edge）

由 `/api/nodes/{id}/snooze` 觸發，`MQTTService.send_snooze_config` 送出。

| 欄位            | 型別                 | 說明                                                        |
| --------------- | -------------------- | ----------------------------------------------------------- |
| `snooze_until`  | ISO-8601 UTC \| `null` | 靜音截止；`null` = 立即取消                                |
| `snooze_reason` | string \| `null`     | 操作員填寫的原因                                            |
| `timestamp`     | ISO-8601 UTC         | 送出時間                                                    |

> 邊緣端消化 cmd/snooze 的行為尚待實作；當前僅伺服器端 event_service 消費此 snooze 旗標。

### `cmd/simulate_trigger`（Server → Edge）

由運維／演練程序透過 MQTT 直接發送。`edge_glass_main.py` 註冊回呼將
`sim_request[0] = True`，主迴圈下一次迭代呼叫 `trigger_engine.force_trigger()` 生成一筆
`is_simulation=True` 事件，走完整警報→ MP4 上傳流程；此類事件：

- 事件 metadata 帶 `is_simulation: true`；伺服器可據此於稽核／統計中排除。
- **不消耗真實警報冷卻期**（`cooldown_seconds` 30s）。演練不會遮蔽同一節點隨後的真實警報。
- 通過 AND-gate（視覺 + 音訊）判定：`force_trigger` 略過相關性視窗，直接產出事件。

---

## 測試觸發（演練）操作範例

假設伺服器 broker 在 `192.168.1.100:1883`，欲對 `glass_node_01` 演練：

```bash
# 觸發一次模擬事件（payload 可為空 JSON，內容目前未讀取）
mosquitto_pub -h 192.168.1.100 -p 1883 \
    -u sdprs -P "$MQTT_PASSWORD" \
    -t "sdprs/edge/glass_node_01/cmd/simulate_trigger" \
    -q 1 -m '{}'
```

若中央伺服器有 Mosquitto + TLS，加上 `--cafile <ca>.pem --tls-version tlsv1.2` 與正確帳密。

**期望觀察：**

- 邊緣節點日誌出現 `Simulate trigger command received: {}` 與 `Simulation event created`
- 中央伺服器 `/ws` 推送 `new_alert`；儀表板出現一筆新警報
- 影片 metadata 帶 `is_simulation: true`
- 演練後隨時可再送出，因為模擬事件不受 30 秒冷卻期限制

---

## 相關文件

- REST／WebSocket 端點請見 [API 參考](api.md)。
- MQTT broker 加固與 Mosquitto 設定請見 [../../deploy/MQTT_SECURITY.md](../../deploy/MQTT_SECURITY.md)。
