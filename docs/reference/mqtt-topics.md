# MQTT 主題參考

本文件列出邊緣節點、水泵節點與中央伺服器之間的 MQTT 主題、QoS 與方向，供開發與運維人員查閱。

← 返回[文件索引](../README.md)

## 主題總覽

| 主題                                      | QoS | 方向           | 說明                            |
| ----------------------------------------- | --- | -------------- | ------------------------------- |
| sdprs/edge/{node_id}/heartbeat            | 0   | Edge -> Server | 心跳（CPU溫度、記憶體，每30秒） |
| sdprs/edge/{node_id}/pump_status          | 0   | Pump -> Server | 水泵狀態 + 水位（每10秒）       |
| sdprs/edge/{node_id}/stream_status        | 1   | Edge -> Server | 串流狀態（啟動/停止）           |
| sdprs/edge/{node_id}/cmd/stream_start     | 1   | Server -> Edge | 啟動串流命令                    |
| sdprs/edge/{node_id}/cmd/stream_stop      | 1   | Server -> Edge | 停止串流命令                    |
| sdprs/edge/{node_id}/cmd/update           | 1   | Server -> Edge | 遠端更新觸發                    |
| sdprs/edge/{node_id}/cmd/simulate_trigger | 1   | Server -> Edge | 測試觸發                        |

## 相關文件

- REST / WebSocket 端點請見 [API 參考](api.md)。
