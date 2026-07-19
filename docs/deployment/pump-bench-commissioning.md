# 水泵節點台架驗證手冊 — §6 Bench Commissioning

**適用時機**：初次燒錄後、正式現場部署前，或更換感測器/更改極性設定時。
**先決條件**：`docs/deployment/edge-pump-esp32.md` 的燒錄流程已完成，節點能連上 MQTT。
**預期時長**：約 60–90 分鐘（含 ADC 校準）。
**目標**：驗證每一顆數位感測器的極性正確、拉電阻方向安全（斷線時讀不觸發）、類比水位刻度正確；只有全部通過後才把 `FLOAT_ENABLED` / `RAIN_ENABLED` 從 `False` 翻為 `True`。

> **為何要台架驗證？** 極性設定錯誤會讓「斷線 → 讀成觸發 → 抽水機被誤啟動」變成單點失效。本手冊逐一驗證每顆感測器的極性、拉電阻方向、類比刻度，讓每一個「觸發」都是真的觸發。詳見 `sensors.py:97-103` 的拉電阻設計原則與 `PROGRESS.md §9`。
>
> **2026-07-19 bench build 現況**：此節點的預設已是 `LEVEL_ENABLED=False`（未接類比探頭）+ `FLOAT/RAIN/HIGH_WATER_ENABLED=True`（極性沿用最佳預估）。所以你走此手冊的目的是**驗證**極性、發現不對時翻 `*_ACTIVE_LOW` 旗標；不是「解鎖」。附錄 A 有更新後的引腳/旗標速查表。

---

## 0. 台架清單

| 項目 | 用途 |
|---|---|
| 已燒錄的 ESP32 pump 節點 | 待驗證的裝置 |
| 5V/3A USB-C 電源 或 電腦 USB | 供電 + REPL |
| USB-Serial 線 | 進入 MicroPython REPL |
| Thonny 或 mpremote | REPL 互動與檔案傳輸 |
| 浮球開關 | 待接的 `FLOAT_PIN=32` 感測器 |
| 雨水模組（DO 輸出） | 待接的 `RAIN_PIN=25` 感測器 |
| 高水位感測器（選用） | `HIGH_WATER_PIN=26` |
| 類比水位感測器 | `ADC_PIN=34`（ADC1_CH6，只讀腳） |
| 已知水位的容器 | 空 / 半滿 / 全滿三種狀態 |
| 三用電表 | 量測拉電阻拉起後的電壓 |
| 繼電器 + 抽水機（Section E 才接） | 濕測階段 |

**安全**：Section A–D 過程中，繼電器與抽水機**不要通電**。所有極性驗證都應該在乾式狀態、抽水機斷電的情況下完成。

---

## 1. 開工前的紀錄

在筆記本／CSV 上準備一張表，逐項打勾。若任一項失敗，**立即停工**，不要進 Section D 翻旗。

```
節點 ID: ______________  日期: ______________  操作者: ______________

[A] 拉電阻極性乾檢
  [ ] FLOAT_PIN=32 未接線讀值 = 未觸發（不啟泵）
  [ ] RAIN_PIN=25  未接線讀值 = 未觸發
  [ ] HIGH_WATER_PIN=26 未接線讀值 = 未觸發（若啟用）

[B] 感測器實體斷言
  [ ] 浮球乾狀態 → float_dry=True（危險，會鎖抽水機）
  [ ] 浮球濕狀態 → float_dry=False
  [ ] 雨水感測乾  → raining=False
  [ ] 雨水感測濕  → raining=True
  [ ] （選用）high_water 觸發 → high_water=True

[C] 類比 ADC 校準
  [ ] 空桶 raw = _____  level_pct = _____
  [ ] 半桶 raw = _____  level_pct = _____  （應介於 40–60）
  [ ] 滿桶 raw = _____  level_pct = _____

[D] 啟用旗標翻轉 + 重新燒錄
  [ ] FLOAT_ENABLED = True
  [ ] RAIN_ENABLED = True
  [ ] 節點重啟後仍能連上 MQTT

[E] 濕式整合測試
  [ ] 水位 > 80% → 抽水機 ON（HYSTERESIS_ON）
  [ ] 水位 < 20% 持續 30s → 抽水機 OFF
  [ ] 觸發 float_dry=True → 抽水機 OFF（DRY_RUN_OFF）
  [ ] 儀表板顯示 pump_state 與現場一致
```

---

## 2. Section A — 拉電阻極性乾檢（最重要）

**目的**：驗證每一顆數位感測器的 `*_ACTIVE_LOW` 旗標與拉電阻方向一致，斷線時讀「不觸發」，保證單一斷線不會誤啟抽水機。

### A.1 進入 REPL

```bash
# 用 mpremote 或 Thonny
mpremote connect auto
# 或 Thonny → 連線 → 選擇 ESP32 COM 埠
```

### A.2 逐腳測試（感測器**未接線**）

在 REPL 中執行：

```python
import machine

# FLOAT_PIN=32, active_low=True → 應該 PULL_UP → 空腳讀 1
p = machine.Pin(32, machine.Pin.IN, machine.Pin.PULL_UP)
print("FLOAT unconnected reads:", p.value())  # 期望: 1
# 邏輯轉譯：active_low=True 且 raw=1 → float_dry=False（安全）→ 不啟泵 ✓

# RAIN_PIN=25, active_low=True → 應該 PULL_UP → 空腳讀 1
p = machine.Pin(25, machine.Pin.IN, machine.Pin.PULL_UP)
print("RAIN unconnected reads:", p.value())  # 期望: 1
# 邏輯轉譯：raw=1 → raining=False → 不觸發降低門檻 ✓

# HIGH_WATER_PIN=26, active_low=False → 應該 PULL_DOWN → 空腳讀 0
p = machine.Pin(26, machine.Pin.IN, machine.Pin.PULL_DOWN)
print("HIGH_WATER unconnected reads:", p.value())  # 期望: 0
# 邏輯轉譯：active_low=False 且 raw=0 → high_water=False → 不啟泵 ✓
```

### A.3 判讀

| 結果 | 動作 |
|---|---|
| 三顆都回傳期望值 | 打勾 A 區，進入 A.4 |
| 有腳位讀出不同值 | **停工**。可能：(a) 板子拉電阻硬體損壞，(b) 該腳位被 ESP32 內部連接到別的功能，(c) 焊接短路。用三用電表量該腳位對 3.3V / GND 的電壓，正常拉起應該 ~3.3V，正常拉下應該 ~0V |

### A.4 用三用電表複驗（強烈建議）

在 A.2 執行完後、電源仍連著時：
- FLOAT (32) 對 GND 應該讀 ~3.3V（拉高）
- RAIN (25) 對 GND 應該讀 ~3.3V（拉高）
- HIGH_WATER (26) 對 GND 應該讀 ~0V（拉低）

**若電壓與旗標不符，一定是 `*_ACTIVE_LOW` 設錯了**。修 `edge_pump/config.py:72-74` 後從 A.1 重來。

> **背景**：`sensors.py:97-103` 用 `pull = PULL_UP if config[al_key] else PULL_DOWN` 讓拉電阻方向自動跟隨 `active_low` 旗標。因此設錯旗標會同時反轉「讀值判讀」與「拉電阻方向」，故障後果會加倍，這也是為什麼 §6 一定要台架驗證。

---

## 3. Section B — 感測器實體斷言

**目的**：接上真實感測器，強制製造已知狀態，驗證邏輯讀值符合預期。

### B.1 浮球開關（`FLOAT_PIN=32`）

接線：浮球一端接 GPIO 32，另一端接 GND。

```python
import machine
p = machine.Pin(32, machine.Pin.IN, machine.Pin.PULL_UP)

# 狀態 1：浮球下垂（水位低於浮球）— 這是「乾」的危險狀態
print("Float DRY (float hanging down):", p.value())
# 期望 raw=0（開關閉合到 GND，短路拉低）
# 邏輯：active_low=True 且 raw=0 → float_dry=True（危險）✓

# 狀態 2：浮球被推起（水位淹過浮球）— 這是「安全」狀態
# 手動把浮球往上撥，或倒水淹過
print("Float SAFE (float raised):", p.value())
# 期望 raw=1（開關打開，PULL_UP 讓腳位讀高）
# 邏輯：raw=1 → float_dry=False → 安全 ✓
```

**若讀值反過來**（例如浮球下垂讀 1、被推起讀 0）→ 你的浮球是常閉型（NC），要把 `FLOAT_ACTIVE_LOW` 翻為 `False`。

### B.2 雨水感測模組（`RAIN_PIN=25`）

雨水模組 DO 通常內建 LM393 比較器：乾 → DO=HIGH，濕 → DO=LOW。

```python
import machine
p = machine.Pin(25, machine.Pin.IN, machine.Pin.PULL_UP)

# 狀態 1：感應板乾燥
print("Rain DRY:", p.value())  # 期望 raw=1

# 狀態 2：滴幾滴水到感應板
print("Rain WET:", p.value())  # 期望 raw=0
```

**若讀值反過來** → 檢查模組跳線（部分模組可切換 DO 極性），或翻 `RAIN_ACTIVE_LOW`。

### B.3 高水位感測器（`HIGH_WATER_PIN=26`，選用）

若你的高水位是常開觸點：低水位 → 開路 → PULL_DOWN 讀 0 → high_water=False；高水位 → 短路到 3.3V → 讀 1 → high_water=True。

```python
import machine
p = machine.Pin(26, machine.Pin.IN, machine.Pin.PULL_DOWN)

print("High water NOT triggered:", p.value())  # 期望 raw=0
# 觸發高水位
print("High water triggered:", p.value())      # 期望 raw=1
```

---

## 4. Section C — 類比 ADC 校準

**目的**：驗證 `analog_to_level()` 的線性反轉（`sensors.py:10-19`）符合實際感測器特性；記錄空/滿刻度。

### C.1 讀原始 ADC

```python
import machine
adc = machine.ADC(machine.Pin(34))
adc.atten(machine.ADC.ATTN_11DB)    # 0~3.3V 範圍
adc.width(machine.ADC.WIDTH_12BIT)  # 0~4095

# 連續讀 10 次取平均
readings = [adc.read() for _ in range(10)]
print("raw samples:", readings)
print("mean:", sum(readings) / len(readings))

# 對照 sensors.py:14 的轉換公式
raw = sum(readings) / len(readings)
level = 100.0 - (raw / 4095.0) * 100.0
print("level_pct:", max(0.0, min(100.0, level)))
```

### C.2 三點校準

在三種已知水位下讀 raw + level：

| 水位狀態 | 期望 level_pct | 允收 raw 範圍 | 動作 |
|---|---|---|---|
| 完全空 | 0–5 | 3800–4095 | 記錄實測 raw |
| 半滿（人工控制到目視 50%） | 40–60 | 1650–2450 | 若偏離 ±15，感測器非線性 → 考慮加分段查表或換感測器 |
| 完全滿 | 95–100 | 0–200 | 記錄實測 raw |

### C.3 若讀值反過來

`analog_to_level` 是 `100 - (raw / 4095) * 100`（`sensors.py:14`），假設「raw 大 = 低水位 = 電壓高」。若你的感測器邏輯相反（raw 大 = 高水位），有兩個修法：
- (a) **硬體反轉**：把感測器輸出反接（電源/訊號對調）
- (b) **軟體反轉**：改 `sensors.py:14` 為 `level = (median / 4095.0) * 100.0`（去掉 `100.0 -`）

**優先 (a)**：軟體反轉會影響 `test_control_logic.py` 的所有測試對水位方向的假設，較高風險。

### C.4 記錄校準結果

把 C.2 三點 raw + level 記到台架筆記，塞進節點的部署筆記（`docs/deployment/edge-pump-esp32.md` 之後可增補 "驗證後刻度" 章節）。若不同節點感測器差異大，未來可考慮把校準點寫到 `config.py`。

---

## 5. Section D — 啟用旗標翻轉 + 重新燒錄

**只有 A / B / C 全部打勾，才進這一步。**

### D.1 修改 `edge_pump/config.py`

```python
# 原本
FLOAT_ENABLED = False         # enable ONLY after §6 bench commissioning
RAIN_ENABLED = False          # enable ONLY after §6 bench commissioning

# 翻轉為
FLOAT_ENABLED = True
RAIN_ENABLED = True
# HIGH_WATER_ENABLED 若你的節點沒接該感測器就保持 False
```

**若你在 Section B 有翻極性**：也把對應的 `*_ACTIVE_LOW` 一起改，並在檔案裡加註解記錄「台架驗證後翻轉，20YY-MM-DD by <name>」。

### D.2 重新燒錄

依照 `docs/deployment/edge-pump-esp32.md` 步驟 3 執行一鍵燒錄腳本，或用 `mpremote cp config.py :` 只更新 config 檔（快得多）：

```bash
mpremote connect auto cp edge_pump/config.py :config.py
mpremote connect auto reset
```

### D.3 開機自檢

觀察 REPL 或 serial log：

```
[MAIN] SDPRS Pump Node starting (merged firmware)...
```

**沒有** `Init failed, resetting` → 引腳都成功建立 → 進入主迴圈。若印出這行 → 通常是某個 GPIO 腳位被拉電阻+輸入模式失敗，回頭複驗接線。

### D.4 觀察十秒的推送

`PUBLISH_INTERVAL=10` 秒（`config.py:47`），第一個 status payload 應該在 ~10 秒內上到 MQTT。用 mosquitto_sub 或儀表板 Status 頁監看：

```bash
mosquitto_sub -h <broker-ip> -u <user> -P <pw> -t 'sdprs/edge/+/pump_status' -v
```

payload 應該包含 `float_dry`、`raining` 欄位（先前 `False` 時這兩個是 `None`）。

---

## 6. Section E — 濕式整合測試

**只有 D 完成、儀表板看得到節點才進行。**

### E.1 接繼電器 + 抽水機

**警告**：抽水機通電後任何邏輯錯誤都可能造成損壞或漏水。先做「假負載」測試——用一顆 LED + 電阻代替抽水機，確認繼電器 ON/OFF 節奏正確。

### E.2 假負載五題

| # | 動作 | 期望 |
|---|---|---|
| 1 | 感測器維持 dry / 水位 0% | LED（假泵）保持 OFF，reason=STANDBY |
| 2 | 慢慢加水到 > 80% | LED ON，reason=HYSTERESIS_ON |
| 3 | 邊加水邊灑水在雨感 | reason=RAIN_TRIGGER，門檻降到 60% 就會 ON |
| 4 | 降水位到 < 20% 並維持 30 秒 | LED OFF，reason=STANDBY |
| 5 | 強制 float_dry=True（把浮球撬下）超過 debounce | LED OFF，reason=DRY_RUN_OFF，`flags.dry_run_protect=True` |

**每一題都要看到 MQTT payload 的 reason 欄位變化**——這是驗證 `control_logic.decide()` 走的哪一條分支。

### E.3 真泵接上

假負載五題全過後，把繼電器換接真抽水機，重跑第 1、2、4 題各一次，觀察泵確實有動、確實會停。

第 5 題（DRY_RUN_OFF）不要用真泵驗證——那是安全機制，應該永遠不會在正常運作中觸發；台架已在假負載驗過即可。

### E.4 儀表板端到端

打開 `/dashboard` → Pumps 頁。應該看到：
- 節點 ONLINE（綠點）
- Water level 數字更新（每 10 秒）
- Reason chip 顯示當前狀態
- Rain / Float / Conflict 指示器對得上

---

## 7. Section F — 簽收與回滾

### F.1 簽收條件（全部要打勾）

- [ ] A 拉電阻乾檢 3/3 通過
- [ ] B 感測器斷言 通過（每顆有裝的都通過）
- [ ] C 三點 ADC 校準通過，實測 raw 記錄在案
- [ ] D 節點重啟後 MQTT 上線，float_dry / raining 欄位有值
- [ ] E 假負載 5 題 + 真泵 3 題全過
- [ ] 儀表板端到端一致
- [ ] `config.py` 的改動已 commit（訊息含節點 ID + 校準日期）

### F.2 若中途失敗需要回滾

**任何一題失敗都停在該題，不要繼續。**

回滾程序：
1. `git checkout edge_pump/config.py` — 還原到 `FLOAT_ENABLED=False`, `RAIN_ENABLED=False`
2. 重新燒錄
3. 節點回到純類比模式，安全出廠狀態
4. 修理硬體/接線問題後，從 Section A 重來

**不要**在 A/B/C 沒全過的情況下強行進 D。極性錯誤的斷線會讓 `float_dry` 永遠讀 True，觸發 `CONFLICT_LATCH_OFF` 讓抽水機永遠不動，或更糟：讀 False 遮蔽真正的乾抽危險。

### F.3 簽收後的下一步

- 更新此節點在 `MEMORY.md` 的部署紀錄
- 若這是首個完成的節點，關掉 `PROGRESS.md §9` 的 (b) 項；若是最後一個節點，也可考慮把 `todo_hardware_day.md` 的 (b) 項改為 DONE
- 若量產多節點，考慮把 Section C 的三點校準流程包成一個 `bench_calibrate.py` REPL script 塞在 `edge_pump/scripts/`（下一次工作）

---

## 附錄 A — 引腳與旗標速查

> **Pin map 更新（2026-07-19 bench build）**：RAIN/HIGH_WATER/RELAY 已依實際接線重編。舊配置（RAIN=33、HIGH_WATER=13、RELAY=26）已作廢；本表反映 commit `6173c3c` 之後的預設。

| 感測器 | Pin | 旗標 | 目前預設 | 拉電阻方向 |
|---|---|---|---|---|
| 類比水位 | GPIO 34 (ADC1_CH6) | `LEVEL_ENABLED=False` | **停用**（未接類比探頭） | — |
| 浮球開關 | GPIO 32 | `FLOAT_ENABLED=True` | 啟用（極性待 §A/§B 驗證） | PULL_UP（因 `FLOAT_ACTIVE_LOW=True`） |
| 雨水模組 | GPIO 25 | `RAIN_ENABLED=True` | 啟用（極性待 §A/§B 驗證） | PULL_UP（因 `RAIN_ACTIVE_LOW=True`） |
| 高水位 | GPIO 26 | `HIGH_WATER_ENABLED=True` | 啟用（極性待 §A/§B 驗證） | PULL_DOWN（因 `HIGH_WATER_ACTIVE_LOW=False`） |
| 繼電器 | GPIO 33 | — | 輸出 | — |
| 紅 LED | GPIO 27 | — | 輸出 | — |
| 綠 LED | GPIO 14 | — | 輸出 | — |
| 電池 ADC | `None`（待接） | `BATTERY_ADC_PIN` | 停用 | — |
| 電源來源 | `None`（待接） | `POWER_SOURCE_PIN` | 停用 | — |

拉電阻方向由 `sensors.py:101` 的 `PULL_UP if config[al_key] else PULL_DOWN` 決定，永遠拉向「非觸發」方向以確保單一斷線安全。

## 附錄 B — 決策層速查

`control_logic.decide()` 依序評估 5 層（`control_logic.py:65-189`）：

1. **Conflict holdoff**：先前的觀察衝突鎖定 → 只要 `float_dry` 與 `wet_votes>=2` 同時真，抽水機永遠 OFF 直到重新一致
2. **Conflict burst**：短暫衝突時抽 60s / 休 30s，最多 15 分鐘後鎖定 OFF
3. **Dry-run protect**：`float_dry=True` 直接 OFF（安全硬鎖）
4. **Max-runtime rest**：連續 ON 10 分鐘後強制休 60 秒
5. **Hysteresis**：水位 >= 80%（或雨中 60%）ON，<= 20% 連續 30 秒後 OFF

**Section E 的每一題對應到哪一層都要能說明**——不然表示你不確定測到的是哪個分支。

## 附錄 C — 快速故障樹

| 症狀 | 可能原因 | 檢查 |
|---|---|---|
| Section A 有腳位讀值反過來 | `*_ACTIVE_LOW` 旗標設反 | 用電表量拉電阻電壓；改 `config.py:72-74` |
| Section B 邏輯狀態反過來 | 感測器是 NC 型 或 模組 DO 極性可切換 | 翻 `*_ACTIVE_LOW`；重跑 A + B |
| Section C 水位讀反 | 感測器輸出斜率相反 | 優先硬體反接電源/訊號；不得已才改 `sensors.py:14` |
| Section D 節點重啟後拒連 MQTT | 燒錄未寫入 `config.py`，或 SSID/broker 有改動 | REPL 檢查 `config.SSID` 值 |
| Section E 假負載題 5 沒觸發 DRY_RUN_OFF | debounce 未達 `DEBOUNCE_MS=2500`，或 float 極性錯 | 確認拆開浮球至少 3 秒後再看 payload |
| E.3 真泵不動 | 繼電器接錯（NO / NC 反）或抽水機無電 | 用電表量繼電器 COM–NO 導通 |
