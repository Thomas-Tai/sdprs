# 天氣資料多來源決策 · Weather Multi-Source Decision

> **Status**: 待決策 · Awaiting decision — proposal for review
> **Author**: 工程 · Engineering
> **Date**: 2026-07-19
> **Context**: 呼應 2026-07-19 天氣板塊「不能用」bug 修復（commit `f94c57e` + `61d2e31`），這是後續 hardening 方案討論。

---

## 目前實作狀態 · Current implementation

**系統已經支援多來源**（並非單一資料源），但可能未達到你期望的深度。

The system ALREADY supports multiple weather sources with a fallback chain. Current architecture (`central_server/services/weather_service.py`):

| Layer | 資料源 · Source | 用途 · Purpose | API Key | 地理範圍 |
|---|---|---|---|---|
| **Primary** | 澳門地球物理暨氣象局 SMG | 即時觀測（風速、雨量、氣溫、濕度、風向） | ❌ 免費 | 澳門 |
| **Fallback / Forecast** | Open-Meteo | 當前氣象 + 36 小時預報 | ❌ 免費 | 全球 |
| **Optional** | 台灣中央氣象署 CWA | 熱帶氣旋警報 | ✅ 需 `CWA_API_KEY` | 台灣為主 |

### 目前的容錯路徑 · Current failover behavior

```
SMG XML 抓取
├── 成功 → 用 SMG 資料，來源標示 "SMG"
└── 失敗 → Open-Meteo（若有經緯度）
    ├── 成功 → 用 Open-Meteo，來源標示 "Open-Meteo"
    └── 失敗 → cache 保留上次成功資料 + is_stale 旗標

Forecast 一律從 Open-Meteo（SMG 沒有預報 API）

Typhoon 從 CWA（若設定 API Key）
```

### 剛剛（2026-07-19）修好的問題

1. **SMG 解析錯誤**（commit `f94c57e`）— 所有讀值傳回 0（0°C、0 km/h、0% 濕度）— 因為解析器沒有下探到 `<Value>` 子元素。已加 5 個 regression 測試。
2. **預設經緯度**（commit `61d2e31`）— 預設 24.967/121.541（台灣）與 SMG 澳門主源不對齊；未設定使用者位置時，Open-Meteo 預報完全不抓。已改預設為澳門 22.19/113.55 + 回退到 settings 值。

**部署後（Zeabur ~1-2 分鐘）預期效果：**
- 儀表板頂部 4 個大方框（風速 / 雨量 / 雷擊 / 環境）都顯示真實數值
- 36 小時預報柱狀圖有資料
- 只有雷擊卡片仍顯示 "—"（後端 SMG/Open-Meteo 都沒有雷擊即時資料源）

---

## 為什麼還想加更多來源 · Why add more sources?

**冗餘 · Redundancy**：任何單一資料源都會 down time（SMG 曾在颱風時因基礎設施影響斷線 30+ 分鐘）。
**地理精度 · Locality**：SMG 只有澳門觀測站，如果部署在其他地區（香港、台灣、中國大陸），SMG 的資料與現場天氣脫節。
**共識 · Consensus**：兩個來源同時報「暴雨即將來臨」比單一來源可信度高。
**專用資料 · Specialized data**：雷擊定位、雷達降水、能見度、紫外線指數等，不同來源各有強項。

---

## 候選新增資料源 · Candidate sources

### 🟢 免費、免 API Key、地理相關

| 資料源 · Source | 覆蓋 · Coverage | 資料類型 · Data types | 推薦度 · Priority | 備註 · Notes |
|---|---|---|---|---|
| **HKO 香港天文台** | 香港 | 即時觀測 + 預報 + 雷擊定位 + 雷達 + 熱帶氣旋警報 | ⭐⭐⭐ | 與澳門地理相鄰，強烈建議；`data.weather.gov.hk` XML/JSON 皆可 |
| **7Timer!** | 全球 | 3 小時解析度預報 | ⭐ | 學術用途；準確度不及 Open-Meteo |
| **wttr.in** | 全球 | 文字格式預報 | — | 主要供 CLI 用；不適合程式化整合 |
| **NOAA/NWS** | 美國本土 | 完整氣象資料 | — | 本專案地理範圍不涵蓋 |

### 🟡 免費但需註冊 API Key

| 資料源 · Source | 免費額度 · Free tier | 資料類型 · Data types | 推薦度 · Priority | 備註 · Notes |
|---|---|---|---|---|
| **OpenWeatherMap** | 60 calls/min, 1M/month | 全球即時 + 預報 + 歷史 + 雷達 | ⭐⭐ | 最普遍；資料涵蓋完整；有免費雷達 tile |
| **WeatherAPI.com** | 1M calls/month | 全球即時 + 預報 + 空氣品質 + 天文 | ⭐⭐ | 免費額度最寬，適合作為次要 |
| **Weatherbit** | 50 calls/day | 全球即時 + 預報 | — | 免費額度太少，僅緊急備援 |
| **AccuWeather** | 50 calls/day | 全球即時 + 生活指數 | — | 免費限制嚴格 |
| **Tomorrow.io** | 500 calls/day | 全球 + 專有「hyperlocal」預報 | ⭐ | 適合高精度需求 |

### 🔵 政府 / 地區專用

| 資料源 · Source | 覆蓋 · Coverage | 開放程度 · Openness | 備註 · Notes |
|---|---|---|---|
| **CMA 中國氣象局** | 中國大陸 | API 接入需申請；資料量豐富 | 若部署對象包含華南地區可考慮 |
| **JMA 日本氣象廳** | 日本本土 | 部分開放；文檔以日文為主 | 本專案地理不相關 |
| **BOM 澳洲氣象局** | 澳洲 | 免費 XML | 本專案地理不相關 |
| **KMA 韓國氣象廳** | 韓國 | 需申請 API Key | 本專案地理不相關 |

### 📡 專用雷擊資料源

儀表板目前的「雷擊」卡片一直顯示「—」，因為 SMG/Open-Meteo 都沒有雷擊資料。可以考慮：

| 來源 | 說明 | 成本 |
|---|---|---|
| **Blitzortung.org** | 社群眾包雷擊定位；亞洲覆蓋良好 | 免費（研究用途 attribution 要求） |
| **HKO 雷暴警告** | 直接讀 HKO API 的雷暴警告訊息 | 免費 |
| **AEM Lightning API** | 專業精準；準確度高 | 付費 |

---

## 設計選項 · Design options

### 選項 A · Priority failover chain（擴充現況）

現況已是這個模式（SMG → Open-Meteo）。擴充成：

```
SMG (Macau) → HKO (HK, adjacent) → Open-Meteo (global) → cache
```

**優點**：實作簡單（現有架構直接擴充），維運成本低。
**缺點**：只有一個「主」來源顯示，其他資料源純備援；不利用「共識」邏輯。

**工作量估計**：+150 行（新增 `_fetch_hko_current()` + 測試 + failover 邏輯）

### 選項 B · Consensus / aggregation

同時從多個來源抓，交叉比對：
- 若 3 源都報「風速 > 30 km/h」→ 高信心度警戒
- 若只有 1 源報 → 標示「單源、待確認」
- 顯示每個欄位的來源分佈

**優點**：資料可信度高，防止單源錯誤誤觸警報。
**缺點**：實作複雜（需 conflict-resolution 邏輯）；抓取次數 ×N。

**工作量估計**：+400-600 行（新資料模型 + aggregation + UI 顯示信心度）

### 選項 C · User-selectable primary

儀表板設定頁加下拉選單，操作員可指定「主要來源」（SMG / HKO / Open-Meteo / OpenWeatherMap...）。

**優點**：不同部署場景（澳門 / 香港 / 台北）可自訂。
**缺點**：需 UI + 儲存 + 動態切換邏輯。

**工作量估計**：+250 行（設定 UI + 動態切換 + 每個支援的來源）

### 選項 D · 補齊缺漏欄位

不加新的「主資料源」，而是針對目前顯示「—」的欄位（雷擊、氣壓、能見度）補上專門資料源：
- 雷擊 → Blitzortung 或 HKO 雷暴警告
- 氣壓 → SMG XML 現有的 `MeanSeaLevelPressure`（已有資料，解析器沒讀）
- 能見度 → Open-Meteo 已有欄位（未串接）

**優點**：以現有架構為基礎，最小改動；每個欄位獨立進度。
**缺點**：不改善整體冗餘度。

**工作量估計**：+80-120 行（每欄位獨立）

---

## 工程建議 · Engineering recommendation

**短期（本週可上）：先做選項 D**
- 已知 SMG XML 有氣壓資料但解析器沒讀 → 補上，`pressure hPa` 立即有真實數值
- Open-Meteo current 已回傳的欄位（如 apparent temperature, visibility, cloud cover）可直接串接
- 這些是 "低垂的果實" — 資料源沒問題，只是 SPA 沒接
- **不需新增任何外部依賴或 API Key**

**中期（下個 sprint）：選項 A 加 HKO**
- HKO 免費、免 API Key、地理與澳門相鄰
- 現有 SMG → Open-Meteo failover 直接改成 SMG → HKO → Open-Meteo
- 增加冗餘度不改變架構
- 若 SMG 因颱風斷線，HKO 通常仍可用（不同基礎設施）

**長期（若需求真的存在）：選項 C**
- 只在確認會有非澳門部署（香港、台北）才考慮
- 教學/演示用途下，選項 A + D 應該已足夠

**不建議**：選項 B（consensus/aggregation）— 對災防系統而言，「哪個來源」比「多來源共識」重要；操作員需要清楚知道現在看的是誰的資料。共識邏輯會製造「哪個是真相」的模糊性。

---

## 待決策問題 · Questions requiring owner sign-off

1. **哪個方向優先** —
   - [ ] **D. 補齊缺漏欄位**（本週可上；氣壓、能見度、雷擊）
   - [ ] **A. 擴充 failover chain**（加 HKO；提升冗餘）
   - [ ] **C. User-selectable primary**（跨地區部署才需要）
   - [ ] **維持現況**（parser bug 已修，先觀察一週再決定）

2. 若選 A（加 HKO）：**如何處理來源顯示**？
   - [ ] 儀表板頂部顯示「來源：SMG」/「來源：HKO」，讓操作員知道是哪個
   - [ ] 只在來源切換（primary → fallback）時彈訊息
   - [ ] 完全不顯示，只在 log

3. 若選 D（補雷擊資料）：**選哪個雷擊來源**？
   - [ ] Blitzortung（社群眾包，免費，需要 attribution）
   - [ ] HKO 雷暴警告（區域粗略，免費）
   - [ ] 直接付費 API（精準但成本）
   - [ ] 不做，雷擊卡片保留「—」直到有需求

4. 是否要在後端 log 記錄每個 tick 的來源與延遲，供未來調校？
   - [ ] 是（現在 log 只記錯誤，不記成功；加成功記錄要考慮 log volume）
   - [ ] 否

---

## 決策紀錄 · Decision log (fill in after approval)

- **決策日期 / Decision date**: __________
- **決策者 / Decided by**: __________
- **選擇方向 / Chosen direction**: [ ] A  [ ] C  [ ] D  [ ] 維持現況
- **後續動作 / Follow-up actions**: __________

---

# 已選定方案 · Post-decision Design

> **決策方向**：組合 **A + C + D** — 加入 HKO 香港天文台、每欄位標示來源、使用者可從 SMG 站台清單自選、設定經緯度時可選 HKO 或 Open-Meteo 為備援。
> **決策時間**：2026-07-19（用戶回饋確認）

## 三個資料源分工 · Source responsibility matrix

| 欄位 · Field | 主要來源 · Primary | 備援 · Fallback (若主源失敗) | UI 標示 · Display label |
|---|---|---|---|
| 當前氣溫 · Temperature | SMG 選定站台 | 使用者選 HKO 站台 or Open-Meteo | `SMG 外港` / `HKO Central` / `Open-Meteo` |
| 濕度 · Humidity | SMG 選定站台 | HKO Observatory / Open-Meteo | 同上 |
| 風速 · Wind speed | SMG 選定站台 | Open-Meteo（HKO `rhrread` 無風速） | 同上 |
| 風向 · Wind direction | SMG 選定站台 | Open-Meteo | 同上 |
| 陣風 · Wind gust | SMG 選定站台 | Open-Meteo | 同上 |
| 雨量 · Rainfall | SMG 選定站台 | HKO 選定 district / Open-Meteo | 同上 |
| 氣壓 · Pressure | SMG（有 `MeanSeaLevelPressure` 元素） | Open-Meteo | 同上 |
| 熱帶氣旋 · Typhoon | HKO 警報（`warningMessage` 有 TC1..10） / CWA（若有 API Key） | — | 兩者皆顯示，來源明確標示 |
| 雷擊 · Lightning | HKO 雷暴警告訊息（`warningMessage` 含 "Thunderstorm"） | 無資料則顯示「—」 | `HKO 雷暴警告` |
| 36 小時預報 · Forecast | Open-Meteo（唯一有結構化 hourly forecast 的免費源） | 無 fallback | `Open-Meteo` |

**每欄位獨立顯示來源**（Option D 的核心）：即使 SMG 站台正常，若某欄位在該站台缺資料（例如大橋站台無溫度），系統從 fallback 補上並清楚顯示「Temperature 來自 HKO Ta Kwu Ling」而非默默混用。

## 使用者設定 UI · Configuration UI

天氣設定頁 (`/settings` 內) 加入下列欄位：

```
┌─ 天氣資料設定 ────────────────────────────────────────────┐
│                                                              │
│  SMG 澳門觀測站  ┌──────────────────┐                       │
│                  │ 外港 (Outer Har.)▼│  (下拉，抓 SMG XML  │
│                  └──────────────────┘   全部站台動態列)     │
│                                                              │
│  備援資料源      ○ HKO 香港天文台                            │
│                  ● Open-Meteo（預設）                         │
│                  ○ 兩者皆抓（雙備援）                        │
│                                                              │
│  HKO 站台        ┌──────────────────┐                       │
│  （若選 HKO）    │ Hong Kong Obs.  ▼│                       │
│                  └──────────────────┘                       │
│                                                              │
│  站台經緯度      緯度 [22.19    ] 經度 [113.55   ]           │
│  （Open-Meteo    （若無 SMG/HKO 資料時用；也影響颱風距離）  │
│    forecast 用） (未填則使用 SITE_LAT/SITE_LON env 值)      │
│                                                              │
│                                            [儲存]           │
└──────────────────────────────────────────────────────────────┘
```

## API 變更 · API changes

新增：
- `GET /api/weather/sources` — 回傳目前設定 + 每個可用來源的健康狀態
- `GET /api/weather/smg/stations` — 動態抓 SMG XML 現有站台清單（下拉用；每次刷新確保與 SMG 同步）
- `GET /api/weather/hko/stations` — HKO temperature 站台清單（26 站）

修改：
- `GET /api/weather/current` — 回傳結構加入 `sources` 欄位：
  ```json
  {
    "temperature_c": 29.0,
    "humidity_pct": 83,
    "wind_speed_ms": 2.78,
    ...
    "sources": {
      "temperature": "SMG 外港",
      "humidity": "SMG 外港",
      "wind_speed": "SMG 外港",
      "rainfall_24h": "SMG 外港",
      "pressure": "Open-Meteo (Macau 22.19,113.55)"
    }
  }
  ```
- `PUT /api/weather/config` — payload 增加 `smg_station` + `fallback_provider` + `hko_station` 欄位

## 資料庫 schema 變更 · DB migration

`weather_config` 表加 3 個欄位：

```sql
ALTER TABLE weather_config ADD COLUMN smg_station TEXT DEFAULT '外港';
ALTER TABLE weather_config ADD COLUMN fallback_provider TEXT DEFAULT 'openmeteo';
ALTER TABLE weather_config ADD COLUMN hko_station TEXT DEFAULT 'Hong Kong Observatory';
```

## 實作階段拆解 · Implementation phases

拆成 3 個獨立 PR，每個都可獨立部署 + 觀察：

### Phase 1 · Backend: 加入 HKO fetcher + per-field source
- 新 `_fetch_hko_current(client, station)` 於 `weather_service.py`
- `CurrentWeather` dataclass 加 `sources: Dict[str, str]` 欄位
- `_tick()` 依 config 分派 SMG/HKO/Open-Meteo；merge 結果時記錄每欄位來源
- 5+ 個 pytest 測試（HKO 解析、fallback 邏輯、per-field source assignment）
- **工作量估**：~300 行

### Phase 2 · Backend: 設定 API + DB migration
- `weather_config` schema migration（3 新欄位）
- `GET /api/weather/smg/stations` 動態抓 SMG XML 列站台
- `GET /api/weather/hko/stations` 列 HKO temperature 站台
- `PUT /api/weather/config` 接受新欄位 + validation（來源合法值）
- **工作量估**：~200 行

### Phase 3 · SPA: 設定頁 UI + 資料頁 per-field source 顯示
- 天氣設定 UI（4 個下拉/選項）
- `pages/weather.jsx` 每張 tile 底部顯示來源小字（如 `SMG 外港 · 2s ago`）
- 首次載入呼叫 `/api/weather/sources` 顯示健康狀態
- **工作量估**：~250 行

### 總工作量 · Total
~750 行、3 個 commits、3 個獨立部署 + 觀察週期。

## 待老闆最後確認 · Final checkpoints for owner

1. **實作優先順序** —
   - [ ] 三個 phase 一次 push（一週內完成）
   - [ ] 分階段（Phase 1 先跑一週再看 Phase 2）

2. **雙備援模式**（「兩者皆抓」）—
   - [ ] 有必要（多一層冗餘）
   - [ ] 不需要（增加抓取次數且不常用）

3. **設定 UI 位置** —
   - [ ] `/settings` 頁下方新增「天氣資料設定」區塊
   - [ ] 天氣頁 (`/weather`) 頂部加齒輪 icon 進入設定
   - [ ] 兩處都放

4. **HKO API 使用條款** — HKO Open Data 免費但需在服務內註明資料來源。UI 上是否需要底部 footer 加「資料來源：香港天文台、澳門地球物理暨氣象局、Open-Meteo」？
   - [ ] 需要（免除授權風險）
   - [ ] 已在每張 tile 標示，footer 不需要
