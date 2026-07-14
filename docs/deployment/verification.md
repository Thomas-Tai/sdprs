# 部署後完整驗證清單

> 所有節點部署完成後，逐一確認本清單，確保整套系統運作正常。面向現場部署與驗收人員。

← 返回[部署指南](README.md)

---

在所有節點部署完成後，逐一確認以下項目：

## 中央伺服器

- [ ] `sudo systemctl status sdprs-server` 顯示 active (running)
- [ ] `sudo systemctl status mosquitto` 顯示 active (running)
- [ ] `sudo systemctl status nginx` 顯示 active (running)
- [ ] 瀏覽器打開 http://sdprs.local 能看到登入頁面
- [ ] 能用帳號密碼成功登入儀表板
- [ ] 儀表板右上角 WebSocket 狀態顯示綠色圓點

## 邊緣節點

- [ ] `sudo systemctl status sdprs-edge` 顯示 active (running)
- [ ] `sudo systemctl status autossh-tunnel` 顯示 active (running)
- [ ] 儀表板「系統狀態」頁面顯示此節點為「在線」
- [ ] 儀表板「監控牆」頁面能看到攝像頭畫面

## 水泵節點

- [ ] ESP32 外接 5V/2A 電源（非僅 USB 供電）
- [ ] ESP32 上綠色 LED 亮起（表示泵停止，正常待機）
- [ ] 串口日誌顯示 `[BOOT] WiFi connected!` 和 `[MQTT] Connected to broker`
- [ ] EMQX Dashboard Clients 頁面顯示 `pump_node_01` 在線
- [ ] EMQX Dashboard 可看到 `sdprs/edge/pump_node_01/pump_status` 主題有資料（每 10 秒）
- [ ] 儀表板顯示水泵節點狀態

## Zeabur 雲端部署驗證

- [ ] `https://<your-domain>.zeabur.app/api/health` 回傳 `{"status": "healthy"}`
- [ ] Dashboard `/login` 可登入
- [ ] Pi 端 `journalctl -u sdprs-edge-cloud` 看到 snapshot POST `204`
- [ ] 監控牆 `/monitor` 顯示 Pi 即時快照
- [ ] 主控台顯示 節點: 1/1
- [ ] `sdprs` user 已加入 `video` 和 `audio` group

## MQTT 通訊測試

```bash
# 在中央伺服器上測試 MQTT 是否正常工作

# 終端 1：訂閱所有主題
mosquitto_sub -h localhost -t "sdprs/#" -v

# 終端 2：發送測試消息
mosquitto_pub -h localhost -t "sdprs/test" -m "hello"
# 終端 1 應該看到這條消息
```

---

驗收通過後，日常運維與故障排除見 [../operations/runbook.md](../operations/runbook.md) 與 [../operations/troubleshooting.md](../operations/troubleshooting.md)。
