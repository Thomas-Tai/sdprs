# 水泵運作模式決策 · Pump Operational Mode Decision

> **Status**: 待決策（需 owner / 老闆確認）  Awaiting decision — needs owner/boss approval before implementation
> **Author**: 工程 · Engineering
> **Date**: 2026-07-19
> **Impacts**: `edge_pump/control_logic.py`, `edge_pump/main.py`, 中央伺服器告警類型, dashboard UI

---

## 背景 · Context

現行水泵節點的韌體實作為 **Mode A（排水模式 / Drainage mode）**。這符合專案「TyphoneCrackDetect_waterRemove」及 SDPRS（Smart Disaster Prevention Response System，智慧防災反應系統）中「移除水量」的命名意圖：颱風時避免積水、抽出淹水區。

但在 2026-07-19 台架驗證討論中，實際部署場景可能是 **Mode B（收集模式 / Containment mode）**：

- 雨水感測器偵測外部下雨
- 水泵啟動，將水抽入建物內的容器
- 高水位感測器（XKC-Y25-V）監測容器水位
- 容器滿 → 水泵停止 + 提醒管理員手動倒空

**這兩種模式使用相同硬體，但控制邏輯完全相反。決策一次即可，之後所有節點依此模式部署。**

The two modes use identical hardware (float switch, MHRD rain sensor, XKC-Y25-V high-water sensor, relay-controlled pump) but the pump-control logic is inverted between them. This decision must be made once and locked in before field deployment — flipping mid-deployment would invert every pump's ON/OFF trigger.

---

## Mode A · 排水模式（現行實作）

```
   [外部下雨]                             [水位到頂 → 抽出]
        │                                          │
        ▼                                          ▼
   ┌────────────┐                          ┌──────────────┐
   │  基坑/淹水區  │  ── 溢流 ──►         │  排水/雨水下水  │
   │  (basin)   │                          │    道 (drain)  │
   └────────────┘                          └──────────────┘
      ↑
      浮球(FLOAT_PIN=32)：水位過低 → 停泵防止空轉
```

### 感測器角色 · Sensor roles

| 感測器 | 訊號 | 泵浦動作 |
|---|---|---|
| 雨水感測（外部） | `raining=true` | **降低啟泵門檻**（80% → 60%），提早排水 |
| 高水位（基坑內） | `high_water=true` | **啟動泵浦**（火速排水以防溢流） |
| 浮球（基坑底） | `float_dry=true` | **停止泵浦**（乾轉保護，水位過低不可運轉） |

### 適用場景 · Use cases

- 地下室排水泵、颱風水患移除、道路積水抽出
- **假設排放目的地無容量限制**（排入市府下水道、外部溪河等）
- 泵浦可長時間連續運轉（有 `MAX_RUN_MS = 600s` 保護，接著強制休息 60 秒）

### 現況 · Current state

**已完整實作、已測試、已在 pump_node_01 台架驗證。** 若選 Mode A，本次不需要任何程式改動。

---

## Mode B · 收集模式（提案）

```
   [外部下雨]                       [容器內部]
        │                                 │
        ▼                                 ▼
   ┌────────────┐   pump    ┌──────────────┐    ┌────────────┐
   │  外部水源   │ ────►     │   收集容器    │ ──►│  管理員手動 │
   │  (source)  │           │ [XKC 探頭在  │    │   倒掉      │
   └────────────┘           │   容器內]    │    │  (manual)  │
                             └──────────────┘    └────────────┘
```

### 感測器角色 · Sensor roles

| 感測器 | 訊號 | 泵浦動作 |
|---|---|---|
| 雨水感測（外部） | `raining=true` | **啟動泵浦**（開始收集雨水） |
| 高水位（容器內） | `high_water=true` | **停止泵浦 + 觸發告警**（容器滿了，需人工倒掉） |
| 浮球（源頭） | `float_dry=true` | **停止泵浦**（源頭無水，避免乾轉） |

### 適用場景 · Use cases

- 雨水收集系統、有限容量儲水、教學/演示裝置
- **排放目的地有容量限制**（水桶、儲水箱、樓層內容器）
- 需要人工介入清空容器

### 需新增的功能 · New features required

1. **反向控制邏輯**：`control_logic.decide()` 需新增一個 `MODE=COLLECT` 分支（或獨立的 `decide_collect()` 函式），把 `high_water` 從「啟動觸發」翻為「停止 + 告警觸發」。
2. **新告警類型 `CONTAINER_FULL`**：中央伺服器需支援這個 event type，儀表板才能顯示「容器已滿，請倒掉」的紅色告警橫幅。
3. **告警清除機制**：管理員倒完水後，如何清除告警？
   - 選項 A：儀表板加「已倒空」按鈕，人工清除
   - 選項 B：自動清除（XKC-Y25-V 回到 `high_water=false` 時解除告警）
4. **`PUMP_MODE` 設定選項**：在 `edge_pump/config.py` 加 `PUMP_MODE = "COLLECT"` 或 `"DRAIN"`，切換兩種模式，方便同一份韌體適用不同節點。

### 實作工作量 · Implementation scope

估計 150-250 行程式碼變更、1 個 commit、~5 分鐘 Zeabur 重新部署：

| 檔案 | 變更範圍 |
|---|---|
| `edge_pump/control_logic.py` | 新增 `decide_collect()` 分支（~60 行） |
| `edge_pump/config.py` | 新增 `PUMP_MODE` 設定 |
| `edge_pump/main.py` | 依 `PUMP_MODE` 分派到不同 `decide()` |
| `edge_pump/mqtt_client.py` | payload 加入 `mode` 欄位（遙測用途） |
| `edge_pump/tests/test_control_logic_collect.py` | 新增 8-10 個測試涵蓋收集模式的決策 |
| `central_server/services/mqtt_service.py` | 收到 `high_water=true` 時觸發 `CONTAINER_FULL` 告警 |
| `central_server/services/event_service.py` | 新增告警類型 `CONTAINER_FULL` |
| `central_server/static/spa/pages/pumps.jsx` | 顯示告警 + 「已倒空」按鈕（若選 A） |

**新的手動控制**（本週已實作的 `[▶ 10s]` / `[⏹ 停機]` 按鈕）在兩種模式下都能運作，不需修改。

---

## 對照速查 · Side-by-side comparison

| 面向 | Mode A · 排水 | Mode B · 收集 |
|---|---|---|
| 雨水偵測 → | 降低啟泵門檻（間接） | **啟動泵浦**（直接） |
| 高水位偵測 → | **啟動泵浦**（火速排水） | **停止泵浦 + 告警** |
| 浮球乾偵測 → | 停止泵浦（乾轉保護） | 停止泵浦（源頭無水） |
| 排放目的地 | 無限容量（排放/下水道） | 有限容量（容器） |
| 主要告警觸發 | 持續高水位 + 泵浦跟不上 | **容器滿了** |
| 是否需人工介入 | 不需要（自動運轉） | **需要**（人工倒空） |
| 適合本專案 | ✅ 符合命名（`waterRemove`） | ✅ 教學演示、有限容器 |
| 實作狀態 | ✅ **完成、已測試、已驗證** | ❌ **未實作** |

---

## 待老闆決策的問題 · Questions requiring owner sign-off

1. **模式選擇**：
   - [ ] **A. 排水模式**（保留現況，本次無需程式改動）
   - [ ] **B. 收集模式**（需新增反向控制邏輯 + 告警類型）
   - [ ] **C. 兩者都要**（新增 `PUMP_MODE` 設定，同一份韌體可切換）

2. 若選 B 或 C，**告警清除機制**：
   - [ ] 儀表板加「已倒空」按鈕，管理員按鈕確認後才清除告警（**建議** — 保留稽核紀錄，避免感測器抖動誤消警）
   - [ ] 自動清除（XKC 回報 `high_water=false` 時解除）

3. 若選 B 或 C，容器實際容量與泵浦流量（避免溢流的安全時間）：
   - 容器容量：______ 公升
   - 泵浦流量：______ 公升/分
   - 從 XKC 觸發到容器完全溢流的緩衝時間：______ 秒
   - （這個決定 `MAX_RUN_MS` 等安全參數的預設值）

4. 現場實際物理配置 — 請確認：
   - 「水源」在哪裡？（屋頂、地下室、外部雨水槽？）
   - 容器擺在哪裡？（能否人工進去倒？）
   - 泵浦電源與泵浦線路的實際長度？

---

## 工程建議 · Engineering recommendation

若專案時程壓力大、只需示範一次，**選 Mode A（現況）** 是最快路徑，本次不需程式異動，可直接進入 §6 台架驗證與現場部署。

若專案本意就是「收集雨水到容器」的教學／演示模型，**選 Mode B** 才符合實際物理配置，Mode A 的邏輯會讓學生/評審看不懂為什麼水位到滿反而更用力抽（因為在收集模式下這是錯的）。

**選 C（可切換）** 是最有彈性但也最複雜的選擇，僅在確定要部署多個不同角色節點時才建議。

---

## 決策紀錄 · Decision log (fill in after approval)

- **決策日期 / Decision date**: __________
- **決策者 / Decided by**: __________
- **選擇模式 / Chosen mode**: [ ] A  [ ] B  [ ] C
- **告警清除機制 / Alert-clear mechanism**: __________
- **後續動作 / Follow-up actions**: __________
