# SDPRS Zeabur 雲端遷移報告

**生成日期：** 2026-03-27  
**版本：** v1.0  
**狀態：** 參考文件（未實作）

---

## 第一部分：方案改動報告

---

### 架構對比

**現有架構（本地 LAN）**

```
Pi 4 (邊緣節點)
  │ MQTT TCP 1883 ──────────┐
  │ HTTP REST ──────────────┤
  │ SSH 反向隧道 (autossh) ──┤
  └─────────────────────────►Pi 5 (中央伺服器)
                              ├── FastAPI :8000
                              ├── Mosquitto :1883
                              ├── Nginx :80
                              └── SQLite WAL
```

**目標架構（Zeabur 雲端）**

```
Pi 4 (邊緣節點)
  │ MQTT over TLS ──────────┐
  │ HTTPS REST ─────────────┤
  │ RTMP push (串流) ────────┤
  └─────────────────────────►Zeabur (中央伺服器)
                              ├── FastAPI (HTTP 服務)
                              ├── EMQX (MQTT broker, TCP 端口)
                              ├── mediamtx (串流, TCP 端口)
                              └── PostgreSQL (managed)
```

> **SSH 反向隧道完全消除**，所有通訊改為 Pi 主動對外連線。

---

### 改動清單（逐檔分析）

---

#### 1. `deploy/docker-compose.yml` — 重大改動

**改動原因：** Mosquitto 替換為 EMQX；SQLite volume 替換為 Zeabur PostgreSQL；加入 mediamtx 串流容器。

| 現有 | 改後 |
|------|------|
| `eclipse-mosquitto:2` | `emqx/emqx:5.8` |
| `../data:/app/data` (SQLite volume) | 移除（DB 改 PostgreSQL 外部服務） |
| 無 mediamtx | 加入 `mediamtx/mediamtx:latest` |
| `MQTT_BROKER=mosquitto` | `MQTT_BROKER=emqx` |
| `MQTT_PORT=1883` | `MQTT_PORT=1883`（EMQX 內網仍 1883） |

**新增環境變數（`.env` 需加入）：**

```
MQTT_USERNAME=sdprs
MQTT_PASSWORD=<strong-password>
DATABASE_URL=postgresql://user:pass@zeabur-pg-host:5432/sdprs
```

---

#### 2. `deploy/mosquitto.conf` — 整個刪除

替換為 EMQX，此檔案不再需要。改用 EMQX 的環境變數配置認證：

```yaml
# docker-compose.yml 中 EMQX 環境變數
EMQX_MQTT__LISTENER__TCP__DEFAULT__BIND: "0.0.0.0:1883"
EMQX_AUTHORIZATION__NO_MATCH: deny
EMQX_AUTHENTICATION__1__MECHANISM: password_based
```

---

#### 3. `central_server/config.py` — 中等改動

**新增 4 個設定欄位（在 Settings class 中）：**

```python
# 現有（需保留）
MQTT_BROKER: str = "localhost"
MQTT_PORT: int = 1883

# 新增
MQTT_USERNAME: str = ""          # EMQX 認證用戶名
MQTT_PASSWORD: str = ""          # EMQX 認證密碼
MQTT_USE_TLS: bool = False       # 雲端部署設為 True
DATABASE_URL: str = ""           # PostgreSQL 連線串（空則用 SQLite）
```

> `DB_PATH` 保留作 SQLite 回退，`DATABASE_URL` 優先使用。

---

#### 4. `central_server/database.py` — 重大改動（最複雜）

**現有問題：** 純 SQLite + `threading.Lock` 同步模式（第 44–52 行），無法在雲端容器重啟後持久化（Zeabur 免費方案無 Volume）。

**改動方向：** 加入 `databases` 庫作 PostgreSQL 非同步支援，保留 SQLite 作本地/開發回退。

```python
# 新增依賴（requirements.txt 加入）：
asyncpg>=0.29.0
databases[postgresql]>=0.9.0

# database.py 改動：
# 1. init_db() 根據 DATABASE_URL 決定使用 PG 或 SQLite
# 2. SQL 語法差異：SQLite AUTOINCREMENT → PostgreSQL SERIAL
# 3. SQLite ON CONFLICT(node_id) DO UPDATE → 標準 UPSERT 語法
# 4. get_db_cursor() context manager 改為 async with
```

**SQL 語法主要差異：**

| SQLite（現有） | PostgreSQL（改後） |
|----------------|-------------------|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` |
| `?` 佔位符 | `$1, $2...` 佔位符 |
| `PRAGMA journal_mode=WAL` | 移除（PG 原生支援） |
| `PRAGMA busy_timeout=5000` | 移除 |
| `sqlite3.Row` → `dict(row)` | `asyncpg.Record` → `dict(row)` |

---

#### 5. `central_server/services/mqtt_service.py` — 小改動

**現有 `start()` 方法（第 90–107 行）：**

```python
self.client = mqtt.Client(client_id="central_server")
self.client.connect(self.settings.MQTT_BROKER, self.settings.MQTT_PORT, keepalive=60)
```

**需加入：**

```python
# 認證
if self.settings.MQTT_USERNAME:
    self.client.username_pw_set(
        self.settings.MQTT_USERNAME,
        self.settings.MQTT_PASSWORD
    )
# TLS（雲端 EMQX 強制加密）
if self.settings.MQTT_USE_TLS:
    self.client.tls_set()  # 使用系統 CA bundle
```

---

#### 6. `central_server/requirements.txt` — 小改動

新增：

```
asyncpg>=0.29.0
databases[postgresql]>=0.9.0
```

---

#### 7. `edge_glass/config.yaml` — 小改動

**現有（第 59–63 行）：**

```yaml
server:
  api_url: "http://central-server:8000/api"
  mqtt_broker: "central-server"
  mqtt_port: 1883
```

**改後：**

```yaml
server:
  api_url: "https://your-app.zeabur.app/api"    # Zeabur 分配的 HTTPS URL
  mqtt_broker: "hkg1.clusters.zeabur.com"        # Zeabur EMQX TCP 轉發地址
  mqtt_port: 34567                               # Zeabur 分配的隨機端口（部署後確認）
  mqtt_username: "sdprs"
  mqtt_password: "changeme"
  mqtt_use_tls: false                            # Zeabur TCP 轉發不含 TLS
```

> **注意：** Zeabur TCP 端口轉發為純 TCP，不含 TLS 包裝。若需 TLS，改用 EMQX WebSocket over HTTPS（port 8083 → Zeabur HTTP 代理）。

---

#### 8. `edge_glass/stream/rtsp_server.py` — `_start_ssh_tunnel()` 方法替換

**現有邏輯（第 204–239 行）：** 執行 `ssh -N -R {port}:localhost:8554 sdprs@server`

**替換方向（選一）：**

**方案 A（推薦 MVP）—— RTMP Push**

```python
def _start_stream_push(self) -> bool:
    # 改為 mediamtx RTMP push 到 Zeabur mediamtx 實例
    # rtmp://zeabur-mediamtx:1935/live/glass_node_01
    push_url = self._config.get("server", {}).get("mediamtx_rtmp_url", "")
    # 修改 mediamtx.yml 加入 publishers → rtmp push target
    ...
```

**方案 B（最簡單）—— 暫時停用串流**

```python
def _start_stream_push(self) -> bool:
    # 串流暫停：雲端部署 MVP 不支援即時串流
    logger.info("Live stream disabled in cloud deployment mode")
    return True  # 假裝成功，不影響其他功能
```

---

#### 9. `edge_glass/systemd/autossh-tunnel.service` — 整個刪除

此 systemd 服務完全不需要。Pi 改為主動連 EMQX，無需反向隧道。

```bash
# 在 Pi 上執行：
sudo systemctl stop autossh-tunnel
sudo systemctl disable autossh-tunnel
sudo rm /etc/systemd/system/autossh-tunnel.service
sudo systemctl daemon-reload
```

---

#### 10. `central_server/main.py` — 小改動

**現有問題（第 68 行）：** `DB_PATH` hardcoded fallback，改動：啟動時根據 `DATABASE_URL` 決定初始化方式。

另需加入 WebSocket 心跳（防 Zeabur proxy 60s 超時斷線）：

```python
# 在 websocket_service.py 中加入 ping
await websocket.send_json({"type": "ping"})  # 每 30s 發送
```

---

### 改動工作量估計

| 優先級 | 檔案 | 難度 | 預計工時 |
|--------|------|------|----------|
| P0 必須 | `database.py` SQLite→PG | 高 | 4h |
| P0 必須 | `docker-compose.yml` EMQX | 低 | 1h |
| P0 必須 | `config.py` + MQTT TLS | 低 | 1h |
| P0 必須 | `mqtt_service.py` 認證 | 低 | 0.5h |
| P0 必須 | `edge_glass/config.yaml` | 低 | 0.5h |
| P0 必須 | 刪除 `autossh-tunnel.service` | 低 | 0.5h |
| P1 重要 | `rtsp_server.py` 串流替換 | 中 | 2h |
| P1 重要 | WebSocket 心跳 | 低 | 0.5h |
| P2 選做 | 遷移舊 SQLite 資料到 PG | 中 | 1h |
| **合計** | | | **~11h** |

---

## 第二部分：可行性建議報告

---

### 整體可行性評分

| 維度 | 評分 | 說明 |
|------|------|------|
| 技術可行性 | ★★★★☆ | 主要元件均有 Zeabur 驗證案例 |
| 實作難度 | ★★★☆☆ | SQLite→PG 是最大風險 |
| 成本合理性 | ★★★☆☆ | 流量費用需謹慎評估 |
| 解決 AP 隔離 | ★★★★★ | 完全消除 Pi-to-Pi 依賴 |
| 維護複雜度 | ★★★☆☆ | 比本地方案多了雲端管理 |

---

### 成本估算

**每月費用（1 個 Pi 4 邊緣節點）：**

| 項目 | 計算 | 月費 |
|------|------|------|
| Zeabur Dev Plan（基本） | 固定 | ~$5 USD |
| PostgreSQL 儲存（1GB） | $0.20/GB | $0.20 |
| 快照流量 | 480p JPEG ~15KB × 86400s × 30天 = 38.9GB | $2.89 |
| MP4 上傳流量 | 事件少，假設 10次/月 × 30MB = 300MB | $0.03 |
| **合計** | | **~$8–10 USD/月** |

> **警告：** 每增加 1 個 Pi 邊緣節點，快照流量增加約 40GB/月（$4/月）。3 個節點 = 約 $20/月。

**長期較省方案：** 快照僅在偵測到動態時才上傳（需修改 `snapshot.py`），可降低 80% 流量成本。

---

### 主要風險與對策

**風險 1：Zeabur TCP 端口不穩定**

- 問題：EMQX TCP 端口 `:34567` 可能在重新部署後變更
- 影響：Pi `config.yaml` 需手動更新 MQTT 端口
- 對策：改用 MQTT over WebSocket（EMQX 的 `:8083`），透過 Zeabur HTTP 代理，端口固定為 443，**完全解決此問題**

**風險 2：SQLite → PostgreSQL 遷移複雜**

- 問題：`database.py` 是同步 SQLite，需完全重寫為 async PostgreSQL
- 影響：整個資料層改動，測試量大
- 對策：用 `databases` 庫支援同一套 API 橋接兩種 DB；或先用 SQLite + Zeabur Volume 作過渡（需付費方案）

**風險 3：串流功能暫時失效**

- 問題：SSH 反向隧道移除後，HLS 串流需要重新設計
- 影響：保安無法在儀表板即時看攝像頭畫面
- 對策：MVP 先用「每秒快照監控牆」（已實作）替代，串流後續再做

**風險 4：WebSocket 在 Zeabur 代理超時**

- 問題：Zeabur nginx 可能有 60s idle timeout
- 影響：儀表板 WebSocket 連線意外斷開，警報推送失效
- 對策：FastAPI 加 30s 心跳 ping；前端加自動重連邏輯（5s retry）

---

### 最終建議

```
優先選項排序：

1. ★★★★★  有線網路（Cat5e/Cat6）
   → 零成本、零改動、今天就能解決
   → 問題：需要場地允許拉線

2. ★★★★☆  Zeabur 全雲（本報告方案）
   → ~$8–10/月，約 11 小時工作量
   → 問題：SQLite→PG 改動風險需謹慎測試
   → 優點：完全雲端管理，場地無任何網路要求

3. ★★★☆☆  路由器設定（關閉 AP 隔離）
   → 聯繫網路管理員，請求關閉 AP Client Isolation
   → 部分校園/辦公室網路可能無法做到
```

**若決定執行 Zeabur 方案，建議的實作順序：**

1. 先在本地 Docker Compose 驗證 EMQX + PostgreSQL 組合
2. 再部署到 Zeabur，確認 MQTT TCP 端口號
3. 更新 Pi `config.yaml`，測試 MQTT 通訊
4. 測試 HTTP 快照上傳、MP4 上傳、WebSocket
5. 最後才處理串流替換（P1，不影響核心防災功能）

---

### 各元件 Zeabur 支援確認（基於官方文件查證，2025年）

| 需求 | Zeabur 支援 | 備註 |
|------|-------------|------|
| FastAPI | 官方支援 | 需綁定 `$PORT` 環境變數 |
| PostgreSQL | 一鍵部署，16000+ 次使用 | 取代 SQLite，內網連線免費 |
| MQTT TCP 1883 | 支援但端口被重映射 | 對外變成隨機高端口如 `:34567` |
| MQTT WebSocket | EMQX 官方模板（58+ 次部署） | 透過 HTTP 代理，端口固定 443 |
| 持久化儲存 | 付費方案 $0.20/GB/月 | 免費方案完全不支援 |
| WebSocket 長連線 | 支援，需加 keepalive | 無文件化超時設定，需實測 |
| 免費方案 | 完全不可用 | 容器服務需付費方案 |

---

*報告由 Claude Code 生成 | 基於 Zeabur 官方文件（2025年）及 SDPRS 程式碼分析*
