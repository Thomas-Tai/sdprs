# 稽核第四輪 · Audit Round 4 — 規格 + 實作計畫

> 日期：2026-08-02 · 範圍：`2026-08-02-pump-socket-switch-design.md`（725 行）
> 與 `2026-08-02-pump-socket-switch-firmware.md`（3593 行），提交 `afc14a1`
>
> 前三輪問的是「會不會出事」「能不能維護」。本輪為交付前總稽核，重點放在
> **規格與計畫之間的落差**、以及**計畫宣稱交付但實際沒有交付的東西**。
> 機械性主張（測試數、import 解析、行號）皆以實際執行驗證，非推論。

**結論**：設計方向正確，安全論證紮實。但有 **1 項採購前必須解決的市電拓樸問題**、
**3 項高風險缺陷**（其中 1 項會在颱風情境下讓水泵停擺 3 分鐘），以及一批
「計畫承諾了但沒有任何任務實作」的落差。**不建議在 C1 釐清前下單採購。**

---

## 🔴 C1 — 市電拓樸未定義：單極切換可能落在兩條火線的迴路上

**規格 §4.1 單線圖以 L / N 繪製，接觸器只切 L 一極。**

台灣民生 220V 通常來自 **單相三線 110/220V**（中間抽頭變壓器），220V 負載接
**L1–L2 兩條火線，對地各 110V**。只切一極時，接觸器正常斷開的情況下，
插座仍有一支腳對地帶 110V。

若現場為 380/220V 三相四線，則 L–N 為真，單極切換符合慣例。**兩種都存在，
規格沒有記錄是哪一種。**

**規格內部已經自相矛盾**：§4.1 把 N 拉到插座，但 §4.1／§9.7 選用的
**NEMA 6-15R 沒有中性極**——它是 2 火線 + 接地。插座的針腳定義與單線圖畫的
不是同一件事。

**後果**：
- §4.5「STOP 不等於隔離」目前的理由是「接點可能熔接」。若為 L1–L2，
  **即使接觸器完好，插座仍部分帶電**——警語的嚴重度被低估。
- BOM 受影響：接觸器需 **2 極主接點**、MPCB 需 **2P**。§4.2 只寫
  「≥10A AC3 @230V 單相」與「MPCB 10A、D 曲線」，**極數皆未指定**。
- RCD 已指定 2P，兩種拓樸都正確，不受影響。

**§10 項 1 問錯了問題。** 目前只問「現場有沒有 220V」，答案影響宣告最大值；
真正影響 BOM 與人身安全的是「220V 是 L–N 還是 L–L」。

**建議**：§10 項 1 改為必須記錄實測 **L1–PE / L2–PE / L1–L2 三個電壓**，
並在 §4.2 依結果指定極數。市電驗收紀錄 §1 增列同樣三欄。
在拓樸確認前**不下單**。

---

## 🟠 H1 — 開機保留會「復燃」，在退水過程中停泵並觸發 180 秒鎖定

**計畫 Task 7 Step 7（行 2109–2135）+ `apply_boot_holdoff`（行 2012–2039）。**

保留時間每個 tick 重算：

```python
return boot_guard.holdoff_remaining_ms(
    uptime, boot_guard.holdoff_total_ms(profile, urgent, reset_loop))
```

`urgent = (PUMP_MODE == "DRAIN" and readings["high_water"] is True)`。
計畫只考慮了 urgent **縮短**保留，沒考慮 urgent **消失**時保留會變回 60s。

`apply_boot_holdoff` 在 `remaining_ms > 0` 時**無條件強制 OFF**，不論水泵是否
正在運轉（docstring 寫的是 "Suppress pump-ON"，實作是 force OFF）。

**SOCKET_220V + DRAIN 的實際序列**：

| 時間 | 事件 |
|---|---|
| t=0 | 開機，淹水中，`high_water=True` → urgent → 保留 10s |
| t=10s | 保留到期，水泵啟動（`_min_off_since` 為 None，Layer 3.5 不擋）|
| t=10–40s | 抽水，水位下降 |
| t=40s | `high_water` 轉 False → urgent 消失 → total 回到 60s → remaining=20s → **強制停泵** |
| | `pump_controller.apply()` 記錄 ON→OFF → `_min_off_since = now` |
| t=60s | 保留到期，但 Layer 3.5：`min_off_elapsed=20s < 180s` → `MIN_OFF_WAIT` |
| t=220s | 才允許再啟動 |

**淹水基坑抽了 30 秒就停機，接著 3 分鐘拒絕啟動。** 而 `high_water` 在抽水後
轉 False 正是系統正常運作的表徵，不是邊角案例。這正好打在第二輪 A5
（「60s 保留會延誤洪水應變」）要修的那個情境上。

**修法**：保留一旦釋放就閂住，不再復燃。closure 內加一個 `released` 旗標，
`remaining == 0` 之後永遠回傳 0。三行。

---

## 🟠 H2 — 所有新增 flag 都到不了遙測；「回報剩餘秒數」沒有任何任務實作

`mqtt_client.build_payload`（`mqtt_client.py:37-49`）**逐鍵挑選** flags，
只取 `raining`／`float_safe`／`high_water`／`sensor_conflict`／
`dry_run_protect`／`manual_override` 六個。Task 11 只加了 `extra` 參數，
**沒有動這份白名單**。

因此以下全部計算出來後在 payload 邊界被丟棄：
`rest_remaining_ms`、`min_off_remaining_ms`、`min_off_wait`、
`boot_holdoff`、`boot_holdoff_remaining_ms`、`container_full`、`overload_trip`。

`manual_state["last_rejected_remaining_ms"]` 更是連 `publish_cb` 都沒有經過。

**受影響的宣稱**：
- Task 7 commit message：「reporting the remaining time so the dashboard can
  say why the click did nothing」——**未交付**。
- 規格 §5.7：「回傳剩餘秒數，讓儀表板能說明為什麼沒有動作」——**未交付**。
- Task 12 台架矩陣第 5 項：「220V：拒絕並回報剩餘秒數」——
  **驗收員沒有任何管道觀察到這個值**，該項無法簽署。

`reason` 欄位仍會發布（`MAX_RUNTIME_REST` / `MIN_OFF_WAIT` / `BOOT_HOLDOFF` /
`CONTAINER_FULL` / `OVERLOAD_TRIP`），所以「為什麼」看得到，「還要等多久」看不到。

**修法**：Task 11 Step 3 併入 flags 白名單擴充，或改為透傳 `*_remaining_ms`
系列。同時把 `last_rejected_remaining_ms` 併入 `publish_cb` 的 `merged`。

---

## 🟠 H3 — 無突波保護（SPD）

全文 0 次提及突波／SPD／雷擊。這是一個**颱風期間部署在台灣、可能半戶外的
市電箱**，設計已為 CT 二次側加了 TVS，卻沒有為市電進線加任何箝位。

雷擊與電網暫態是這類設備最常見的損壞原因，而 DIN 導軌 Type 2 SPD 約
NT$200–600，且**需要導軌空間**——箱體做完後加裝要重排配線。與 §4.8 A 案、
§4.6.1 同屬「製作時便宜、事後昂貴」類別。

**修法**：§4.2 BOM 增列 SPD，§4.1 單線圖置於 RCD 上游，§8.3 增列一項目視確認。

---

## 🟠 H4 — 設定錯誤會讓節點進入無法自救的重開機迴圈

Task 7 Step 6 把 `profiles.validate()` 與 `build_decider()` 放進 `main()` 的
init `try:` 區塊內（計畫行 2086–2088）。該區塊的 `except` 是
（`main.py:214-217`）：

```python
except Exception as e:
    print("[MAIN] Init failed, resetting: %s" % str(e))
    import machine
    machine.reset()
```

`validate()` 擲出 ValueError → `machine.reset()` → 重開 → 再擲 → **永久迴圈**。

**觸發機率不低**：`config.py:60` 寫著
`WDT_ENABLED = True  # 生產預設為 True；開發除錯時可暫時改為 False`——
這是文件化的開發流程，而在 `SOCKET_220V` 下它現在會把節點變磚。
`PUMP_MODE` 打錯字（`"Drain"`）有完全相同的後果。

更糟的是 `persist.register_boot()` 排在 `validate()` **之後**，所以
`boot_count` 從未遞增，重置迴圈偵測器也不會留下任何線索。節點就是離線，
而 §9.1 已經說明離線無法歸因。

實體方向是安全的（GPIO 從未驅動 → 線圈失電 → 水泵停止），但在 §4.8 A 案下
修復意味著到場打開低壓艙。

**修法**：把 profile 驗證移出會 `machine.reset()` 的路徑。失敗時保持繼電器
OFF、讓 MQTT 正常上線並發布一個明確的組態錯誤告警，比無聲重開機有用得多。

---

## 🟡 M1 — Task 1 的 `import golden_grid` 會直接失敗（已實測）

`edge_pump/conftest.py` 只把 **`edge_pump/`** 插入 `sys.path`，而
`edge_pump/tests/__init__.py` 存在（tests 是套件）。因此 `tests/` 本身
**不在** `sys.path` 上。

實測（建立探針模組後執行 pytest 再刪除）：

```
tests/test__probe_import.py:1: in <module>
    import _probe_mod
E   ModuleNotFoundError: No module named '_probe_mod'
```

計畫行 258 的 `import golden_grid` 因此會在**收集階段**就失敗。
Task 1 Step 4（行 290）宣稱「`test_grid_size_is_stable` should PASS」——
實際是整個檔案 collection error，兩個測試都不會執行。

產生器 `tools/gen_decide_golden.py` 自己額外插入了 `tests/`，所以它可以跑；
**同一個模組在兩處用不同方式 import，只有測試那一側是錯的。**

**修法**：`from tests import golden_grid`，並更正 Step 4 的預期輸出。

---

## 🟡 M2 — 測試數量鏈不可靠，終值差 7

每個 Task 都用「Expected: PASS — N tests」當驗收訊號。實際逐檔清點（基準 70
已實測確認）：

| Task | 計畫宣稱 | 實際 | 說明 |
|---|---|---|---|
| 1 | 72 | 72 | ✓ |
| 3 | 87 | **86** | `test_config.py` 淨增 4（1 個是改寫既有），非 5 |
| 4 | 97 | 96 | 承上 |
| 5 | 117 | **115** | `test_boot_guard.py` 是 **11** 個測試，非 12 |
| 6 | 131 | 129 | |
| 7 | 143 | 141 | |
| 8 | （未列）| 149 | +8 |
| 9 | 155 | **162** | Task 8 的 +8 與 Task 9 的 +13 沒有累加進去 |
| 10 | 158 | 165 | |
| 11 | 165 | **172** | |

終值應為 **172（70 基準 + 102 新增）**，計畫的 165 / 「+95 new」皆錯。
實作者會在七個不同的檢查點看到對不上的數字，每次都得判斷是自己弄壞了還是
計畫寫錯——這正是驗收訊號最不該有的狀態。

> **修正後的實際終值為 183，不是本節寫的 172。** 172 是**稽核當下**的清點結果；
> 隨後套用的 H1／H2／H3／H4／M3 修正各自帶進新測試（閂鎖式保留追蹤器、payload
> 白名單、`resolve_runtime()` 的組態錯誤路徑等），使終值再增 11。
> 計畫現行的完整鏈為
> **72 · 72 · 86 · 97 · 121 · 135 · 150 ·（158）· 171 · 174 · 183**，
> 終值 **183 = 70 基準 + 113 新增**。**以計畫為準，本節的 172 保留為稽核當下的紀錄。**

---

## 🟡 M3 — 12V 節點每次開機都寫 flash，且永不清除

Task 7 Step 6 的 `persist.register_boot(nvs)` **無條件執行**（計畫行 2090），
不看 profile。而在 `PUMP_12V` 下：

- `boot_loop_threshold = 0` → `is_reset_loop()` 恆為 False，計數器沒有用途；
- `boot_healthy_ms = 0` → `is_boot_healthy()` 恆為 False，**永不清除**；
- 更徹底的是 Step 7 的 closure 在
  `boot_holdoff_ms or boot_loop_holdoff_ms` 皆為 0 時**根本不會建立**，
  所以 `clear_boot_count()` 在 12V 節點上永遠不會被呼叫。

結果：台架節點每次上電就消耗一次 flash 抹寫額度，計數單調成長且無歸零路徑。
台架節點在開發期間的上電次數遠高於現場節點。這與計畫自己在 `persist.py`
docstring 立下的「NEVER per control-loop iteration」紀律屬於同一類問題。

**修法**：`if profile["boot_loop_threshold"] > 0:` 才 `register_boot`。

---

## 🟡 M4 — NVS 不可用時，重置迴圈偵測會無聲失效（風險登錄缺漏）

`persist._read()` 對任何失敗都回傳 `0`，`register_boot()` 寫入失敗回傳 `0`。
因此 flash 分割區損壞或不存在時，`is_reset_loop(0, 3)` 恆為 False →
**偵測器讀到「一切正常」**，保留時間退回 60s 而非 300s。

**這與 B2 判定 RTC memory 不可用的失效方向完全相同**，只是成因不同
（B2：brownout 清空；此處：flash 不可用）。

`register_boot()` 的 docstring 說這是「the safe direction: it only ever
lengthens a hold-off」——**方向寫反了**：失去偵測是讓保留變短，不是變長。

計畫的「Accepted design limitations」表格**有**記錄 `persist._read → 0`，
但只寫了接觸器保養追蹤失效（LOW）這個後果，**沒有寫重置迴圈偵測失效**
（也就是 B2 等級的那個後果）。風險登錄低估了自己已經知道的事。

**修法**：至少把後果補進限制表；更好的是發布 `nvs_ok: false`，讓
「計數器是 0」與「計數器讀不到」在遙測上可區分。

---

## 🟡 M5 — Task 9 重演了計畫自己警告過的「模組沒人呼叫」陷阱

Task 9 的 Files 寫著「add `apply_overload_interlock`, **CT read in the loop**」
（計畫行 2426），但 Task 9 的 12 個步驟裡**沒有任何一步把它接上
`run_iteration`**——實際接線在 **Task 11 Step 4**（行 3044–3066）。

於是 Task 9 以全綠的測試套件結束，而 CT 的唯一控制權限（過載互鎖）是死碼，
`readers["ct"]` 建立了但沒有人取樣。

計畫在 Task 5 上加了明確的前向參照警告（「Nothing calls any of this until
Task 7 … a green suite here is not evidence the protection works」），
**Task 9 沒有同樣的警告**，而它的 Files 區塊還反過來暗示接線已在本任務內完成。

**修法**：Task 9 的 Files 移除「CT read in the loop」，並補上與 Task 5 同款的
前向參照警告。

---

## ⚪ 低優先

| # | 項目 |
|---|---|
| L1 | 市電驗收紀錄行 3349 的說明寫「**第 15 項**的意義：GPIO 在 MCU 當機時維持最後狀態」——該說明講的是 **第 23 項**（ESP32 斷電測試）。第 15 項是分艙的艙門開關，行 3325 已有它自己的說明。上一輪重新編號時漏改。 |
| L2 | 計畫行 3443 連結 `docs/deployment/esp32-ch340-setup.md`，**該檔案不存在**（`docs/deployment/` 下只有 8 個檔案，無此檔）。Task 12 Step 6 的驗證只數表格列數，不檢查連結。 |
| L3 | 規格 §9.4（行 529）引用 `config.py:73-79`；三個 `*_ACTIVE_LOW` 實際在 **81–83**（73 是註解行）。**計畫引用的 81-83 才是對的**，錯的是規格。 |
| L4 | Task 4 Step 7 讓 `_min_off_since` 與 `_off_since` 在 `apply()` 的同一個區塊中以**完全相同的規則**維護，兩者在任何時刻數值恆等。規格 §5.4 主張的解耦只在**讀取端**成立（None 的解讀不同），而沒有任何測試保證這兩個欄位不會漂移。建議補一個不變式測試。 |
| L5 | 規格 §5.2 對 `PUMP_12V` 的 `boot_loop_threshold` / `boot_healthy_ms` 寫「—」，計畫用 `0`。語意相同（0 = 停用）且 `test_both_profiles_have_identical_key_sets` 會強制鍵存在，但表格用「—」讀起來像「不適用／不存在」。建議統一寫 0。 |

---

## 已查核且判定健全的部分

- **黃金基準機制**：3213 = 17 scenarios × 7 levels × 3³ tristates，已核算無誤；
  網格穩定性警語、「不得重新產生基準來讓測試通過」的 Step 4 檢查、
  以及每個後續任務的「只有這些 flag 可以變」差異檢查，是這份計畫最強的部分。
- **`_safety_guards()` 抽取**：逐行比對 `control_logic.py:68-192` 的現行邏輯，
  抽取後的三個函式在行為上等價，包含 `state` 就地變異與 fall-through 的
  latch 清除語意。
- **`pump_controller.apply()` 的替換片段**與現行 `pump_controller.py:87-94`
  完全吻合。
- **`decide()` 之上的三層互鎖**（manual / boot / overload）形狀一致，
  皆回傳完整 decision dict 並經 `pump_controller.apply()`，A1 的架構約束守住了。
- **AC3 選型規則、PE、HOA 上拉方向、CT 二次側開路要求、分艙三條件**——
  硬體側的安全論證完整且理由寫得清楚。
- **Task 12 的四份文件**內容紮實：驗收紀錄要求實測值而非打勾、
  回復程序要求先演練、交接九項齊全。
