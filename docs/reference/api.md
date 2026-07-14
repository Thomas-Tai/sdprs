# API 參考

本文件列出中央伺服器 REST / WebSocket 端點總覽及認證方式，供開發與整合人員查閱。

← 返回[文件索引](../README.md)

啟動後可存取自動生成的互動式文件：

- **Swagger UI**: http://sdprs.local/docs
- **ReDoc**: http://sdprs.local/redoc

## 端點總覽

| 方法  | 路徑                                | 認證方式            | 說明               |
| ----- | ----------------------------------- | ------------------- | ------------------ |
| GET   | /api/health                         | 無                  | 健康檢查           |
| POST  | /api/alerts                         | X-API-Key           | 建立新警報         |
| GET   | /api/alerts                         | X-API-Key / Session | 取得警報列表       |
| GET   | /api/alerts/{id}                    | X-API-Key / Session | 取得警報詳情       |
| PUT   | /api/alerts/{id}/video              | X-API-Key           | 上傳 MP4 影片      |
| PATCH | /api/alerts/{id}/resolve            | Session             | 標記已處理         |
| POST  | /api/edge/{node_id}/snapshot        | X-API-Key           | 上傳快照           |
| GET   | /api/edge/{node_id}/snapshot/latest | X-API-Key / Session | 取得最新快照       |
| GET   | /api/nodes                          | Session             | 節點列表           |
| GET   | /api/nodes/summary                  | Session             | 節點統計           |
| POST  | /api/stream/{node_id}/start         | Session             | 啟動串流           |
| POST  | /api/stream/{node_id}/stop          | Session             | 停止串流           |
| WS    | /ws                                 | Session Cookie      | WebSocket 即時推送 |

## 相關文件

- MQTT 主題與命令通道請見 [MQTT 主題參考](mqtt-topics.md)。
