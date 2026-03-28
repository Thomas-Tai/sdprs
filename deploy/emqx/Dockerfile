FROM emqx/emqx:5.8

# ── EMQX for SDPRS ──
# Zeabur 部署：同 project 內 central server 用內部名 "emqx" 連接
# Pi 外部連接：透過 Zeabur Networking 公開 TCP 1883

# 預設監聽端口（不需修改）
# TCP  1883  - MQTT 協議
# HTTP 18083 - EMQX Dashboard（管理介面）
# WSS  8083  - WebSocket（可選）

EXPOSE 1883 18083
