# 220V 切換插座控制器設計 · Pump Socket Switch Controller Design

> **Status**: 設計已核可，待實作計畫 · Design approved, awaiting implementation plan
> **Author**: 工程 · Engineering
> **Date**: 2026-08-02
> **Impacts**: `edge_pump/config.py`, `edge_pump/control_logic.py`, `edge_pump/pump_controller.py`, `edge_pump/sensors.py`, `edge_pump/main.py`, 新增市電控制箱硬體 · new mains control box
> **Depends on**: `docs/pump-operational-mode-decision.md`（Mode C 已選定 · Mode C chosen）

---

## 1. 背景 · Background

老闆重新定義水泵節點的交付範圍：不再交付**完整抽水系統**，改為交付一個**220V 插座開關控制器**。現場自備水泵，插入我們的插座；我們負責偵測水位並決定何時通電。

The boss has re-scoped the pump node. We no longer deliver a complete pumping system; we deliver a **switched 220V socket**. The site plugs in its own pump. We own the sensing and the decision of when to energize it.

### 1.1 關鍵發現 · The finding that shapes this design

**韌體本來就已經在做這件事。** `control_logic.decide()` 是純函式安全核心，`pump_controller._apply_relay()` 只是把 GPIO 33 拉高或拉低——它從來不知道另一端接的是什麼。

The firmware already does this. `_apply_relay()` drives one GPIO; it has never known what is on the other side. **This is therefore an actuation-path and hardware-safety problem, not a control-logic rewrite.**

控制邏輯的改動被刻意壓到最小（見 §5.4）。大部分工作在市電側。

---

## 2. 已鎖定決策 · Locked decisions

| # | 決策 Decision | 理由 Rationale |
|---|---|---|
| 1 | 僅更換致動器 Actuator swap only | 感測器、ESP32、自動控制邏輯全部不變 |
| 2 | 保留 12V 版本作為展示／備援 Keep 12V as demo/backup | 一套韌體，兩個 profile，非兩個 build |
| 3 | 市電箱由我們製作 We build the mains box | 牆插 → 我們的箱子 → 水泵插頭 |
| 4 | 依「宣告最大值」設計 Design to a declared maximum | 水泵未知；標示額定，超出者不保 |
| 5 | 加裝 CT 電流感測 Add CT current sensing | 插座化之後唯一能偵測「接點熔接」的手段 |
| 6 | DIN 導軌接觸器 DIN contactor | AC3 額定、失效為斷路、可檢查、可維修 |
| 7 | 面板 HOA 三段開關 Panel HOA switch | 市電設備必須有本地控制，不能只靠儀表板 |
| 8 | Mode C（可切換）Switchable | `PUMP_MODE = DRAIN \| COLLECT`，同一韌體兩種角色 |

---

## 3. 架構：兩個正交軸 · Architecture: two orthogonal axes

本設計的核心是把兩件事拆開，且**互不參照**：

```
ACTUATOR_PROFILE  ──  GPIO 33 另一端是什麼 · what is on the other side
    "SOCKET_220V"  →  接觸器箱 + 現場自備水泵
    "PUMP_12V"     →  現有繼電器 + 12V 水泵（展示／備援）

PUMP_MODE         ──  水位代表什麼意義 · what the water means
    "DRAIN"        →  high_water 啟動水泵（現行行為）
    "COLLECT"      →  high_water 停止水泵 + CONTAINER_FULL
```

四種組合皆為合法部署：

| | `DRAIN` | `COLLECT` |
|---|---|---|
| **`SOCKET_220V`** | 淹水基坑，現場水泵 | 雨水收集，現場水泵 |
| **`PUMP_12V`** | 目前的台架節點 | 桌上展示收集情境 |

**Profile 攜帶**：安全時間參數、CT 啟用、HOA 啟用（見 §5.2）。
**Mode 攜帶**：`decide()` 中 Layer 4 的觸發分支，以及 `CONTAINER_FULL` 告警路徑。

兩者不互相讀取。

---

## 4. 硬體：市電控制箱 · The mains box

### 4.1 單線圖 · One-line diagram

```
                    ┌──────── DIN 封閉箱體 IP54+ ────────────────────┐
                    │                                                │
  L ────────────────┤─[RCD 30mA]─┬─[MPCB 10A, D曲線, 含過載]─┐        │
                    │            │                          │        │
                    │            │      ┌── 接觸器 ─────────┤        │
                    │            │      │  ≥10A AC3 @230V   │        │
                    │            │      │  24VDC 線圈  A1/A2 │        │
                    │            │      └────────┬──────────┘        │
                    │            │               │      ⊙ CT (穿心)   │
  N ────────────────┤────────────┼───────────────┼──────────┬────────┤
                    │            │               │          │        │
  PE ═══════════════╪════════════╪═══════════════╪══════════╪════════╡═══╗
                    │            │               │          │        │   ║
                    │      [2A 保險絲]           └── 220V 專用插座 ───┘   ║
                    │            │                   NEMA 6-15R          ║
                    │      [24V DIN SMPS]            (實體防呆)           ║
                    │       │        │                                   ║
                    │       │   ┌────┴─── HOA 三段開關（2P）              ║
                    │       │   │   HAND ─ 直接接通線圈                   ║
                    │       │   │   STOP ─ 斷開                          ║
                    │       │   │   AUTO ─ 經 MOSFET                     ║
                    │       │   │      └─ 第2極 ──► GPIO 13              ║
                    │       │   │                                        ║
                    │  [降壓 5V]│                                        ║
                    │       │   │                                        ║
                    │   [ESP32] ┴── GPIO 33 ──► MOSFET + 飛輪二極體       ║
                    │       │                                            ║
                    │       └─ GPIO 39 ◄── CT 偏壓網路 + 箝位            ║
                    └────────────────────────────────────────────────────╨═══► 水泵外殼接地
```

### 4.2 BOM

| 項目 Item | 規格 Spec | NT$ |
|---|---|---|
| 接觸器 Contactor | **≥10A AC3 @230V 單相**（見 §4.3）、24VDC 線圈 | 800–1,500 |
| MPCB | 10A、D 曲線、含可調過載 | 900–1,600 |
| RCD | 30mA、2P | 400–700 |
| HOA 凸輪開關 | 2 極、3 段、面板安裝 | 150–300 |
| 24V DIN 電源 SMPS | ≥30W，預留 DC-UPS 擴充 | 300–500 |
| CT + 前端 | 分裂式，含偏壓／箝位（見 §4.6） | 200–400 |
| 箱體、導軌、電纜固定頭、端子、PE 排 | IP54+ | 400–700 |
| **合計 Total** | | **≈ 3,150–5,700** |

> 相較於「繼電器模組」方案（約 NT$300）成本高一個數量級。這是一個沒有 §11 稽核所列缺陷的市電箱的誠實價格。

### 4.3 為何依 AC3 選型（本設計最容易犯錯之處）

繼電器與接觸器上印的電流值通常是 **AC1（電阻性負載）** 額定。水泵是 **AC3（感應電動機）**，啟動突入電流為運轉電流的 5–8 倍。經驗法則：AC3 約為 AC1 的 1/3 至 1/2。

| 標示 Marked | 實際馬達負載能力 Real motor duty |
|---|---|
| 「10A 250VAC」繼電器模組 | ≈3A ≈ 700W |
| 「20A」安裝型接觸器（AC1） | ≈7–10A AC3 ≈ 1,700–2,200W |
| 需求 Required | **≥10A AC3 ≈ 2,200W** |

> **採購規則**：看資料表的 **AC3 欄位**，不要看型號上的數字。「20A」安裝型接觸器很可能不足以承擔本設計宣告的最大負載。

### 4.4 保護接地 PE

- 箱內設 PE 端子排；PE 連續性：進線 → 插座 → 金屬箱體（若有）→ 電纜固定板。
- 水泵金屬外殼必須經插座 PE 接地。
- **RCD 不能取代接地**：沒有接地路徑時，RCD 只會在電流已經流經人體時才動作。
- 接地連續性測試列為驗收必要項目（§8.3）。

### 4.5 HOA 三段開關

標示為 **`AUTO / STOP / HAND`**，**不可標示為 "OFF"**。

> **STOP 不等於隔離。** HOA 開關控制的是**線圈**。接觸器一旦熔接，無論線圈是否激磁都維持導通。因此 STOP 位置**不保證插座無電**。隔離點為 MPCB，並在交接文件中明確指定為上鎖點（lockout point）。

**第二極接至 GPIO 13**：若無此訊號，技師每次切到 HAND 都會產生與「接點熔接」完全相同的電流特徵，導致誤發 CRITICAL 告警。

**上拉方向為關鍵**：依專案既有慣例，拉向 **非 HAND**。斷線時韌體判定為 AUTO，熔接偵測維持**啟用**（傾向誤報）。反向配置會使斷線永久停用熔接偵測（傾向漏報）。誤報優於盲點。

### 4.6 CT 前端

CT 輸出為**雙極性**。直接接進 ADC 腳位會只讀到半個波形，且負半週可能損傷腳位。

必要元件：
- 負擔電阻 burden resistor
- **1.65V 偏壓網路**（使波形落在 ADC 中段線性區）
- 箝位二極體

腳位為 **GPIO 39（ADC1_CH3，唯讀腳）**。
> 早期草案誤用 GPIO 35；該腳已於 `config.py:46` 保留給 `BATTERY_ADC_PIN`。

---

## 5. 韌體 · Firmware

### 5.1 設定新增 · Config additions

預設值即為現行行為，既有節點重新燒錄後行為不變：

```python
ACTUATOR_PROFILE = "PUMP_12V"    # "SOCKET_220V" | "PUMP_12V"
PUMP_MODE        = "DRAIN"       # "DRAIN" | "COLLECT"
```

### 5.2 Profile 參數表 · Profile params

集中於單一表格，不散落成個別旗標：

| 參數 | `PUMP_12V` | `SOCKET_220V` | 理由 |
|---|---|---|---|
| `min_off_ms` | 0 | 180000 | `0` 表示 Layer 3.5 守衛不作用（12V 直流泵無此需求）。小型交流水泵每小時啟動次數上限約 10–20 次；180s ≈ 20 次/hr |
| `burst_cooldown_ms` | 30000 | 180000 | 衝突叢發（Layer 1）位於 min-off 之上，30s 會讓 AC 馬達在 15 分鐘內重啟約 30 次 |
| `boot_holdoff_ms` | 0 | 60000 | 見 §5.6（含高水位縮短條款） |
| `ct_enabled` | False | True | |
| `hoa_enabled` | False | True | |

### 5.3 腳位新增 · Pin additions

| 腳位 | 用途 | 備註 |
|---|---|---|
| `CT_ADC_PIN = 39` | CT 電流感測 | ADC1_CH3，WiFi 安全；35 已保留給電池 |
| `HOA_HAND_PIN = 13` | HAND 位置偵測 | HIGH_WATER 移至 26 後釋出 |

### 5.4 `control_logic.py` 變更

沿著真正的接縫拆分——**安全與模式無關，觸發才與模式有關**：

```
_safety_guards()      ← Layer 1,2,3,3.5  模式無關 mode-INDEPENDENT
   ├── decide()           ← Layer 4  DRAIN 觸發（維持現狀）
   └── decide_collect()   ← Layer 4  COLLECT 觸發（約 40 行，非 150 行）
```

如此 Mode C 不會讓「可能弄壞水泵」的程式面積加倍——已通過台架驗證的安全核心維持單一路徑。

**新增 Layer 3.5：min-off 防短循環守衛。**
使用**獨立**的 `min_off_elapsed_ms` 計時器，**不重用** `rest_elapsed_ms`。
> 重用會造成同一計時器有兩個消費者，且對 `None` 的解讀相反：Layer 3 以 `or 0` 強制為 0（阻擋），min-off 則視 `None` 為不阻擋。未來若有人修改該 `or 0`，min-off 行為會無聲改變。多一個整數換掉這個耦合是划算的。

**新增理由碼**：`MIN_OFF_WAIT`、`CONTAINER_FULL`、`COLLECT_RAIN_ON`、`SOURCE_DRY`。

**實作順序為強制要求**：
1. **Commit 1** — 純重構抽出 `_safety_guards()`，DRAIN 行為與現有測試**完全不變**且全綠。
2. **Commit 2** — 加入 `decide_collect()` 與 Layer 3.5。

> 混在一起提交時，COLLECT 的錯誤與重構的迴歸將無法區分。目前的安全核心已在 4 台節點上驗證運行。

### 5.5 CT 的角色 · What the CT does and does not do

**CT 不進入 `decide()`。** 它是輔助訊號與遙測，因此純安全核心保持不變，既有測試持續有效。

唯一例外是**過載跳脫**，那屬於**硬體保護**而非控制決策，位於 `decide()` 之上。

> **⚠ 實作約束（架構性）**：過載互鎖**必須**產生一個完整的 decision dict，經由 `pump_controller.apply()` 生效，**絕對不可直接操作繼電器**。
>
> 直接操作會使狀態機與實體致動器分歧：`ctrl_state["pump_state"]` 仍為 `ON`、`_on_since` 持續累計、下一輪 `decide()` 回傳 `HOLD`（維持現狀）而繼電器恰好已是 OFF，看起來正常但 Layer 3 的最大運轉計時器正在對一台沒有運轉的水泵累計，且因為沒有記錄到 ON→OFF 轉換，`rest_elapsed_ms` 永遠不會啟動。
>
> `apply_manual_override()` 已經正確採用此模式（回傳 decision dict 而非直接動作），照做即可。

### 5.6 重置迴圈與開機保留 · Reset loop and boot hold-off

**問題**：`_off_since` 位於 RAM（`pump_controller.py:19`）。WDT 重置迴圈（`WDT_TIMEOUT = 30000`）會讓 2200W 馬達每 30 秒重啟一次，完全繞過 min-off。

**不可行的做法**：把重置前的 tick 存入 RTC memory。`ticks_ms()` 重開機後從 0 重新計數，跨重置的差值無意義。NTP 牆鐘可行但需要網路，違反離線自主契約。

**採用做法**：於 RTC memory 存放**開機計數器**。短時間內連續開機且中間沒有一次成功長時間運轉，即為重置迴圈的特徵，偵測此特徵不需要跨重置的經過時間。

**開機保留採分級制**：

| 情境 | 保留時間 |
|---|---|
| 冷開機、開機計數器無效 | 完整 `boot_holdoff_ms`（60s） |
| `high_water` 已觸發（DRAIN 模式的緊急情境） | 縮短至 10s |

> 單純套用 60 秒保留會造成：颱風期間節點因電壓驟降重置後，水位上升而水泵拒絕啟動 60 秒。防止短循環不能以犧牲主要使用情境為代價。

### 5.7 手動 ON 的拒絕條件擴充

`main.py:115` 目前僅在 `dry_run_protect` 或 `sensor_conflict` 時拒絕手動 ON，**未檢查 `max_runtime_rest`**。操作員在 Layer 3 冷卻期間點擊 ▶ 即可重啟一台剛連續運轉 10 分鐘的馬達。

12V 時無害；2200W 時這正是燒毀馬達的方式。

**變更**：在 `SOCKET_220V` profile 下，拒絕清單加入 `max_runtime_rest` 與 `min_off_wait`，並回傳剩餘秒數，讓儀表板能說明**為什麼**沒有動作。

---

## 6. 遙測與伺服器 · Telemetry & server 〔Phase 2〕

> **排程說明**：本節與 `fix/spa-lane-2026-08-01`（進行中）及已暫停的 Medium tier 後端 worktree 有檔案衝突風險。**本節排在該兩條線之後執行**，不納入第一階段交付。

### 6.1 遙測欄位（純新增，不改名）

既有欄位名稱維持不變，無遷移成本：

| 欄位 | 用途 |
|---|---|
| `actuator_profile`、`pump_mode` | 儀表板顯示此節點的身分 |
| `current_band` | CT 電流分級（見下方警告） |
| `hoa_hand` | 操作員已取得本地控制 |

> **⚠ 不可發布精確安培值。** ESP32 ADC 未校正時誤差 ±10–20%，且近軌處非單調。發布 `current_a: 3.7` 會被人當真。改為分級：`none / low / normal / high`。

### 6.2 新增告警類型與清除機制

| 告警 | 嚴重度 | 清除方式 |
|---|---|---|
| `WELDED_CONTACT` | CRITICAL | **人工確認**（硬體故障，需實體維修） |
| `OVERLOAD_TRIP` | HIGH | **人工確認**（可遠端自動復歸的過載保護不算過載保護） |
| `CONTAINER_FULL` | HIGH | 感測器恢復後自動清除 |
| `PUMP_NOT_RUNNING` | HIGH | 感測器恢復後自動清除 |
| `HOA_LOCAL_CONTROL` | LOW（持續） | HOA 離開 HAND 後自動清除 |

> `HOA_LOCAL_CONTROL` 的存在理由：`hoa_hand=true` 會停用熔接偵測。若開關被留在 HAND 或輔助接點斷線，該保護將**無聲**失效。必須有一個持續存在的低階告警說明「此節點目前為本地控制，自動保護已降級」。

---

## 7. 失效矩陣 · Failure matrix

| 失效 Failure | 偵測 Detected by | 回應 Response |
|---|---|---|
| 接觸器熔接 | CT：命令 OFF 但有電流 | `WELDED_CONTACT` CRITICAL，需人工確認 |
| 水泵未插／MPCB 跳脫／卡死 | CT：命令 ON 但無電流 | `PUMP_NOT_RUNNING` |
| **接觸器無法閉合**（線圈斷路、機械卡死） | 同上，**與上一列無法區分** | `PUMP_NOT_RUNNING`（診斷需人工到場） |
| 水泵堵轉 | CT：持續過電流 | 互鎖 OFF（經 `pump_controller.apply()`）+ `OVERLOAD_TRIP` |
| 乾轉 | 浮球（Layer 2 硬互鎖） | Layer 2 停泵。CT 低電流僅作為遙測佐證，**不參與該決策**（§5.5） |
| **CT 本身故障**（二次側開路） | 無法自我偵測 | 恆讀 0 → ON 時持續誤報 `PUMP_NOT_RUNNING` |
| 操作員在面板操作 | GPIO 13 HOA 輔助接點 | 抑制熔接告警 + `HOA_LOCAL_CONTROL` |
| **XKC 卡在觸發狀態** | 無法自我偵測 | DRAIN：水泵持續運轉（受 Layer 3 上限約束）<br>COLLECT：水泵永不啟動 |
| ESP32 重置迴圈 | RTC 開機計數器 | 分級開機保留（§5.6） |
| 市電中斷 | — | 節點失電 → 伺服器 `PUMP_OFFLINE_TIMEOUT` 判定離線（見 §9.1） |
| 網路中斷 | — | 依設計不影響控制 |

---

## 8. 測試與驗收 · Testing & commissioning

### 8.1 桌面單元測試（CPython pytest）

- Layer 3.5 min-off 守衛
- `decide_collect()` 觸發邏輯
- Profile 參數選取
- CT 真值表（純函式形式）
- **重構後 DRAIN 既有測試必須完全不變且全綠**（§5.4 Commit 1 的驗收條件）

### 8.2 台架矩陣

2 profiles × 2 modes = **4 種組態**。`docs/deployment/pump-bench-commissioning.md` 目前是為單一組態撰寫的，需擴充。

### 8.3 市電驗收（新增章節，需簽署紀錄）

| 項目 | 需求 |
|---|---|
| 接地連續性 | 必測，記錄實測值 |
| RCD 動作測試 | 必測 |
| 絕緣電阻 | 必測 |
| HOA × 熔接偵測交互 | 切至 HAND 時不得誤發 CRITICAL |
| CT 校正 | 已知電阻性負載，多點電流，鉤表對照，記錄 |
| 突入電流觀察 | 見 §9.3 |

**所有市電測試須記錄測試者姓名與日期。** 這份文件是萬一發生事故時唯一的憑據。

### 8.4 提供／組態核對（程序性管制）

軟體無法偵測 XKC 實體安裝在哪裡。驗收簽署必須明確逐項確認：

- [ ] XKC 實體安裝位置與 `PUMP_MODE` 相符
- [ ] `ACTUATOR_PROFILE` 與實際安裝硬體相符

> 若 XKC 裝在收集容器上卻執行 DRAIN 韌體，容器滿時水泵會**啟動**——溢流與水損，肇因僅是一個設定字串。Mode C 的真正風險不在程式碼，而在提供組態。

---

## 9. 已知限制與假設 · Known limitations & assumptions

### 9.1 市電故障的可觀測性

v1 **不含**電池／UPS。`BATTERY_ADC_PIN` 與 `POWER_SOURCE_PIN` 目前皆為 `None`（`config.py:46-47`）。

市電中斷時節點失電，伺服器仍會經由 `PUMP_OFFLINE_TIMEOUT`（`config.py:28`）判定該節點離線——**故障不會完全無聲，但無法歸因**：「離線」可能是網路問題，也可能是市電問題。

24V 電源軌已預留容量，可於後續加裝 DIN DC-UPS 模組取得歸因能力。

### 9.2 未認證設備

本箱體為自行組裝的市電設備，**未取得 BSMI/CNS 認證**。

- 組裝與檢查應由合格電匠執行。
- 交接文件必須明載「本設備未經認證」。
- 需標示額定並註明「不得超載」。

### 9.3 無法量測突入電流

鉤表無法捕捉約 100ms 的突入尖峰；需示波器搭配電流探棒。若無此設備，**接觸器選型只能依賴資料表 AC3 額定**，這使 §4.3 的選型規則成為唯一防線。

### 9.4 感測器極性尚未驗證

`config.py:73-79` 明載 `FLOAT_ACTIVE_LOW` / `RAIN_ACTIVE_LOW` / `HIGH_WATER_ACTIVE_LOW` 為**推測預設值**，待 §A 台架極性驗證。該驗證**至今未執行**。

四種組態全部繼承此前提。**極性驗證是硬性前置條件，不是計畫中的一個步驟。**

### 9.5 RMS 取樣的即時性未驗證

每次輪詢阻塞約 60ms 進行 RMS 取樣，同時 MQTT 與 30s WDT 皆在運行。理論上可行，**但須實測後才可定案取樣率**。

---

## 10. 開放項目 · Open items

| # | 項目 | 阻擋對象 |
|---|---|---|
| 1 | **確認現場確實有 220V 迴路。** 台灣民生為 110V。若現場為 110V，宣告最大值減半，BOM 全面重算。 | **採購前硬性關卡** |
| 2 | 感測器極性驗證（§9.4） | 任何硬體工作之前 |
| 3 | 決策文件 Q3：容器容量／流量／溢流緩衝秒數 | COLLECT 組態的安全參數 |
| 4 | 宣告最大值**簽署核可**。本規格全文依 **10A / 2200W @220V** 設計；此項為老闆簽署確認，非待定數值。 | 額定標示與 MPCB 過載設定值 |

---

## 11. 稽核紀錄 · Audit log

本設計經兩輪機電工程稽核。以下為發現事項與處置。

### 第一輪（硬體與市電側）

| ID | 嚴重度 | 發現 | 處置 |
|---|---|---|---|
| C1 | 🔴 | 保護接地完全缺漏 | §4.4 |
| C2 | 🔴 | 插座須為 220V 專用防呆型（台灣民生為 110V） | §4.1、§10.1 |
| C3 | 🔴 | BOM 誤將接觸器標示數字當作 AC3 額定——與本文開頭警告的錯誤相同 | §4.3 |
| H1 | 🟠 | HOA "OFF" 不等於隔離；熔接的接觸器不受線圈控制 | §4.5 |
| H2 | 🟠 | ESP32 電源未列入 BOM；置於負載 MCB 下游會使節點在最需要告警時失電 | §4.1、§4.2 |
| H3 | 🟠 | min-off 不跨重置存活 | §5.6 |
| H4 | 🟠 | 衝突叢發以 30s 週期繞過 min-off，15 分鐘內約 30 次重啟 | §5.2 |
| H5 | 🟠 | 手動 ON 未檢查 `max_runtime_rest`（已於程式碼確認） | §5.7 |
| H6 | 🟠 | 無馬達過載保護（MCB 保護電纜，不保護馬達） | §4.2 改用 MPCB |
| M1 | 🟡 | 230V 線圈使面板開關帶市電、電壓驟降時顫動 | §4.1 改用 24VDC 線圈 |
| M2 | 🟡 | C 曲線 MCB 會被突入電流誤跳 | §4.2 改用 D 曲線 |
| M3 | 🟡 | CT 前端需偏壓與箝位 | §4.6 |
| M4 | 🟡 | 既有 median-of-3 ADC helper 不適用於 50Hz 交流 | §5.5、§9.5 |
| M5 | 🟡 | 0.3A 於 20A CT 上僅佔滿刻度 1.5%，接近雜訊底 | CT 依實際負載選型 |
| M6 | 🟡 | `min_off_ms = 60s` 允許每小時 60 次啟動 | §5.2 改為 180s |

### 第二輪（韌體與系統側）

| ID | 嚴重度 | 發現 | 處置 |
|---|---|---|---|
| A1 | 🔴 | CT 互鎖直接操作繼電器 → 狀態機與致動器分歧 | §5.5（架構性約束） |
| A2 | 🔴 | 失效矩陣宣稱有電池備援，實際 `BATTERY_ADC_PIN = None` | §9.1（修正：伺服器離線判定使其非完全無聲，但無法歸因） |
| A3 | 🔴 | Mode／profile 與實體安裝的錯配無法由軟體偵測 | §8.4 程序性管制 |
| A4 | 🟠 | 第一輪 H3 的修法不可行：`ticks_ms()` 無法跨重置測量 | §5.6 改用開機計數器 |
| A5 | 🟠 | 60s 開機保留會延誤洪水應變 | §5.6 分級保留 |
| A6 | 🟠 | `_safety_guards()` 重構動到已驗證的安全核心 | §5.4 強制兩段提交 |
| A7 | 🟠 | 四種新告警皆未定義清除機制 | §6.2 |
| A8 | 🟠 | 整個台架矩陣建立在未驗證的感測器極性上 | §9.4、§10.2 |
| A9 | 🟡 | 重用 `rest_elapsed_ms` 造成雙消費者對 `None` 解讀相反 | §5.4 改用獨立計時器 |
| A10 | 🟡 | 發布未校正的精確安培值 | §6.1 改為分級 |
| A11 | 🟡 | HOA HAND 無聲停用熔接偵測 | §6.2 `HOA_LOCAL_CONTROL` |
| A12 | 🟡 | 與兩條進行中的分支有檔案衝突風險 | §6 排為 Phase 2 |
| A13 | 🟡 | 失效矩陣遺漏四種失效模式 | §7 已補入 |
| A14 | 🟡 | 無 CT 校正程序 | §8.3 |
| — | ⚪ | 無法量測突入電流 | §9.3 |
| — | ⚪ | 市電測試需簽署紀錄 | §8.3 |
| — | ⚪ | RMS 取樣的即時性未驗證 | §9.5 |

---

## 12. 交付範圍 · Delivery scope

**Phase 1（本規格）**：市電箱硬體 + `edge_pump` 韌體變更 + 台架驗收程序。

**Phase 2（排在 SPA lane 之後）**：遙測欄位、四種告警類型、儀表板顯示（§6）。

**不在範圍內 Out of scope**：電池／UPS（§9.1）、設備認證（§9.2）、現場水泵本身。
