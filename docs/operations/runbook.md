# 日常運維手冊

本文件涵蓋服務管理、日誌、備份、代碼更新部署、資料維護與安全建議，供運維與部署人員日常操作使用。遇到異常請參閱 [故障排除](troubleshooting.md)。

← 返回[文件索引](../README.md)

## 服務管理命令

```bash
# ===== 中央伺服器 =====
sudo systemctl start sdprs-server       # 啟動
sudo systemctl stop sdprs-server        # 停止
sudo systemctl restart sdprs-server     # 重啟
sudo systemctl status sdprs-server      # 查看狀態
journalctl -u sdprs-server -f           # 即時日誌
journalctl -u sdprs-server --since today # 今天的日誌

# ===== 邊緣節點 =====
sudo systemctl restart sdprs-edge
journalctl -u sdprs-edge -f

# ===== SSH 隧道（本地 LAN 模式）=====
sudo systemctl restart autossh-tunnel
journalctl -u autossh-tunnel -f

# ===== 邊緣節點 — 雲端模式 =====
sudo systemctl restart sdprs-edge-cloud
journalctl -u sdprs-edge-cloud -f

# ===== MQTT =====
sudo systemctl status mosquitto

# ===== Nginx =====
sudo nginx -t                           # 測試配置語法
sudo systemctl reload nginx             # 重載配置（不中斷連線）
```

## 日誌位置

| 日誌         | 查看方式                            |
| ------------ | ----------------------------------- |
| FastAPI 應用 | journalctl -u sdprs-server -f       |
| 邊緣節點     | journalctl -u sdprs-edge -f         |
| SSH 隧道     | journalctl -u autossh-tunnel -f     |
| Nginx 存取   | cat /var/log/nginx/sdprs-access.log |
| Nginx 錯誤   | cat /var/log/nginx/sdprs-error.log  |
| MQTT Broker  | journalctl -u mosquitto -f          |

## 備份

```bash
# 備份資料庫
sudo cp /opt/sdprs/data/sdprs.db /backup/sdprs-$(date +%Y%m%d).db

# 備份事件影片
sudo rsync -av /opt/sdprs/storage/events/ /backup/events/

# 備份配置
sudo cp /opt/sdprs/.env /backup/.env.bak
```

## 代碼更新部署

**方法 A：Git Pull（雲端模式 / 正式更新）**

Pi 只需能上網，不需和開發機在同一網路：

```bash
cd /opt/sdprs && sudo -u sdprs git pull
sudo systemctl restart sdprs-edge-cloud
```

**方法 B：Rsync（LAN 開發調試）**

使用 `deploy_sync.sh` 將本地修改增量同步到 Pi，自動重啟服務。適合同一 LAN 內快速迭代（含未 commit 的改動）。

### 首次部署（init）

```bash
cd sdprs/scripts
chmod +x deploy_sync.sh

# 首次初始化中央伺服器（建立 venv、裝依賴、設定 systemd）
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh init-server

# 首次初始化邊緣節點（用 IP 連線）
SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh init-glass 01
```

> **重要：** venv 必須在 Pi 上建立（ARM 架構），不能從開發機（x86）同步。
> `init-server` / `init-glass` 會自動處理 piwheels 連線問題、建立系統用戶、安裝 systemd 服務。
> **前提：** `init` 命令需要 SSH 金鑰認證（密碼認證會導致遠端腳本卡住），請先執行 `ssh-copy-id`。

初始化完成後需在邊緣節點上修改 `config.yaml`：

```bash
ssh pi@192.168.1.101 'sudo nano /opt/sdprs/edge_glass/config.yaml'
# 修改 server.api_url、server.api_key、server.mqtt_broker 指向中央伺服器
```

### 日常代碼更新

```bash
# 同步到中央伺服器 (Pi 5) — 自動更新依賴 + 重啟服務
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh server

# 同步到邊緣節點 01 (Pi 4/5) — 用 IP 連線
SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh glass 01

# 同步到所有節點（需配置 mDNS 或 /etc/hosts）
SDPRS_GLASS_NODES="01,02,03" ./deploy_sync.sh all

# 預覽模式（不實際執行，只顯示會同步的檔案）
./deploy_sync.sh --dry-run server
```

### SSH 免密碼登入（強烈建議）

腳本涉及多次 SSH 連線（檢查、rsync、pip、重啟），不配置金鑰需要反覆輸入密碼。

**WSL / Linux / Mac：**

```bash
# 1. 生成金鑰（如果還沒有）
ssh-keygen -t ed25519
# 按 Enter 使用預設路徑，密碼留空（或設定 passphrase）

# 2. 複製公鑰到 Pi
ssh-copy-id pi@192.168.1.100

# 3. 測試免密碼登入
ssh pi@192.168.1.100
# 應該直接登入，不要求密碼
```

**Windows PowerShell（無 WSL）：**

Windows 沒有 `ssh-copy-id` 命令，需手動操作：

```powershell
# 1. 生成金鑰（如果還沒有）
ssh-keygen -t ed25519
# 金鑰預設存放在 C:\Users\你的用戶名\.ssh\id_ed25519

# 2. 查看公鑰內容（複製輸出）
type $env:USERPROFILE\.ssh\id_ed25519.pub

# 3. 將公鑰傳送到 Pi（一行命令）
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@192.168.1.100 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
# 輸入一次密碼後，之後就不需要了

# 4. 測試免密碼登入
ssh pi@192.168.1.100
```

**Windows CMD：**

```cmd
# 1. 生成金鑰
ssh-keygen -t ed25519

# 2. 查看公鑰
type %USERPROFILE%\.ssh\id_ed25519.pub

# 3. 手動複製公鑰到 Pi
# 方法：將上面輸出的公鑰整行複製，然後 SSH 到 Pi 貼上
ssh pi@192.168.1.100
mkdir -p ~/.ssh
echo "ssh-ed25519 AAAA...你的公鑰..." >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
exit
```

> **提示：** 如果你同時有中央伺服器和邊緣節點，需要對每台 Pi 都執行一次公鑰複製。

> **安全提示：** 腳本會自動排除 `.env`、`data/`、`storage/`、`venv/` 等目錄，不會覆蓋目標機器上的密碼和資料。

## 自動資料清理

系統每天凌晨 3:00 自動清理超過 RETENTION_DAYS（預設 30 天）的：

- 資料庫中的舊事件記錄
- 對應的 MP4 影片檔案

## 資料庫維護

```bash
# 檢查 WAL 模式是否正常
sqlite3 /opt/sdprs/data/sdprs.db "PRAGMA journal_mode;"
# 應返回: wal

# 手動整理資料庫（可選，每月一次）
sqlite3 /opt/sdprs/data/sdprs.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

## 安全建議

1. **修改所有預設密碼** -- 佈建後**立即**修改 .env 中的三個密碼
2. **啟用 HTTPS** -- 生產環境在 Nginx 配置 SSL 憑證（Let's Encrypt）
3. **網路隔離** -- 將 IoT 設備放在獨立 VLAN，與辦公網路隔離
4. **定期輪換金鑰** -- 每季更換 SSH 金鑰和 API Key
5. **防火牆** -- 只開放端口 80 和 1883，隧道端口只綁定 127.0.0.1
6. **定期備份** -- 每週備份資料庫和影片到外部儲存
7. **系統更新** -- 每月執行 `sudo apt update && sudo apt upgrade`
8. **MQTT 認證** -- 生產環境啟用 Mosquitto 帳號密碼（見 `deploy/mosquitto.conf` 中的說明）
9. **上傳驗證** -- MP4 影片上傳限制 100MB 且僅允許 video/* MIME 類型；快照限制 5MB
10. **XSS 防護** -- 儀表板 JavaScript 使用 DOM API（textContent/createElement），不使用 innerHTML
11. **SSH 安全** -- 反向隧道使用 `StrictHostKeyChecking=accept-new`（首次連線自動接受，後續驗證）
12. **systemd 速率限制** -- 所有服務配置 `StartLimitIntervalSec` 和 `StartLimitBurst`，防止崩潰迴圈
13. **佔位密碼偵測** -- 伺服器啟動時自動檢查 .env 中是否仍使用預設佔位密碼，並發出警告
14. **Zeabur 環境變數安全** -- 所有密鑰透過 Zeabur Variables 設定，禁止將 `changeme` 密鑰上傳至 GitHub
15. **備份可用性驗證** -- 定期執行 `restore_to_zeabur.sh` 驗證備份檔案可屬恢復
16. **Mosquitto 安全** — 雲端 Mosquitto 建議啟用認證，禁用匿名登入（在 Zeabur `mosquitto` 服務的 Variables 設定 `MQTT_USERNAME`/`MQTT_PASSWORD`，容器 `entrypoint.sh` 會用 `mosquitto_passwd -U` 雜湊寫入 passwd 檔；預設已內建於 `deploy/mosquitto/`）

> MQTT broker 加固步驟詳見 [MQTT_SECURITY](../../deploy/MQTT_SECURITY.md)。
