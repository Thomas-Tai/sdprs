# SDPRS - 智能防災監測與自動響應系統

**Smart Disaster Prevention Response System**

[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-blue.svg)](https://fastapi.tiangolo.com/)

> **適用對象**：本專案為完整的颱風防災監測系統。部署指南以步驟化撰寫，即使非技術人員（如保安人員）也能按步驟完成；每一步都附有**驗證方法**，請確認每步成功後再進入下一步。

---

## 一、系統簡介

SDPRS 是一套基於**邊緣計算**的智能防災監測系統，專為颱風季節設計：

| 功能                   | 說明                                                                      |
| ---------------------- | ------------------------------------------------------------------------- |
| **玻璃破裂偵測** | 結合視覺（OpenCV 10步管線）與音訊（FFT 6步管線），融合觸發（2秒關聯窗口） |
| **即時警報推播** | Alert-First 模式 -- JSON 警報 <1秒送達儀表板，MP4 影片隨後上傳            |
| **HLS 即時串流** | 透過 mediamtx + SSH 反向隧道，瀏覽器直接觀看攝像頭畫面                    |
| **水泵自動控制** | 滯後控制（水位 >=80% 啟動 / <=20% 關閉），防止頻繁切換                    |
| **連續監控牆**   | 每秒上傳 480p 快照，儀表板即時顯示所有攝像頭                              |
| **離線自治**     | 邊緣節點網路中斷時獨立偵測、錄影，網路恢復後自動上傳                      |

### 事件狀態流程

```
邊緣節點:  QUEUED --> JSON_SENT --> UPLOADED
伺服器:   PENDING_VIDEO --> PENDING --> RESOLVED (由保安標記)
```

---

## 二、系統架構

以下為主要系統架構（區域網路部署）。完整架構說明——含雲端 AP 隔離部署架構與完整目錄結構——請見 [docs/architecture.md](docs/architecture.md)。

```
+--------------------------------------------------------------+
|                    中央伺服器 (Pi 5 / Docker)                  |
|                                                              |
|  +-----------+  +-----------+  +-----------+                 |
|  |  FastAPI   |  | Mosquitto |  |   Nginx   |                |
|  |  (:8000)   |  |  (:1883)  |  |   (:80)   |                |
|  +-----+-----+  +-----+-----+  +-----------+                |
|        |               |                                      |
|  +-----v-----+  +-----v------+                               |
|  |  SQLite   |  |  WebSocket |--> 儀表板 (瀏覽器)             |
|  | WAL mode  |  |  Manager   |                               |
|  +-----------+  +------------+                               |
+--------------------------------------------------------------+
        | MQTT               ^ REST API + SSH隧道
        v                    |
+----------------+    +----------------+
| 邊緣節點 (Pi 4) |    | 水泵節點 (ESP32)|
| - 攝像頭+麥克風 |    | - 水位感測器    |
| - OpenCV 視覺  |    | - 繼電器控制    |
| - FFT 音訊     |    | - MQTT 回報    |
| - mediamtx HLS |    | - MicroPython  |
| - SSH 反向隧道  |    | - 離線自治      |
+----------------+    +----------------+
```

### 專案結構速覽

僅列出頂層目錄；完整目錄結構請見 [docs/architecture.md](docs/architecture.md)。

```
sdprs/
|-- central_server/   # 中央伺服器應用（FastAPI + SQLite/PostgreSQL + MQTT + WebSocket + Jinja2 儀表板）
|-- edge_glass/       # 玻璃偵測邊緣節點（Pi 4：OpenCV 視覺 + FFT 音訊 + 環形緩衝 + HLS 串流）
|-- edge_pump/        # 水泵控制節點（ESP32 MicroPython：滯後控制 + 感測器 HAL + MQTT）
|-- shared/           # 共用模組（MQTT 主題與 QoS 常數）
|-- deploy/           # 部署配置（Dockerfile、docker-compose、nginx、mosquitto）
|-- scripts/          # 佈建腳本（一鍵佈建、ESP32 燒錄、Zeabur 備份還原）
+-- docs/             # 完整文件（部署、運維、參考、架構）
```

---

## 快速開始

- **想部署整套系統？** 從 [docs/deployment/README.md](docs/deployment/README.md) 開始，它會依序引導你完成中央伺服器、邊緣節點與水泵節點的部署。
- **需要採購硬體或查看接線圖？** [docs/hardware-network.md](docs/hardware-network.md) 提供完整硬體清單、接線圖與網路規劃。
- **選擇中央伺服器部署方案？** 支援 Pi 5（區域網路）、Docker 備用方案與 Zeabur 雲端方案；各方案指南見 [docs/deployment/README.md](docs/deployment/README.md)。
- **想先理解系統如何運作？** 閱讀上方架構圖與 [docs/architecture.md](docs/architecture.md)。
- **完整文件導覽？** 見下方 [文件地圖](#文件地圖) 或 [docs/README.md](docs/README.md)。

---

## 專案狀態

六大主題程式碼重構已於 2026-07-13 完成，水泵節點已合併、V2 SPA 儀表板已上線，中央伺服器已部署於 Pi 5（區域網路）；共 264 項自動化測試全數通過（edge_pump 48 / central_server 92 / edge_glass 124）。

- 專案狀態總覽：[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)
- 工程進度（權威文件）：[docs/superpowers/PROGRESS.md](docs/superpowers/PROGRESS.md)

---

## 文件地圖

依對象快速定位文件（完整索引見 [docs/README.md](docs/README.md)）：

| 對象         | 建議閱讀                                                                                                                                                                        | 內容                               |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| **新使用者** | [docs/README.md](docs/README.md) · [docs/architecture.md](docs/architecture.md)                                                                                                | 文件總覽與系統架構                 |
| **部署者**   | [docs/deployment/README.md](docs/deployment/README.md) · [docs/hardware-network.md](docs/hardware-network.md)                                                                  | 部署流程、硬體清單與接線、網路規劃 |
| **運維人員** | [docs/operations/dashboard-guide.md](docs/operations/dashboard-guide.md) · [runbook.md](docs/operations/runbook.md) · [troubleshooting.md](docs/operations/troubleshooting.md) | 儀表板操作、日常運維、故障排除     |
| **開發者**   | [docs/reference/configuration.md](docs/reference/configuration.md) · [api.md](docs/reference/api.md) · [mqtt-topics.md](docs/reference/mqtt-topics.md)                          | 配置、API、MQTT 主題參考           |
| **管理層**   | [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) · [docs/superpowers/PROGRESS.md](docs/superpowers/PROGRESS.md)                                                                | 專案狀態與工程進度                 |

---

## Webcam Client (Windows)

讓任一 Windows 電腦透過 USB Webcam 推送畫面到 Dashboard。

### 使用方式

1. Dashboard → 系統狀態 → 「新增 Webcam Client」→ 複製 API Key
2. 在目標電腦運行 `SDPRS_Webcam.exe`
3. 填入 Server URL + API Key → 選擇攝影機 → 開始
4. 程式最小化到 System Tray，自動推送 1Hz 快照
5. Dashboard 上點「即時觀看」可觸發 H.264 HLS 串流

### 開發

```bash
cd webcam_client
pip install -r requirements.txt
python -m webcam_client.main
```

### 打包

```bash
cd webcam_client
pyinstaller build.spec
# 產物: dist/SDPRS_Webcam.exe
```

---

## 授權

MIT License

## 貢獻

歡迎提交 Issue 和 Pull Request。
