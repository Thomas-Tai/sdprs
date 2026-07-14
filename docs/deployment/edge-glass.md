# 玻璃偵測邊緣節點部署（Pi 4／Pi 5，LAN 模式）

> 部署現場的玻璃破裂偵測節點（視覺＋音訊融合），並透過 SSH 反向隧道連回中央伺服器。每個攝像頭對應一個邊緣節點。面向現場部署人員。

← 返回[部署指南](README.md)　·　硬體與網路先看 [../hardware-network.md](../hardware-network.md)

---

> **每個攝像頭對應一個邊緣節點。預計時間：10-15 分鐘**
> Zeabur 雲端模式請改看 [Zeabur 雲端方案 › 步驟 5](zeabur-cloud.md#步驟-5設定-pi-邊緣節點連接雲端)。

## 步驟 1：燒錄 Pi OS、SSH 連線、下載程式碼

參照 [中央伺服器（Pi 5）› 部署前準備](pi5-server.md#部署前準備燒錄-pi-os)（hostname `sdprs-glass-01`），開機後：

```bash
ssh pi@sdprs-glass-01.local
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/Thomas-Tai/sdprs.git /opt/sdprs
```

> **替代：** 開發機上有 LAN 連線時可改用 `cd sdprs/scripts && SDPRS_GLASS_HOST=<pi-ip> ./deploy_sync.sh init-glass 01`，會用 rsync 同步並自動建環境，跳到步驟 4。

## 步驟 2：執行一鍵佈建腳本

```bash
cd /opt/sdprs/scripts && sudo chmod +x setup_pi.sh
sudo ./setup_pi.sh glass_node_01 192.168.1.100 --api-key <your-edge-api-key>
```

- `glass_node_01`：節點唯一 ID（第二台用 `glass_node_02`）
- `192.168.1.100`：中央伺服器 IP
- `--api-key`：與中央伺服器 `EDGE_API_KEY` 一致；省略則寫入預設 placeholder（事後 `sudo nano config.yaml` 改）

腳本會自動完成：hostname / 時區 / tmpfs / watchdog / 依賴 / venv / SSH 金鑰生成 / `config.yaml`（含 API key）/ `.env.tunnel` / systemd 服務啟用 + 啟動。

## 步驟 3：配置 SSH 金鑰（讓邊緣節點能反向打洞）

腳本結尾會印出 SSH 公鑰。在**中央伺服器**用一條指令吃進去：

```bash
# 在中央伺服器上（推薦）
ssh-copy-id -i ~/.ssh/id_ed25519.pub sdprs@sdprs-glass-01.local
# 或反過來：在邊緣節點上推給伺服器
sudo -u sdprs ssh-copy-id sdprs@192.168.1.100
```

如果還沒設密碼登入，第一次需在中央伺服器 `sudo passwd sdprs` 暫時開啟，配置完即可關閉（`sudo passwd -l sdprs`）。

## 步驟 4：啟動 SSH 隧道並驗證

```bash
sudo systemctl start autossh-tunnel
journalctl -u sdprs-edge -f         # 應看到偵測迴圈日誌；Ctrl+C 退出
```

**驗證清單：**

- `systemctl is-active sdprs-edge autossh-tunnel watchdog` → 三個都 `active`
- 中央儀表板「系統狀態」頁顯示此節點 **在線**（綠色）

## 部署第二台

```bash
sudo ./setup_pi.sh glass_node_02 192.168.1.100 --api-key <same-key>
```
hostname 燒錄時改成 `sdprs-glass-02`，其他完全相同。

---

## 下一步

- 若還要部署水泵節點：[edge-pump-esp32.md](edge-pump-esp32.md)。
- 全部就緒後，執行 [verification.md](verification.md) 完整驗證清單。
