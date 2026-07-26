# Webcam Client 啟動加速 + 保全人員導向 UX 全面稽核設計文件

**日期：** 2026-07-26
**狀態：** 已核准（brainstorming），待撰寫實作計畫
**範圍：** `SDPRS_Webcam.exe` 全面 UI/UX 稽核 → 三階段改造（啟動加速 / 誠實狀態 / 保全導向設定）
**相關：** [[2026-07-21-webcam-client-design]]（原始功能設計，已 SHIPPED）、
[[2026-07-25-webcam-settings-ux-design]]（前一輪，已 SHIPPED）

---

## 0. 概述

本輪對 `SDPRS_Webcam.exe` 做完整 UI/UX 稽核，得到 **6 項啟動問題（S1–S6）** 與
**12 項介面問題（U1–U12）**，並據以規劃三個各自可獨立出貨的階段。

兩項決定性的新資訊改變了設計方向：

1. **啟動慢的真正主因被量測出來了。** 前一輪假設「ffmpeg 約 80 MB」而押注在
   `upx=False`；實測顯示 build 機器 PATH 上的 ffmpeg 是 **227 MB 的 `full` build**，
   佔 onefile payload 的 **55.5%**。這就是 `upx=False` 已上線、啟動仍需 39 秒的原因。
2. **目標使用者是不具技術背景的保全人員**，且**保全人員同時負責安裝設定與日常操作**。
   現行 UI 要求他輸入 `Server URL` 與 `API Key`，失敗時只回報 `伺服器回應：401`，
   執行中唯一的狀態指示是一顆**永遠是綠色**的系統匣圓點。

### 0.1 階段化

三個階段**各自獨立可出貨**，且**各自撰寫一份實作計畫**（本文件只是共同的設計依據）：

| 階段 | 內容 | 相依 |
|---|---|---|
| **Phase 1** | 啟動加速（payload 瘦身、splash、單一執行個體、檔案 logging、圖示） | 無——**不動任何 UI 版面**，可立即開始 |
| **Phase 2** | 誠實的狀態（`StatusHub`、系統匣真實化、通知、狀態視窗） | 依賴 Phase 1 的檔案 logging |
| **Phase 3** | 保全導向設定（單頁引導式精靈、白話字串、掃描加速） | 依賴 Phase 2 的 `strings.py` |

**本輪先執行 Phase 1。** Phase 2、3 的實作計畫待 Phase 1 出貨後再撰寫，以便把
Phase 1 的實測結果（特別是 splash 出現時機，見 §3.1）回饋進後續設計。

---

## 1. 量測基準線（2026-07-26，實測非估計）

### 1.1 啟動耗時

以 `SDPRS_Webcam.exe --check`（解析 import 後結束，**不開 GUI、不開攝影機**）連續量測：

| 次數 | 耗時 | 說明 |
|---|---|---|
| 1 | **60.08 s** | 冷啟動（OS file cache 未暖） |
| 2 | 48.33 s | |
| 3 | **39.42 s** | 暖啟動（cache 已暖） |

此為**下限**——真實啟動還要再加上開攝影機與建立網路連線。

### 1.2 Payload 組成

exe 實際大小 **161,047,800 bytes**；解壓後 **409.4 MB**，每次啟動都要寫入 `%TEMP%\_MEIxxxxx`。
（以 `build/build/PKG-00.toc` 逐檔統計）

| 元件 | 大小 | 佔比 | 可否移除 |
|---|---:|---:|---|
| `ffmpeg.EXE`（full build） | 227.4 MB | 55.5% | **可** → `essentials` 約 85 MB |
| `cv2.pyd` | 74.5 MB | 18.2% | 否（單體模組） |
| `opencv_videoio_ffmpeg4130_64.dll` | 28.6 MB | 7.0% | **可**（僅影片**檔案** I/O 需要） |
| `libscipy_openblas64_*.dll` | 20.4 MB | 5.0% | 嘗試（見 §3.1 風險） |
| `PIL\_avif.pyd` | 7.8 MB | 1.9% | **可** |
| `PYZ-00.pyz` | 9.7 MB | 2.4% | 否 |
| `python314.dll` | 6.8 MB | 1.7% | 否 |
| tcl/tk | 3.8 MB | 0.9% | 否（Tk 需要） |
| 其他（996 檔） | 15.7 MB | 3.8% | 否 |

`opencv_videoio_ffmpeg*.dll` 可移除的依據：本 client 只用
`cv2.VideoCapture(index, CAP_DSHOW)`（即時攝影機擷取）與
`resize` / `imencode` / `cvtColor` / `GaussianBlur` / `absdiff`，
**完全不讀寫影片檔**，故 OpenCV 自帶的 videoio-ffmpeg 後端用不到。

---

## 2. 稽核發現

### 2.1 啟動（S1–S6）

| # | 發現 | 證據 |
|---|---|---|
| **S1** | onefile 每次啟動重新解壓 409 MB 到 `%TEMP%`，Defender 再逐檔掃描。此即 39–60 秒的絕大部分。 | `build.spec:47` EXE(...) onefile 形式；[[project_webcam_dashboard_tile_fix]] 記錄「child 比 parent 晚約 26 秒生成」 |
| **S2** | 內嵌的是 **227 MB 的 ffmpeg `full` build**。 | `build.spec:15` `shutil.which('ffmpeg')` → `ffmpeg-8.1.1-full_build` |
| **S3** | 即使是「已設定完成、不會顯示精靈」的正常啟動，`cv2` + `numpy` 仍在啟動路徑上被 eager import。 | `main.py:8` → `setup_wizard.py:10` → `camera_manager.py:6` |
| **S4** | **啟動期間畫面上完全沒有任何回饋。** `console=False`，系統匣圖示是唯一的生命跡象。使用者面對約 40 秒的空白，最自然的反應是再按一次。 | `build.spec:61`、`main.py:87` |
| **S5** | **無單一執行個體防護。** 與 S4 疊加後，第二次啟動會與第一次搶同一支 DSHOW 裝置。 | `main.py` 全檔無 mutex |
| **S6** | engines 先於系統匣圖示啟動，開攝影機（每支 0.5–2 s）延後了唯一的回饋。 | `main.py:78` 早於 `main.py:87` |

### 2.2 介面（U1–U12）

| # | 發現 | 證據 |
|---|---|---|
| **U1** | `scan_cameras` 無條件探測 index 0–9，每次 miss 在 DSHOW 上約 0.5–2 s → 單攝影機 PC 要卡 10–20 秒「掃描中…」。 | `camera_manager.py:14` |
| **U2** | 精靈中每支攝影機被**開啟兩次**（scan 一次、縮圖一次），且兩者可能與 release 競態。 | `setup_wizard.py:225` + `preview.py:42` |
| **U3** | **「開始」按鈕會凍結視窗**——阻塞式 `httpx.post(timeout=10)` 跑在 Tk 主執行緒上。 | `setup_wizard.py:279` |
| **U4** | **無法單獨驗證連線。** 唯一驗證 URL/Key 的方法是按「開始」，而該動作**同時會註冊攝影機**，故打錯字無法被隔離診斷。 | `setup_wizard.py:258` |
| **U5** | 固定 500×480、`resizable(False, False)`、無捲軸——4 支以上攝影機會溢出且無法捲到。 | `setup_wizard.py:156-157` |
| **U6** | **系統匣狀態燈說謊。** `set_status(True)` 只在啟動時呼叫一次、之後永不更新——伺服器掛掉、金鑰被拒、攝影機拔除，它都是綠的。 | `main.py:88` |
| **U7** | **所有 log 寫進被吞掉的 stdout。** `console=False` 的 exe 中 `basicConfig()` 等於寫到黑洞。保全說「它不動了」時，**沒有任何可供檢查的產物**。 | `main.py:11` |
| **U8** | 精靈無鍵盤支援：無 Enter 送出、無 Esc 取消、無初始焦點。 | `setup_wizard.py:291` |
| **U9** | 無應用程式圖示（`icon=None`）——檔案總管／工作列都是預設圖示。 | `build.spec:67` |
| **U10** | 無「開機自動啟動」選項，但這是 24/7 無人值守的監控 client。 | 全專案無此功能 |
| **U11** | 首次設定按取消 → 靜默結束，不解釋任何原因。 | `main.py:67` |
| **U12** | API Key 欄位遮蔽且無顯示切換——貼上錯誤無從診斷。 | `setup_wizard.py:169` |

---

## 3. 已鎖定的決策

| 項目 | 決策 | 後果 |
|---|---|---|
| 打包 | **維持 onefile，改為瘦身** | 啟動 39 s → 目標 ≤20 s。**不是 ~2 s**——那需要 onedir。 |
| 設定流程 | **單頁引導式**（編號區塊、逐段解鎖） | 一個視窗、強制順序、不可能跳過連線測試 |
| 執行中狀態 | **通知 + 狀態視窗** | 誠實的系統匣 + Windows 快顯通知 + 狀態視窗 |
| 對象 | 保全人員兼任安裝與操作 | 無術語、無狀態碼、每個錯誤都要指出**該做什麼** |

### 3.1 誠實的限制與風險

- **onefile 是啟動時間的硬上限。** 解壓無法被移除，只能讓它少搬 53% 的資料。
  這是使用者在 [[2026-07-25-webcam-settings-ux-design]] 就已明確表達的長期約束，本設計
  不再重新討論。`build.spec` 會保持「切換到 onedir 只需改動極少行」的結構，以備日後改變主意。
- **移除 openblas 有實質風險。** Windows 上 numpy 的 `_multiarray_umath` 在載入時就連結
  BLAS DLL，移除**可能導致 `import numpy` 直接失敗**。故此項列為**選配**：
  實作時嘗試 → 以 `--check` 驗證 → 失敗則保留。省下的 20 MB 不值得賭掉可用性。
- **Splash 出現時機需實測。** PyInstaller 的 splash 需先解壓 tcl/tk 與圖檔才能繪出，
  故「立刻出現」是待驗證的假設而非承諾。驗收標準訂為 **≤3 秒**；
  若實測顯示 splash 出現得太晚而失去意義，退路是改用一支極小的獨立前置 launcher。
  此風險必須在 Phase 1 早期就量測，不可留到最後。

---

## 4. 架構：模組切分

`gui/setup_wizard.py` 目前 300 行，同時處理網路、執行緒、版面與驗證。加上引導式流程與
白話字串後會膨脹到 600 行以上。依「純邏輯與 Tk 分離、純邏輯可無硬體無顯示器測試」切分：

**新增**

| 模組 | 職責 | 可測性 |
|---|---|---|
| `status.py` | `StatusHub`：全 app 健康狀態的單一真相來源。engines / control channel 回報事件，它推導 `(狀態, 白話訊息, 建議動作)`，**僅在狀態轉變時**通知監聽者，並含去抖動。無 Tk、無 pystray。 | 純單元 |
| `strings.py` | 所有面向操作者的繁中字串集中於此 | 純單元 |
| `logging_setup.py` | `RotatingFileHandler` → `%APPDATA%\SDPRSWebcam\logs\` | 純單元 |
| `single_instance.py` | 具名 mutex（`CreateMutexW`） | 純單元 |
| `gui/status_window.py` | 狀態視窗（Tk 呈現層，薄） | 冒煙 |
| `gui/notifier.py` | Windows 快顯通知封裝（`pystray.Icon.notify`） | 以 fake icon 測 |
| `gui/wizard/flow.py` | 引導式區塊狀態機（哪一段解鎖、何者有效）——**純邏輯** | 純單元 |
| `gui/wizard/window.py` | 精靈的 Tk 呈現層（薄） | 冒煙 |
| `gui/wizard/connection.py` | 測試連線 / 註冊，皆在 worker 執行緒，回傳白話結果 | 以 mock httpx 測 |

**修改：** `build.spec`、`main.py`、`camera_manager.py`、`gui/tray_app.py`、
`push_engine.py`、`control_channel.py`

**相依性確認（已實測，無需新增套件）**

- `pystray._win32.Icon`：`HAS_NOTIFICATION = True`、`HAS_MENU = True`、支援預設（雙擊）動作
- `PyInstaller.building.splash.Splash`：可正常 import

---

## 5. Phase 1 — 啟動加速（不動任何 UI 版面）

### 5.1 `build.spec`

1. **ffmpeg 來源改為明確指定，並加上防呆。** 解析順序：
   `SDPRS_FFMPEG` 環境變數 → 專案內 `vendor/ffmpeg.exe` → `shutil.which('ffmpeg')`。
   **若解析到的檔案 > 120 MB，build 時印出醒目警告**（代表又抓到 full build）。
   這把「靜默的體積回歸」轉成 build 期就會被看見的訊號——正是這次沒被發現的那個 bug。
2. **排除二進位：** 從 `a.binaries` 濾掉 `opencv_videoio_ffmpeg*`；
   `excludes` 加入 `PIL._avif` 及未使用的影像外掛。
3. **（選配）** 嘗試排除 `libscipy_openblas64_*`，以 `--check` 驗證；失敗即還原。
4. **`Splash(...)`** 搭配品牌圖，`splash.close()` 在系統匣圖示出現後呼叫。
   執行期 `import pyi_splash` 必須以 `try/except ImportError` 包住（未凍結執行時不存在）。
5. **`icon='assets/sdprs.ico'`**（U9）。
6. 維持 `upx=False`（前一輪的成果，加測試守住）。

### 5.2 `main.py` 啟動順序

```
mutex 取得（失敗 → 顯示「SDPRS 監控已在執行中」並喚起既有視窗後結束）
  → 設定檔案 logging（API key 必須遮蔽）
  → 建立系統匣圖示（S6：先於 engines，讓回饋立刻出現）
  → splash.close()
  → 啟動 engines
```

**onefile 雙 PID 陷阱：** mutex 必須在**子行程**（真正的 app）建立，而非 bootloader。
本專案的程式碼本來就跑在子行程，故自然成立——但此點必須寫進註解，避免日後有人「修正」它。
（見 [[project_webcam_dashboard_tile_fix]]）

### 5.3 驗收標準（可證偽）

| 指標 | 現況 | 目標 | 量測方式 |
|---|---|---|---|
| Payload 解壓大小 | 409.4 MB | **≤ 250 MB**（修訂，見下） | 重跑 TOC 統計腳本 |
| `--check` 暖啟動 | 39.4 s | **≤ 25 s**（修訂，見下） | 同一支 3 次量測 harness |
| Splash 出現 | 不存在 | **≤ 3 s** | 碼錶／螢幕錄影 |
| — | — | — | — |

**目標修訂（2026-07-26，安裝 essentials build 後實測）**

本文件初稿假設 ffmpeg essentials 約 85 MB，據此把 payload 目標訂為 ≤200 MB。
實際安裝 `Gyan.FFmpeg.Essentials` 後量得 **97 MB**，且初稿的減法本身也算錯了
（即使按 85 MB，正確結果也是約 211 MB，不是文中寫的 190 MB）。以實測值重算：

| 項目 | 大小 |
|---|---:|
| 基準 payload | 409.4 MB |
| ffmpeg 227.4 → 97.0 | −130.4 |
| `opencv_videoio_ffmpeg*.dll` | −28.6 |
| `PIL\_avif.pyd` | −7.8 |
| **預估結果** | **242.6 MB** |
| 再加 OpenBLAS（有風險，§3.1） | 222.2 MB |

因此目標修訂為 **payload ≤250 MB、暖啟動 ≤25 s**。
連帶影響：**§5.1.3 的 OpenBLAS 排除從「選配」升為「預期要做」**——它是唯一還能
再砍 20 MB 的項目。仍然保留「一旦 `import numpy` 失敗就立刻還原」的規則：
20 MB 不值得賭掉 client 能否啟動。

**誠實結論：** 在 onefile 前提下，剩餘 payload 的最大宗是 `cv2.pyd`（74.5 MB，
單體模組無法拆）與 ffmpeg（97 MB，除非自行編譯只含 h264+hls 的最小版本）。
啟動時間要再往下壓，就只剩 onedir 一途。**真正解決「使用者盯著空白畫面」的是
splash，不是這 40% 的體積縮減**——體積縮減把痛苦從 39 秒降到 20 幾秒，
splash 則讓那段時間不再像當機。
| 重複啟動 | 兩個行程搶攝影機 | 第二次顯示提示後結束 | 手動 |
| Log 檔 | 不存在 | `%APPDATA%\SDPRSWebcam\logs\webcam.log` 有內容且**不含 API key** | 單元測試 + 手動 |

---

## 6. Phase 2 — 誠實的狀態（`StatusHub`）

### 6.1 狀態模型

```
StartingUp / Running / Paused / NoServer / BadKey / CameraDown
```

每個狀態對應 `strings.py` 中的 `(標題, 說明, 建議動作)` 三元組。**狀態碼永不進入保全視野**——
`401` 照樣寫進 log 檔給技術人員。

| 狀態 | 保全看到 |
|---|---|
| Running | 監控中 — 2 支攝影機運作正常 |
| NoServer | 無法連線到伺服器。請檢查網路連線，或通知管理員。 |
| BadKey | 連線密碼已失效。請通知管理員重新設定密碼。 |
| CameraDown | 前門攝影機沒有畫面。請檢查 USB 線是否鬆脫；若仍無畫面，請通知管理員。 |
| Paused | 已暫停上傳（由操作員手動暫停中）。 |

### 6.2 關鍵行為

- **僅在狀態轉變時通知。** 每次輪詢失敗都跳通知會讓保全直接忽略所有通知。
- **去抖動：** 故障需**持續 30 秒**才升級為通知（網路瞬斷是常態）。
  此值進 `DEFAULTS`（`NOTIFY_DEBOUNCE_SECONDS = 30`）以便日後調整。
  恢復方向**不去抖動**——恢復正常應立即反映，讓保全盡快知道問題已排除。
- **`feat/webcam-auth-error-tray` 分支在此收編**，從「僅 401/403」推廣為全部故障類別。
  該分支的既有測試需一併遷移，不可丟棄。
- 系統匣圖示顏色改由 `StatusHub` 驅動（修正 U6）。
- 狀態視窗提供「開啟記錄」（修正 U7 的最後一哩）與「重新連線」（免重啟重試）。

---

## 7. Phase 3 — 保全導向設定（單頁引導式）

### 7.1 版面

一個視窗、三個編號區塊，逐段解鎖：

1. **連線** — 系統網址 / 連線密碼（附「顯示」切換，修正 U12）+ 「測試連線」按鈕
2. **選擇要監控的攝影機** — **在區塊 1 測試通過前保持鎖定**；通過後區塊 1 收合為綠色 ✓ 摘要列
3. **開始監控**

術語全面白話化：`Server URL` → **系統網址**、`API Key` → **連線密碼**，每欄附說明文字。

### 7.2 一併修正的反應性問題

| 修正 | 作法 |
|---|---|
| U1 掃描慢 | 連續 3 次 miss 即提前結束（保留 `max_index` 上限）。10–20 s → 約 2 s |
| U2 重複開啟攝影機 | `scan_cameras` 掃描時**順便取回該格畫面**並回傳，縮圖直接沿用 → 第二次開啟完全消失 |
| U3 UI 凍結 | 所有網路操作移出 Tk 執行緒，沿用本檔已驗證的 `_safe_after` marshalling 慣用法 |
| U4 無法單獨驗證 | 「測試連線」只驗證身分，**不註冊攝影機** |
| U5 溢出 | 攝影機清單加捲軸 |
| U8 鍵盤 | Enter 送出、Esc 取消、開窗即聚焦第一欄 |
| U11 靜默結束 | 取消時說明後果並提供再次設定的方式 |
| U10 開機啟動 | 「開機時自動啟動」核取方塊（寫入 HKCU `Run`；**僅 HKCU，不需系統管理員權限**） |

---

## 8. 測試策略

遵守本專案既有 CI 陷阱（見 [[env_cloud_path_quirks]]）：**每個測試檔單獨執行，帶
`-p no:cacheprovider`**；新測試一律走 RED→GREEN 並確實證偽。

| 對象 | 測試 |
|---|---|
| `StatusHub` | 狀態推導、**僅轉變時通知**、去抖動計時、執行緒安全 |
| `gui/wizard/flow.py` | 未通過測試連線前區塊 2/3 保持鎖定；通過後解鎖 |
| `strings.py` | **斷言任何面向保全的字串都不含裸狀態碼或 exception repr** |
| `camera_manager.scan_cameras` | 以 fake capture factory 驗證連續 3 次 miss 提前結束、回傳畫面供縮圖使用 |
| `single_instance.py` | 第二次取得失敗 |
| `logging_setup.py` | 檔案輪替；**API key 被遮蔽** |
| `build.spec` | 擴充 `test_packaging.py`：`upx=False`、排除項存在、splash 已設定、icon 已設定 |
| 既有 | `test_setup_wizard.py`（218 行）需大幅改寫；`feat/webcam-auth-error-tray` 的測試需遷移 |

---

## 9. 明確非目標（YAGNI）

不改 onedir；不做配對碼（pairing code）機制；不新增任何 server 端 API；
不做多鏡頭即時預覽面板；不動 DPAPI 憑證儲存機制；不做多語系（僅繁中）；
不做自動更新機制。

---

## 10. 受影響檔案（依階段）

### Phase 1 — 啟動加速
**新增：** `logging_setup.py`、`single_instance.py`、`assets/sdprs.ico`、`assets/splash.png`、
`tests/test_logging_setup.py`、`tests/test_single_instance.py`
**修改：** `build.spec`、`main.py`、`tests/test_packaging.py`

### Phase 2 — 誠實的狀態
**新增：** `status.py`、`strings.py`、`gui/status_window.py`、`gui/notifier.py`、
`tests/test_status.py`、`tests/test_strings.py`、`tests/test_notifier.py`
**修改：** `main.py`、`gui/tray_app.py`、`push_engine.py`、`control_channel.py`、
`tests/test_tray_app.py`
**收編：** `feat/webcam-auth-error-tray` 分支（含其既有測試）

### Phase 3 — 保全導向設定
**新增：** `gui/wizard/{__init__,flow,window,connection}.py`、`tests/test_wizard_flow.py`、
`tests/test_wizard_connection.py`
**修改：** `camera_manager.py`、`main.py`、`strings.py`
**刪除：** `gui/setup_wizard.py`（內容遷入 `gui/wizard/`）、
`tests/test_setup_wizard.py` 改寫為新結構的測試

### 跨階段
`.gitignore` 加入 `.superpowers/`（本輪 brainstorming 的視覺稿存放於此，不應進版控）。

### 素材相依（Phase 1 的前置阻擋項）
`assets/sdprs.ico` 與 `assets/splash.png` **目前不存在，專案內也沒有任何既有 icon**。
這是 Phase 1 唯一的非程式相依。兩種處理方式，實作時擇一：

- **（預設）程式化產生：** 以 Pillow 產生一個樸素但一致的識別圖（SDPRS 字樣 + 純色底），
  隨 repo 進版控。可立即進行，不阻擋任何工作。
- **（若有品牌素材）** 由使用者提供正式 logo，取代上述產出物。

**不可接受的作法：** 為了等素材而延後 Phase 1 的其餘部分——icon 與 splash 圖檔是
可替換的產出物，不應成為啟動加速的阻擋項。

---

## 11. 安全約束（沿用，不得違反）

不得硬編任何憑證；`Msc@2333`、`MSC-Person` 不得出現於任何位置；正式路徑不得出現
`broker.emqx.io`；**不得對 edge 裝置新增 `stream_start` / `stream_stop` 以外的任何指令
下行介面**。

本設計全為 client 端，未觸及以上任一。另新增一項約束：
**API key 不得出現在 log 檔中**（Phase 1 新增檔案 logging 後才成立的新風險，已納入測試）。
