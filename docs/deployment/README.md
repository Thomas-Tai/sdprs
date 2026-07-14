# 部署指南

> 依你的場地與硬體條件選擇部署方案。每個指南都可獨立照做，完成後統一以驗證清單確認。面向現場部署人員（含非技術人員）。

← 返回[文件索引](../README.md)

---

## 開始之前

**硬體與網路先看** [../hardware-network.md](../hardware-network.md) —— 硬體清單、接線圖與網路規劃（AP 隔離、IP 分配、端口）都在該文件。

---

## 中央伺服器：選一個方案

| 場景 | 方案 | 指南 |
| ---- | ---- | ---- |
| 場地允許有線／同網段，用 Raspberry Pi 5 | **主要方案** | [pi5-server.md](pi5-server.md) |
| 筆電或非 Raspberry Pi 電腦（開發／測試） | 備援／開發 | [docker.md](docker.md) |
| WiFi AP 隔離、不能拉線、無法改路由器 | 雲端方案 | [zeabur-cloud.md](zeabur-cloud.md) |

## 邊緣節點

中央伺服器就緒後，部署現場的偵測與控制節點：

| 節點 | 說明 | 指南 |
| ---- | ---- | ---- |
| 玻璃偵測邊緣節點（Pi） | 每個攝像頭一台，視覺＋音訊融合偵測 | [edge-glass.md](edge-glass.md) |
| 水泵控制節點（ESP32） | 水位感測＋滯後控制，MicroPython | [edge-pump-esp32.md](edge-pump-esp32.md) |

## 完成後

所有節點部署完畢，執行 [verification.md](verification.md) 逐項確認整套系統運作正常。

---

## 佈建腳本一覽（`scripts/`）

以下為 `scripts/` 目錄現有的腳本；各部署指南中會依需要引用。

| 腳本                          | 用途                                                                 |
| ----------------------------- | -------------------------------------------------------------------- |
| `setup_server.sh`             | 中央伺服器一鍵佈建（Pi 5 / 一般 Linux），見 [pi5-server.md](pi5-server.md) |
| `setup_pi.sh`                 | 邊緣節點一鍵佈建（LAN／雲端模式共用），見 [edge-glass.md](edge-glass.md) |
| `setup_esp32.sh`              | ESP32 韌體燒錄 + `edge_pump/` 上傳，見 [edge-pump-esp32.md](edge-pump-esp32.md) |
| `deploy_sync.sh`              | rsync 增量部署，`SDPRS_*` 環境變數見 [../reference/configuration.md](../reference/configuration.md) |
| `gen_qrcode.sh`               | 生成含 WiFi 或伺服器 IP 的 QR Code（現場張貼／掃碼加入用）           |
| `backup_from_zeabur.sh`       | 從 Zeabur 拉取 PostgreSQL + 影片備份到 Pi（每日 cron）               |
| `restore_to_zeabur.sh`        | 反向：從 Pi 本地備份還原到 Zeabur PostgreSQL                         |
