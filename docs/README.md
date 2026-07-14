# SDPRS 文件中心

本目錄為 **SDPRS 智能防災監測與自動響應系統** 的完整文件。以下依對象提供閱讀導引，並附每份文件的一句話說明；建議先找到符合自己角色的段落再逐一深入。專案總覽與架構圖請見 repo 根目錄的 [README.md](../README.md)。

---

## 依對象閱讀導引

### 新使用者（先理解系統）

- [architecture.md](architecture.md) — 系統架構總覽，含區域網路與雲端 AP 隔離部署架構、完整目錄結構。
- [PROJECT_STATUS.md](PROJECT_STATUS.md) — 目前的專案進度、已完成範圍與測試狀態。

### 部署者（動手安裝）

- [deployment/README.md](deployment/README.md) — 部署總覽與建議順序，所有部署由此開始。
- [hardware-network.md](hardware-network.md) — 硬體採購清單、接線圖與網路規劃。
- [deployment/pi5-server.md](deployment/pi5-server.md) — 中央伺服器部署於 Raspberry Pi 5（區域網路方案）。
- [deployment/docker.md](deployment/docker.md) — 中央伺服器 Docker 備用方案（筆電或任何 Docker 平台）。
- [deployment/zeabur-cloud.md](deployment/zeabur-cloud.md) — 中央伺服器 Zeabur 雲端方案（AP 隔離場地適用）。
- [deployment/edge-glass.md](deployment/edge-glass.md) — 玻璃偵測邊緣節點（Pi 4）部署。
- [deployment/edge-pump-esp32.md](deployment/edge-pump-esp32.md) — 水泵控制節點（ESP32 / MicroPython）部署。
- [deployment/verification.md](deployment/verification.md) — 部署後完整驗證清單，逐項確認系統運作正常。

### 運維人員（日常操作）

- [operations/dashboard-guide.md](operations/dashboard-guide.md) — 儀表板使用說明（警報處理、監控牆、串流）。
- [operations/runbook.md](operations/runbook.md) — 日常運維作業（備份、健康檢查、例行維護）。
- [operations/troubleshooting.md](operations/troubleshooting.md) — 常見故障排除與診斷步驟。

### 開發者（配置與介面）

- [reference/configuration.md](reference/configuration.md) — 環境變數與各節點配置參考。
- [reference/api.md](reference/api.md) — 中央伺服器 REST API 參考。
- [reference/mqtt-topics.md](reference/mqtt-topics.md) — MQTT 主題與 payload 參考。
- [superpowers/PROGRESS.md](superpowers/PROGRESS.md) — 工程審計與逐階段實作紀錄（權威進度文件）。

### 管理層（狀態與決策）

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — 專案狀態與里程碑總覽。
- [superpowers/PROGRESS.md](superpowers/PROGRESS.md) — 工程進度權威文件。

---

## 其他資源

- **工程審計與規格工作流**：[superpowers/](superpowers/) — 記錄程式碼審計、規格（SDD）與逐階段實作歷程；其中 [superpowers/PROGRESS.md](superpowers/PROGRESS.md) 為**權威進度文件**。
- **MQTT 安全設定**：[../deploy/MQTT_SECURITY.md](../deploy/MQTT_SECURITY.md) — MQTT broker 認證與安全強化設定。
- **歷史文件**：[archive/](archive/) — 已封存的歷史文件，例如 [archive/zeabur_migration_report.md](archive/zeabur_migration_report.md)（雲端遷移可行性分析報告）。

---

## 上游需求來源

本專案的需求與技術決策原始文件——`requirements.md`（v3.3）、`spec.md`、`tech_decisions.md`——位於專案工作區，**不在本 repo 內**。本文件樹著重於部署、運維與工程實作；如需追溯原始需求與決策脈絡，請至專案工作區查閱上述文件。
