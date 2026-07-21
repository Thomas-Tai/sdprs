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

讓任一 Windows 電腦透過 USB Webcam 推送畫面到 Dashboard —— 每秒 JPEG 快照，並可依需求開啟 H.264 / HLS 即時串流。

### 使用方式

1. Dashboard → 系統狀態 →「新增 Webcam Client」→ 複製一次性顯示的 API Key
2. 在目標電腦運行 `SDPRS_Webcam.exe`（或 `python -m webcam_client.main`）
3. 首次啟動精靈：掃描攝影機（含預覽縮圖與逐鏡命名）→ 填入 Server URL + API Key → 開始
4. 程式最小化到 System Tray，自動推送 1Hz 快照到監控牆
5. 節點卡點「▶ 即時」觸發即時串流：等實際影格就緒才播放；觀看期間每 30s 自動續租（90s lease），關閉分頁後約 90s 內自動停止串流、回收上行頻寬

### 管理

於 Dashboard → 系統狀態 的 Webcam 節點列：

- **暫停推送 / 恢復推送**（Tray 選單）—— 真正暫停/恢復快照上傳，非僅圖示變化
- **撤銷 Key** —— 輪換 API Key，舊 Key 立即失效（外洩或換機時使用）
- **刪除** —— 除役整台 Client（含其所有攝影機與憑證）；確認對話框會顯示 Client 名稱

### 安全

- API Key 以 Windows DPAPI 加密後才落地：`config.json` 只存 `api_key_encrypted`，永不存明文；換帳號解密失敗即視為未設定並重跑精靈，絕不退回明文
- 每個 Webcam Key 僅於 `/api/webcam/*` 有效，用於 edge 端點會回 401

### 現場驗證

自動化測試全數 mock 攝影機 / ffmpeg / 網路。實機端到端須先跑一次
[`docs/webcam-client-bench-test-checklist.md`](docs/webcam-client-bench-test-checklist.md)（11 節，逐步標註所驗證的不變量），方可視為現場驗證通過。

### 開發

```bash
cd webcam_client
pip install -r requirements.txt
python -m webcam_client.main
```

### 打包（單一 exe，內含即時串流）

在**已安裝 ffmpeg 且在 PATH** 的機器上打包，`build.spec` 會自動將 ffmpeg 一併打包進 exe，
產物為完全獨立的單一檔案：複製到任何 Windows x64 電腦即可運行（含即時串流），
目標電腦**無需安裝 Python 或 ffmpeg**。

```bash
cd webcam_client
pip install -r requirements.txt
pyinstaller build.spec
# 產物: dist/SDPRS_Webcam.exe（單檔；建議使用 static ffmpeg 版本，單一自足的 ffmpeg.exe）
```

若打包機沒有 ffmpeg，仍會產生 exe，但目標電腦需自行在 PATH 安裝 ffmpeg 才能使用即時串流（1Hz 快照不受影響）。
exe 未經簽章且經 UPX 壓縮，首次執行時 SmartScreen / 防毒可能警告，選「仍要執行」即可。

---

## 授權

MIT License

## 貢獻

歡迎提交 Issue 和 Pull Request。
