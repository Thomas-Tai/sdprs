# 中央伺服器部署（Docker — 備援／開發方案）

> 在筆電或非 Raspberry Pi 的 Linux/Mac/Windows 電腦上以 Docker Compose 啟動整套中央伺服器，適合開發、測試與備援。面向具基本命令列操作經驗者。

← 返回[部署指南](README.md)　·　硬體與網路先看 [../hardware-network.md](../hardware-network.md)

---

## 前提條件

- 安裝 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 或 Docker Engine
- 安裝 Docker Compose（Docker Desktop 已內建）

## 步驟 1：準備環境變數

```bash
cd sdprs
cp .env.example .env
```

編輯 .env，修改三個密碼（同 [Pi 5 部署步驟 4](pi5-server.md#步驟-4修改密碼非常重要)）。

> **注意：** `.env` 檔案必須在 `sdprs/` 專案根目錄下，Docker Compose 會自動引用此路徑。

## 步驟 2：啟動所有容器

```bash
cd deploy
docker compose up -d
```

> **注意：** 必須在 `deploy/` 目錄下執行 `docker compose` 命令。建置上下文為上層 `sdprs/` 目錄。

這會啟動三個容器：

| 容器      | 服務         | 端口         |
| --------- | ------------ | ------------ |
| sdprs-app | FastAPI 應用 | 8000（內部） |
| mosquitto | MQTT Broker  | 1883         |
| nginx     | 反向代理     | 80（對外）   |

## 步驟 3：驗證

```bash
# 查看容器狀態
docker compose ps
# 所有容器應顯示 Up (healthy)
# 健康檢查會定期存取 /api/health 端點確認服務正常

# 查看日誌
docker compose logs -f sdprs-app
```

瀏覽器打開 http://localhost 即可存取儀表板。

## Docker 常用命令

```bash
docker compose down          # 停止所有容器
docker compose restart       # 重啟所有容器
docker compose up -d --build # 重新建置並啟動
docker compose logs -f       # 查看所有容器日誌
```

---

## 下一步

1. 部署現場的邊緣節點：[edge-glass.md](edge-glass.md)（玻璃偵測）、[edge-pump-esp32.md](edge-pump-esp32.md)（水泵）。
2. 全部就緒後，執行 [verification.md](verification.md) 完整驗證清單。
