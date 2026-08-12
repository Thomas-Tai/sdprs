# 水泵節點更新與回復 · Pump Fleet Update & Rollback

> 規格 §12.2。現行 4 台已驗收節點共用 `control_logic.py`，因此更新策略是
> 交付的一部分，不是實作細節。
>
> **這是實體接觸更新**（`mpremote` / `esptool`），與 edge_glass 的 rsync
> 佈署不同。若日後採用規格 §4.8 的 OTA 方案，本文件即被取代。

## 設計上的保障

`ACTUATOR_PROFILE` 與 `PUMP_MODE` 的預設值就是現行行為
（`PUMP_12V` / `DRAIN`），所以重新燒錄既有節點在行為上是**中性**的。
這是刻意的：讓「更新」與「行為變更」兩件事分開發生、分開驗證。

## 步驟

### 1. 標記已知良好版本

```bash
git tag -a pump-fw-known-good-$(date +%Y%m%d) -m "pre-update baseline"
git push origin --tags
```

### 2. 演練回復（**先做這個**）

在**台架節點**上，先燒新版、再燒回舊版、確認節點恢復正常運轉。

> **未演練過的回復程序不算回復程序。** 颱風夜不是第一次執行它的時機。

- [ ] 台架節點燒錄新版 → 正常運轉
- [ ] 台架節點回燒 tag 版本 → 正常運轉
- [ ] 記錄整個回復耗時：______ 分鐘

### 3. 金絲雀

先更新**一台**現場節點，觀察**一個完整的乾濕循環**後再繼續。

- [ ] 節點 ID：______
- [ ] 更新時間：______
- [ ] 觀察至少一次啟泵 → 停泵完整循環
- [ ] `boot_count` 未持續遞增（無重置迴圈）
- [ ] 遙測欄位正常，無非預期告警

### 4. 其餘節點

**逐台更新，不同時。** 若第 2 台出現第 1 台沒有的問題，同時更新會讓你
分不清是韌體問題還是那台節點的問題。

### 5. 回復

```bash
git checkout pump-fw-known-good-YYYYMMDD -- edge_pump/
cd edge_pump && mpremote connect <port> fs cp *.py :
```

> **CH340 板的自動重置不可靠。** `mpremote` 需要 `resume`；`esptool` 需要
> `--before no-reset`。進燒錄模式要手動操作：按住 **BOOT** → 點一下
> **RST** → 放開 BOOT，然後才下指令。若跳過這步，指令會逾時，看起來像
> 節點壞了，其實只是沒進 bootloader。
