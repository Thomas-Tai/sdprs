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
