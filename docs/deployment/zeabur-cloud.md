# 中央伺服器部署（Zeabur — 雲端方案）

> 將中央伺服器部署至 Zeabur 雲端，Raspberry Pi 只需出站網際網路連線。適用於 WiFi AP 隔離、不允許有線連接、無法修改路由器設定的場地。面向具基本命令列與雲端平台操作經驗者。

← 返回[部署指南](README.md)　·　硬體與網路先看 [../hardware-network.md](../hardware-network.md)

---

> **適用場景：WiFi AP 隔離、不允許有線連接、無法修改路由器設定。**
> 中央伺服器部署至 Zeabur 雲端，Raspberry Pi 只需要出站網際網路連線。

## 前提條件

- GitHub 帳號（Zeabur 透過 GitHub 自動部署）
- Zeabur 帳號 (https://zeabur.com)
- 已將 `sdprs` repo push 至 GitHub（含 `Dockerfile` 和 `zbpack.json`）

## 關鍵檔案

| 檔案                                 | 用途                                                                            |
| ------------------------------------ | ------------------------------------------------------------------------------- |
| `Dockerfile`                       | 定義 Docker 映像（python:3.11-slim base）                                       |
| `zbpack.json`                      | 告訴 Zeabur 使用 Dockerfile build（**必須**，否則 zbpack 自動偵測會失敗） |
| `.dockerignore`                    | 排除 edge_glass/、docs/ 等不需要的檔案                                          |
| `config.zeabur.yaml`               | Pi 端雲端模式配置                                                               |
| `systemd/sdprs-edge-cloud.service` | Pi 端雲端模式 systemd 服務                                                      |

### zbpack.json 內容

```json
{
  "build_type": "dockerfile"
}
```

> **重要：** 若此檔案不存在或包含 `build_command`/`start_command`，Zeabur 會使用 Python buildpack（alpine base），
> 導致映像不正確（Pod 拉取 alpine:latest 而非 python:3.11-slim）。

## 步驟 1：建立 Zeabur 專案並部署伺服器

1. 登入 Zeabur → **新建專案**
2. 選擇 **Deploy from GitHub** → 選擇 `sdprs` repo
3. Zeabur 偵測到 `zbpack.json` 中 `build_type: dockerfile` → 使用 `Dockerfile` 構建
4. 進入服務設定 → **Variables** → 依下表填入。完整範本見 [`central_server/.env.example`](../../central_server/.env.example)。

   **必填（4 個）— 缺任一個會 `pydantic ValidationError` 並 crash**

   | 變數名稱           | 範例值                            | 說明              |
   | ------------------ | --------------------------------- | ----------------- |
   | `DASHBOARD_USER` | `admin`                         | 儀表板帳號        |
   | `DASHBOARD_PASS` | `<dashboard-password>`          | 儀表板密碼        |
   | `EDGE_API_KEY`   | `<edge-api-key>`                | Pi 端 API 金鑰    |
   | `SECRET_KEY`     | `f8e2d1c4b7a6...` (64 字元 hex) | Session 加密金鑰  |

   > 生成隨機密鑰：`python3 -c "import secrets; print(secrets.token_hex(32))"`

   **進階（純玻璃偵測 MVP 可全部跳過 — Pi 透過 HTTP POST 即可運作）**

   | 變數名稱           | 範例值                                     | 何時需要                                    |
   | ------------------ | ------------------------------------------ | ------------------------------------------- |
   | `MQTT_BROKER`    | `${MOSQUITTO_HOST}`                      | 部署 Mosquitto 後填；優先用 Zeabur 自動注入的 `${MOSQUITTO_HOST}`（會解析成 private hostname），退而求其次寫死 `mosquitto.zeabur.internal` |
   | `MQTT_PORT`      | `1883`                                   | Mosquitto TCP 端口                               |
   | `MQTT_USERNAME`  | `sdprs`                                  | Mosquitto 認證                                   |
   | `MQTT_PASSWORD`  | `<mqtt-password>`                        | Mosquitto 認證                                   |
   | `DATABASE_URL`   | `postgresql://...` (見步驟 2)            | 部署 PostgreSQL 後手動加；空值 = SQLite WAL |

   > **MVP 結論：** 只設前 4 個必填即可。MQTT 用 `connect_async()` 非阻塞，
   > 連不上不會 crash；ESP32 水泵或遠端控制才需要 MQTT。

   > **`${MOSQUITTO_HOST}` 的優點：** Zeabur 在 mosquitto 服務部署後會自動在同專案其他服務暴露
   > `MOSQUITTO_HOST` auto-generated 變數（即 `mosquitto.zeabur.internal`）。用 `${MOSQUITTO_HOST}`
   > 引用比硬編碼字串更能適應 Zeabur 未來的 DNS 變動。

## 步驟 2：部署 PostgreSQL 資料庫（可選）

MVP 階段可跳過。未設定 `DATABASE_URL` 時，系統自動使用 SQLite（WAL mode）。

如需 PostgreSQL：

1. 在同一個 Zeabur 專案中，點擊 **新增服務** → **Marketplace** → **PostgreSQL**
2. 手動添加環境變數：

   ```
   DATABASE_URL=postgresql://${POSTGRES_USERNAME}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DATABASE}
   ```

   > Zeabur 不會自動注入 `DATABASE_URL`，需手動設定並使用 `${VAR}` 引用語法。
   > **變數命名（2026-07-14 實測）：** Zeabur Postgres Marketplace image 暴露的變數為
   > `POSTGRES_*`（無 `L`），不是 `POSTGRESQL_*`。
   > **不要使用 `${POSTGRES_URI}`** — Zeabur 提供的預組 URI 使用短前綴 `postgres://`，
   > SQLAlchemy 2.x 會拒絕；本 repo `central_server/database.py:98` 要求 `postgresql://` 前綴。
   >

## 步驟 3：部署 Mosquitto MQTT Broker（可選，MVP 可跳過）

如需 MQTT 心跳和指令功能（見下方 [MQTT 使用場景](#mqtt-使用場景)）：

> **為什麼選 Mosquitto 而非 EMQX：** Zeabur 專案共享記憶體池（本方案 ~2 GB）。
> EMQX 5.8 空載約 200 MB，且 Zeabur 所有服務 `request=0`（BestEffort QoS），
> 一旦其他服務吃記憶體，EMQX 是第一個被 evict 的對象（實測 2026-07-14）。
> Mosquitto 2.x 空載僅約 15 MB，同 MQTT 協定，SDPRS 程式碼零改動，只是沒有
> 網頁管理介面（可用 `mosquitto_sub` 除錯）。

**部署步驟：**

1. 點擊 **新增服務** → **Git** → 選擇 `Thomas-Tai/sdprs` repo
2. **Configure** → Root Directory 填 `deploy/mosquitto`
3. 部署完成後，到 **Settings** → Service Name 改為 `mosquitto`
4. **Variables** 頁面（必填，容器啟動時會在 image 內雜湊產生 passwd 檔）：

   | 變數名稱          | 範例值                          | 說明                           |
   | ----------------- | ------------------------------- | ------------------------------ |
   | `MQTT_USERNAME` | `sdprs`                       | Broker 帳號                    |
   | `MQTT_PASSWORD` | `<mqtt-password>`             | Broker 密碼（20+ 字元隨機字串） |

   > 生成隨機密碼：`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`
   > **切勿將密碼寫進 Dockerfile 或 mosquitto.conf** — `entrypoint.sh` 在容器啟動時
   > 用 `mosquitto_passwd -U` 雜湊寫入 `/mosquitto/config/passwd`，image layer 內不留明碼。

5. **Networking** 頁面：
   - 新增 `1883` (TCP) → 開啟 **Public**（給 Pi/ESP32 連入）
   - **不要開放** 其他端口（沒有管理介面，減少攻擊面）
6. 記下：
   - **Private** hostname（如 `mosquitto.zeabur.internal`）→ 給 Central Server 用
   - **Public** TCP 轉發地址（如 `<ip>:<random-port>`）→ 給 Pi/ESP32 用

> **關鍵：** Zeabur 內部服務名 ≠ Service Name。
> Central Server 的 `MQTT_BROKER` 必須填 **Private hostname**（如 `mosquitto.zeabur.internal`）。
> Pi/ESP32（外部網路）必須使用 **Public TCP 轉發地址**。

> **除錯**（Zeabur 沒有 GUI dashboard，用 CLI）：
> ```bash
> mosquitto_sub -h <public-ip> -p <public-port> -u sdprs -P <password> -t 'sdprs/#' -v
> ```

> **MQTT 安全設定進階說明** 詳見 [../../deploy/MQTT_SECURITY.md](../../deploy/MQTT_SECURITY.md)。

## MQTT 使用場景

| 功能                          | 需要 MQTT？      | 說明                                  |
| ----------------------------- | ---------------- | ------------------------------------- |
| 快照上傳（Pi → Server）      | **不需要** | HTTP POST                             |
| 事件告警（Pi → Server）      | **不需要** | HTTP POST JSON + MP4                  |
| 監控牆即時顯示                | **不需要** | 快照 POST 已寫入記憶體                |
| 節點心跳/離線偵測             | **需要**   | 每 30 秒心跳，90 秒未收到標記 OFFLINE |
| 遠端串流控制（開始/停止直播） | **需要**   | Server → MQTT → Pi                  |
| 遠端更新指令                  | **需要**   | Server → MQTT → Pi 執行 git pull    |
| ESP32 水泵節點                | **必需**   | ESP32 MicroPython 只支援 MQTT         |

> **結論：** 純玻璃偵測 MVP 不裝 MQTT 也能運作。需要 ESP32 水泵或遠端控制時才必須部署。

## 步驟 4：驗證雲端伺服器

```bash
# 健康檢查
curl https://sdprs.zeabur.app/api/health
# 應回傳 {"status": "healthy", "timestamp": "...", "service": "sdprs-central-server"}

# 儀表板登入
# 瀏覽器開啟 https://sdprs.zeabur.app/login
# 帳號: admin  密碼: <your-dashboard-password>
```

## 步驟 5：設定 Pi 邊緣節點連接雲端

新版 `setup_pi.sh` 直接支援雲端模式，**單一指令即完成所有設定**（git clone、venv、依賴、config.zeabur.yaml 寫入、systemd 啟用）。

```bash
# 在 Pi 上（首次部署）
ssh pi@sdprs-glass-01.local
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/Thomas-Tai/sdprs.git /opt/sdprs

# 一鍵雲端部署（替換成你的 Zeabur URL 和 EDGE_API_KEY）
cd /opt/sdprs/scripts && sudo chmod +x setup_pi.sh
sudo ./setup_pi.sh glass_node_01 \
    --mode cloud \
    --cloud-url https://sdprs.zeabur.app/api \
    --api-key <your-edge-api-key>
```

**有部署 Mosquitto 時，再加上 MQTT 參數：**

```bash
sudo ./setup_pi.sh glass_node_01 \
    --mode cloud \
    --cloud-url https://sdprs.zeabur.app/api \
    --api-key <your-edge-api-key> \
    --mqtt-broker <mosquitto-public-ip> \
    --mqtt-port <mosquitto-public-port> \
    --mqtt-username sdprs \
    --mqtt-password <your-mqtt-password>
```

**驗證：**

```bash
journalctl -u sdprs-edge-cloud -n 20 --no-pager
# 預期日誌：
#   Audio stream started
#   HTTP Request: POST https://sdprs.zeabur.app/api/edge/glass_node_01/snapshot "HTTP/1.1 204 No Content"
#   MQTT client: Connected to MQTT broker: <mosquitto-public-ip>:<port>   (有 Mosquitto 才會出現)
```

> **腳本自動完成：** 系統依賴 / venv / config.zeabur.yaml 改寫 / `usermod -aG video,audio sdprs` / `sdprs-edge-cloud.service` 啟用 + 開機自啟。
> 雲端模式跳過 SSH 隧道（`stream.cloud_mode: true`），不需 autossh。

**Pi 硬體相關預設值（由腳本寫入 `config.zeabur.yaml`，特殊型號才需手動覆寫）：**

| 項目              | 預設值                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------- |
| USB 攝影機        | `camera.source: 0`（Logitech C920 在 PyAudio index 0）                                |
| 音訊 sample rate  | `audio.sample_rate: 16000`（C920 只支援 16000 / 32000 Hz，**不支援 44100**）   |
| 音訊 device index | `audio.device_index: 0`（PyAudio index，非 ALSA card 號碼）                           |

## 步驟 6：設定自動備份（可選）

見下方 [Zeabur 雲端備份管理](#zeabur-雲端備份管理)。

## Zeabur 平台特性 / 已知問題（實測 2026-07-15）

以下為在 Zeabur 上部署 SDPRS 時實際遇到的平台層級問題與繞道方案：

### 1. Ghost 服務 / Pod（服務或 Pod 卡在刪除狀態）

Zeabur 偶發性 bug：透過 Dashboard 或 CLI 刪除服務後，後端記錄仍存在，容器可能仍在跑（吃記憶體、佔用 auto-generated env vars）。刪除 API 回應 `service deletion already scheduled or service not found`，但實際沒生效。

**症狀：**
- 已刪除的服務仍在資源用量圖裡顯示 memory footprint
- 已刪除的服務其 `<SVC>_HOST` 等 auto-generated env vars 仍出現在同專案其他服務的變數列表裡
- Redeploy 後舊 Pod 沒被終止，新舊 Pod 並存

**繞道：**
- **短期：** 忽略 stale auto-generated env vars（本 repo 程式碼不會讀 `EMQX_HOST` 等未使用變數，harmless）。
- **中期：** 到 [Zeabur Forum](https://zeabur.com/forum) 開 ticket 附上 project + service ID，請團隊手動清理 backend 狀態。
- **長期防禦（已內建於本 repo）：** `central_server/services/mqtt_service.py` 用 `client_id=f"central_server_{hostname}_{pid}"` 而非固定字串，讓多個 sdprs-server pod 可共存在 broker 上不互踢（若真的 ghost pod 存在，會產生 N× 訊息處理，MVP 規模可接受）。

### 2. Resource Limit UI 只暴露 limit，不暴露 request

Zeabur Dashboard 的「Resource Limit」欄位對應 K8s 的 `limit`（硬上限），沒有暴露 `request`（保證下限，會決定 QoS tier）。這代表所有服務都是 `request=0` → BestEffort QoS → **在節點記憶體壓力下最先被 evict**。

**影響：** 大型服務（如 EMQX 5.8 空載 200 MB）容易在跨服務記憶體壓力下被 kill。本 repo 選 Mosquitto（15 MB）而非 EMQX 就是為此。

**繞道：** 若需要「即使發生記憶體壓力也不能被 kill」，需向 Zeabur 洽詢 dedicated node 方案。

### 3. Mosquitto 2.1+ 密碼檔權限嚴格

Mosquitto 2.1.2 拒絕載入 group-/world-readable 的 passwd 檔（日誌：`Warning: File has world readable permissions` 後直接 terminating）。本 repo `deploy/mosquitto/entrypoint.sh` 已內建修復：`umask 077` + 明確 `chown mosquitto:mosquitto`。**直接用 `deploy/mosquitto/` 部署即可，勿手工改 entrypoint。**

### 4. Zeabur TCP 端口在重新部署後可能變更

Mosquitto public TCP port（如 `:30471`）在服務重建後可能變成不同號碼。Pi/ESP32 若寫死舊 port 就會斷線。

**繞道：** 在 Zeabur Networking 頁面把該端口設為 **Reserved Port** 鎖定；或用 CI 監控 port 變化並推送更新到邊緣節點。

## Zeabur 常見部署問題速查

| 症狀                                                                                                          | 原因                                            | 解法                                                                               |
| ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| Build 日誌空白 / Pod 拉取 alpine                                                                              | `zbpack.json` 缺少 `build_type: dockerfile` | 確認 `zbpack.json` 只有 `{"build_type": "dockerfile"}`                         |
| `pydantic ValidationError: 4 errors`                                                                        | 4 個必填環境變數未設定                          | 在 Variables 添加 DASHBOARD_USER/PASS、EDGE_API_KEY、SECRET_KEY                    |
| `EXPOSE $PORT` build 失敗 | Dockerfile EXPOSE 不支援變數 | 使用 `EXPOSE 8080`，CMD 中用 `${PORT:-8080}` |                                                 |                                                                                    |
| 啟動後 502 但 logs 正常                                                                                       | 舊 Pod 仍在路由                                 | 等待舊 Pod 終止，或在 Zeabur 面板強制重新部署                                      |
| MQTT 連線 timeout（Pi 端）                                                                                    | `mqtt_broker` 用了 Zeabur 內部名              | Pi 需使用 Mosquitto 的 **Public** TCP 轉發地址                                     |
| Central Server MQTT 連不上                                                                                    | `MQTT_BROKER=mosquitto` 但實際 hostname 不同  | 用 Networking 頁面的 **Private** hostname（如 `mosquitto.zeabur.internal`） |
| 監控牆不自動刷新                                                                                              | WebSocket 用 `ws://` 但站點是 HTTPS           | 已修正為自動偵測 `wss://`；確認使用最新版代碼                                    |
| 監控牆/節點數為 0                                                                                             | 無 MQTT 心跳                                    | 正常（MVP 方案 A）；監控牆透過快照數據顯示節點                                     |
| `pydantic ValidationError` 或 DB 連線失敗，log 顯示 `${POSTGRESQL_PORT}` 未解析 | `DATABASE_URL` 用了 `${POSTGRESQL_*}`（多個 L） | Zeabur Marketplace image 暴露的是 `POSTGRES_*`（無 L）；改用 `${POSTGRES_HOST}` 等 |
| Mosquitto crash loop：`password-file: Error: Unable to open pwfile`                                          | 密碼檔權限太寬（world-readable）或未 chown 給 mosquitto user | 直接用 `deploy/mosquitto/` 的 entrypoint.sh（已內建 `umask 077` + chown）；不要自己改 |
| Mosquitto crash loop：`MQTT_USERNAME env var is required`                                                    | 忘了在 mosquitto 服務設 `MQTT_USERNAME`/`MQTT_PASSWORD` env vars | 在 **mosquitto 服務**（不是 sdprs-server）的 Variables 加上這兩個變數                |
| sdprs-server 日誌不斷 `MQTT ... rc=7` + Mosquitto 日誌 `session taken over` 1 Hz 循環 | 兩個 sdprs-server pod 都用同一 `client_id` 互踢對方 | 本 repo `cc3b860` 起已用 unique client_id；若仍發生 → Zeabur ghost pod，見「平台已知問題 §1」 |

## 雲端部署驗證清單

- [ ] `https://<your-app>.zeabur.app/api/health` 回傳 `{"status": "healthy", ...}`
- [ ] 儀表板（`/login`）可以成功登入
- [ ] sdprs-server logs 出現 `Connected to MQTT broker successfully`（部署 Mosquitto 後）
- [ ] Mosquitto logs 顯示 `Opening ipv4 listen socket on port 1883` + `mosquitto version X.Y.Z running`（無 crash loop）
- [ ] **端到端 MQTT 煙霧測試**：從外部網路推一個假 heartbeat，驗證 server 收到並處理：

  ```bash
  # 需先安裝 mosquitto-clients（macOS: brew install mosquitto，Ubuntu: apt install mosquitto-clients）
  mosquitto_pub -h <public-ip> -p <public-port> -u sdprs -P '<mqtt-password>' \
      -t sdprs/edge/smoke_test_node/heartbeat \
      -m '{"node_id":"smoke_test_node","timestamp":"2026-01-01T00:00:00Z","online":true}'
  ```

  在 Zeabur → sdprs-server → Logs，等 ~90 秒後應看到：
  `Node smoke_test_node marked OFFLINE (no heartbeat for 92s)` → 表示 server 有收到、有寫入、有跑 offline detection timer。

- [ ] Pi 端 `journalctl` 看到 snapshot POST `204 No Content`（部署攝影機 Pi 後）
- [ ] 監控牆（`/monitor`）顯示 Pi 攝影機即時快照
- [ ] 主控台（`/`）顯示節點: 1/1
- [ ] Pi 重啟後服務自動啟動：`systemctl is-enabled sdprs-edge-cloud` 顯示 `enabled`

---

## 雲端環境注意事項（補充）

以下為雲端部署時仍然適用的操作要點，補充上文未涵蓋之處：

- **資料持久化：** 雲端容器重啟後 SQLite 不會持久化（Zeabur 免費方案無 Volume；持久化儲存為付費方案）。正式雲端部署建議部署 PostgreSQL（見步驟 2），SQLite 僅作本地／開發回退。
- **MQTT TLS：** 中央伺服器另有 `MQTT_USE_TLS` 環境變數（預設 `False`）。Zeabur TCP 端口轉發為純 TCP，不含 TLS 包裝。Mosquitto 支援原生 TLS 但需額外設定 listener 8883 + 憑證掛載，MVP 暫不啟用（走 Zeabur 內部私有網段時本身即為隔離）。
- **MQTT 端口穩定性：** Mosquitto 對外 TCP 端口（如 `:34567`）可能在重新部署後變更，屆時需更新 Pi `config.yaml` 的 MQTT 端口。可在 Zeabur Networking 頁面「Reserved Port」鎖定端口以避免此問題。

> 歷史遷移報告已封存於 [../archive/zeabur_migration_report.md](../archive/zeabur_migration_report.md)。

---

## Zeabur 雲端備份管理

> 適用雲端部署模式。備份由 Pi 主動發起，繞過 AP 隔離限制。

### 前提條件：設定 Pi SSH 金鑰

```bash
# 在 Pi 上執行（以 sdprs 用戶）
ssh-keygen -t ed25519 -f /home/sdprs/.ssh/zeabur_backup -N ""

# 查看公鑰
cat /home/sdprs/.ssh/zeabur_backup.pub
# 複製輸出，登入 Zeabur 伺服器將公鑰貼入 ~/.ssh/authorized_keys
```

### 修改備份腳本

```bash
sudo nano /opt/sdprs/scripts/backup_from_zeabur.sh
```

填入以下兩個必填變數：

```bash
ZEABUR_HOST="xxx.xxx.xxx.xxx"   # Zeabur 伺服器 IP
DATABASE_URL="postgresql://..."  # Zeabur PostgreSQL 連線串
```

### 設定 Cron 自動備份

```bash
# 以 sdprs 用戶身份編輯 crontab
sudo crontab -u sdprs -e

# 加入（每天凌晨 3 點執行）
0 3 * * * /opt/sdprs/scripts/backup_from_zeabur.sh >> /var/log/sdprs_backup.log 2>&1
```

### 備份內容

| 類型                        | 位置                           | 保留  |
| --------------------------- | ------------------------------ | ----- |
| PostgreSQL 即時檔 (.sql.gz) | `/opt/backup/sdprs/db/`      | 30 天 |
| MP4 影片 (rsync 增量)       | `/opt/backup/sdprs/storage/` | 全部  |

### 手動還原

```bash
# 還原最新備份
/opt/sdprs/scripts/restore_to_zeabur.sh

# 還原指定日期的備份
/opt/sdprs/scripts/restore_to_zeabur.sh sdprs_20260327_030000.sql.gz
```

### 驗證備份

```bash
# 手動執行一次，確認腳本正常
sudo -u sdprs /opt/sdprs/scripts/backup_from_zeabur.sh

# 檢查備份檔案
ls -lh /opt/backup/sdprs/db/
du -sh /opt/backup/sdprs/
```

---

## 下一步

Pi 邊緣節點已於步驟 5 一併完成。若還要部署 ESP32 水泵節點，見 [edge-pump-esp32.md](edge-pump-esp32.md)。
全部就緒後，執行 [verification.md](verification.md) 完整驗證清單（含 Zeabur 雲端部署驗證段落）。
