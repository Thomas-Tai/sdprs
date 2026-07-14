# 中央伺服器部署（Pi 5 — 主要方案）

> 本指南涵蓋 Raspberry Pi OS 燒錄與中央伺服器在 Pi 5 上的一鍵部署，適用於場地允許有線／同網段連接的主要情境。面向現場部署人員（含非技術人員）。

← 返回[部署指南](README.md)　·　硬體與網路先看 [../hardware-network.md](../hardware-network.md)

---

## 部署前準備：燒錄 Pi OS

> **此步驟適用於所有 Raspberry Pi（中央伺服器和邊緣節點都需要）**

### 所需工具

- 一台電腦（Windows/Mac/Linux）
- microSD 卡讀卡器
- microSD 卡（32GB）

### 步驟

1. **下載 Raspberry Pi Imager**

   - 到 https://www.raspberrypi.com/software/ 下載並安裝
2. **燒錄系統**

   - 打開 Raspberry Pi Imager
   - 選擇設備：Raspberry Pi 5（伺服器）或 Raspberry Pi 4（邊緣節點）
   - 選擇系統：**Raspberry Pi OS Lite (64-bit)** -- **必須選 Lite 版本（無桌面）**
   - 選擇 SD 卡
   - **點擊齒輪圖示設定：**
     - 設定主機名稱：伺服器填 `sdprs-server`，邊緣節點填 `sdprs-glass-01`
     - 啟用 SSH：選擇「使用密碼認證」
     - 設定用戶名：`pi`，密碼：你的密碼
     - 設定 WiFi（可選，建議用有線）
     - 設定時區：Asia/Macau
   - 點擊「燒錄」
3. **插入 SD 卡並開機**

   - 將燒錄好的 SD 卡插入 Pi
   - 接上網路線和電源
   - 等待約 1 分鐘開機完成
4. **SSH 連線到 Pi**

   ```bash
   # 從你的電腦連線（Windows 可用 PowerShell 或 PuTTY）
   ssh pi@sdprs-server.local
   # 或用 IP
   ssh pi@192.168.1.100

   # 輸入你設定的密碼
   ```

   **如果找不到 Pi 的 IP：**

   - 檢查路由器管理頁面的 DHCP 客戶端列表
   - 或將 Pi 接上螢幕和鍵盤，登入後執行 `hostname -I`

---

## 部署中央伺服器（Pi 5）

> **預計時間：15-20 分鐘**（取決於網速）

### 步驟 1：SSH 連線到 Pi 5

```bash
ssh pi@sdprs-server.local
# 或
ssh pi@192.168.1.100
```

### 步驟 2：下載專案程式碼

**方法 A：使用 Git（推薦）**

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone <你的-repo-url> /opt/sdprs
```

**方法 B：使用 USB 隨身碟**

```bash
# 1. 在你的電腦上將 sdprs 資料夾複製到 USB
# 2. 插入 Pi 的 USB 口
# 3. 在 Pi 上執行：
sudo mkdir -p /opt/sdprs
sudo mount /dev/sda1 /mnt
sudo cp -r /mnt/sdprs/* /opt/sdprs/
sudo umount /mnt
```

**方法 C：使用 rsync 一鍵部署（推薦，適合開發階段反覆更新）**

```bash
# 在你的開發電腦上執行（需要 SSH 連線到 Pi）
cd sdprs/scripts
chmod +x deploy_sync.sh

# 首次初始化（自動建立 venv、裝依賴、設定 systemd 服務）
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh init-server

# 之後每次代碼更新只需：
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh server
```

> **提示：** 使用此方法可跳過步驟 3-6，`init-server` 會自動完成所有設定。
> 詳見 [日常運維 › 代碼同步部署](../operations/runbook.md)。

**方法 A、B 或 C 完成後，驗證：**

```bash
ls /opt/sdprs/
# 應該看到: central_server  deploy  edge_glass  edge_pump  scripts  shared  README.md
```

### 步驟 3：執行一鍵佈建腳本

```bash
cd /opt/sdprs/scripts
sudo chmod +x setup_server.sh
sudo ./setup_server.sh
```

**可選 -- 設定固定 IP：**

```bash
sudo ./setup_server.sh --static-ip 192.168.1.100
```

佈建腳本會**自動**完成以下工作：

- 設定 hostname 為 sdprs-server
- 設定時區為 Asia/Macau
- 安裝 avahi-daemon（mDNS，讓你可以用 sdprs.local 存取）
- 安裝 Python 3、Mosquitto MQTT、Nginx、SQLite
- 建立 sdprs 系統用戶
- 建立 Python 虛擬環境並安裝所有依賴
- 建立 .env 環境變數檔案
- 配置 Mosquitto MQTT Broker
- 配置 Nginx 反向代理
- 安裝並啟動 systemd 服務

### 步驟 4：修改密碼（非常重要！）

```bash
sudo nano /opt/sdprs/.env
```

**必須修改的三個密碼：**

```bash
DASHBOARD_PASS=改成你的強密碼          # 儀表板登入密碼
EDGE_API_KEY=改成隨機字串              # 邊緣節點的 API 金鑰
SECRET_KEY=改成另一個隨機字串           # Session 加密金鑰
```

> **提示：** 可以用這個命令生成隨機密碼：
>
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(24))"
> ```

按 Ctrl+O 儲存，Ctrl+X 退出。

### 步驟 5：重啟服務使密碼生效

```bash
sudo systemctl restart sdprs-server
```

### 步驟 6：驗證部署成功

```bash
# 檢查三個核心服務狀態
sudo systemctl status sdprs-server   # 應顯示 active (running)
sudo systemctl status mosquitto      # 應顯示 active (running)
sudo systemctl status nginx          # 應顯示 active (running)
```

**如果任何服務不是 active (running)，查看錯誤日誌：**

```bash
journalctl -u sdprs-server --since "5 minutes ago" --no-pager
```

### 步驟 7：打開儀表板

在同一網路的任何電腦或手機瀏覽器中打開：

```
http://sdprs.local
```

或

```
http://192.168.1.100
```

使用以下帳號登入：

- **帳號：** admin（或你在 .env 中設定的 DASHBOARD_USER）
- **密碼：** 你在步驟 4 設定的 DASHBOARD_PASS

> **看到儀表板頁面 = 中央伺服器部署成功！**

---

## 下一步

1. 部署現場的邊緣節點：[edge-glass.md](edge-glass.md)（玻璃偵測）、[edge-pump-esp32.md](edge-pump-esp32.md)（水泵）。
2. 全部就緒後，執行 [verification.md](verification.md) 完整驗證清單。
