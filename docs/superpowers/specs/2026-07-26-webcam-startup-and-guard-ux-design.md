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
| `libscipy_openblas64_*.dll` | 20.4 MB | 5.0% | **否——已於 2026-07-30 實測並否決，見 §5.3** |
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
- ~~**移除 openblas 有實質風險。**~~ **結案：風險成真，此項永久否決（2026-07-30）。**
  當初的假設（「Windows 上 numpy 的 `_multiarray_umath` 在載入時就連結 BLAS DLL，
  移除可能導致 `import numpy` 直接失敗」）**完全正確**：它是 `_multiarray_umath.pyd`
  的硬性匯入表項目。實測 build 成功、payload 降到 227.1 MB，exe 一啟動就
  `ImportError: DLL load failed while importing _multiarray_umath`。已還原，
  詳見 §5.3。**不要再嘗試。**
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
3. ~~**（選配）** 嘗試排除 `libscipy_openblas64_*`，以 `--check` 驗證；失敗即還原。~~
   **已執行並否決（2026-07-30）：`--check` 直接 `ImportError`，已還原。`buildconfig.py`
   的 `EXCLUDED_BINARIES` 上方留有 `DO NOT ADD` 註解與理由。詳見 §5.3。**
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

| 指標 | 現況 | 目標 | 量測方式 | 實測（2026-07-26，essentials ffmpeg 101.5 MB／101,457,920 bytes，`4fb2e27` 重 build） |
|---|---|---|---|---|
| Payload 解壓大小 | 409.4 MB | **≤ 250 MB**（修訂，見下） | 重跑 TOC 統計腳本 | **247.5 MB — 達標**，但僅剩約 2.5 MB（約 1%）安全邊界，幾乎貼著上限 |
| `--check` 暖啟動 | 39.4 s | **≤ 25 s**（修訂，見下） | 同一支 3 次量測 harness | 50.12 / 18.17 / **18.22 s — 達標**（第 3 次對比 25 s 目標與 39.42 s 基準線） |
| Splash 出現 | 不存在 | **≤ 3 s** | 程式化輪詢：PowerShell `Get-Process`／`Get-CimInstance` 每 100ms 檢查一次 `SDPRS_Webcam.exe` 各行程的 `MainWindowHandle`，非碼錶或螢幕錄影 | **約 0.45–0.49 s（2026-07-26 fix round 1 重新量測，兩次獨立乾淨量測，含 PID/PPID 逐筆記錄）— 達標**，遠低於 3 s 門檻；詳見下方「splash 行程歸屬」說明 |
| — | — | — | — | — |

**Phase 2 重新量測（2026-07-30，branch `feat/webcam-truthful-status` @ `956b90b`）**

Phase 2 動到 `status.py` / `main.py` / `app_controller.py` / `control_channel.py`，
所以重跑 packaging 與啟動量測當回歸檢查。結論：**無回歸**。

| 指標 | Phase 1（2026-07-26） | Phase 2（2026-07-30） | 目標 | 判定 |
|---|---:|---:|---:|---|
| Payload 解壓 | 247.5 MB | **247.5 MB** | ≤250 MB | 達標，數字未動 |
| exe 檔案大小 | — | 95,750,666 bytes | — | — |
| `--check` 暖啟動（收斂值） | 18.22 s | **15.1–16.2 s** | ≤25 s | 達標 |

暖啟動這次跑 **7 次**而非 3 次，因為前 3 次還沒收斂：
`47.35 / 39.16 / 25.11 / 15.34 / 15.11 / 16.15 / 16.15 s`（全部 `exit=0`）。
第 3 次的 25.11 s 若照原本 3 次 harness 就會被記成「剛好超過 ≤25 s 門檻」，
但那是量測污染——該次之前剛用 `cp` 覆寫過 exe 並跑過一個壞 build，
Defender 正在重掃全新 payload。**教訓：3 次 harness 不足以保證收斂；
量測前若動過 exe 檔案本身，要跑到連續兩次持平才算數。**

**目標修訂（2026-07-26，安裝 essentials build 後實測）**

本文件初稿假設 ffmpeg essentials 約 85 MB，據此把 payload 目標訂為 ≤200 MB。
實際安裝 `Gyan.FFmpeg.Essentials` 後量得 **101.5 MB（101,457,920 bytes，十進位 MB，
與 `payload_audit.py` 的 `/1e6` 換算一致；換算成 MiB 約 96.8）**，且初稿的減法本身也算錯了
（即使按 85 MB，正確結果也是約 211 MB，不是文中寫的 190 MB）。以實測值重算：

| 項目 | 大小 |
|---|---:|
| 基準 payload | 409.4 MB |
| ffmpeg 227.4 → 101.5 | −125.9 |
| `opencv_videoio_ffmpeg*.dll` | −28.6 |
| `PIL\_avif.pyd` | −7.8 |
| **預估結果** | **247.1 MB** |
| ~~再加 OpenBLAS（有風險，§3.1）~~ | ~~226.7 MB~~ — **已實測並否決，見下** |

（此欄位下方 §5.3 已記錄的實測值 **247.5 MB** 與此處 247.1 MB 的預估非常接近，
差異在誤差範圍內，可視為互相印證。）

因此目標修訂為 **payload ≤250 MB、暖啟動 ≤25 s**。

**OpenBLAS 排除：已實測，永久否決（2026-07-30）**

上一段曾把「§5.1.3 的 OpenBLAS 排除」從選配升為「預期要做」，理由是它是唯一還能
再砍 20 MB 的項目。**該升級現已撤回——這條路走不通，不要再試。**

證據有兩層，互相印證：

1. **靜態證據（PE import table）**：`libscipy_openblas64_-b78821…dll` 是
   `numpy/_core/_multiarray_umath.cp314-win_amd64.pyd` 的**硬性（非 delay-load）
   匯入表項目**。Windows loader 在任何 Python 程式碼執行之前就必須解析它，
   因此不存在「用不到就不載入」的可能。
2. **動態證據（實際 build + `--check`）**：把 `'libscipy_openblas'` 加進
   `buildconfig.EXCLUDED_BINARIES`，build **成功**（`dropped 2 excluded binaries`），
   payload 降到 **227.1 MB**（與上表預估 226.7 MB 相符），但 exe 一啟動就死：

   ```
   ImportError: DLL load failed while importing _multiarray_umath:
                The specified module could not be found.
   ```

   呼叫鏈是 `app.py` → `webcam_client.main` → `gui/setup_wizard` →
   `camera_manager` → `import cv2` → `import numpy` → 炸。**cv2 硬性依賴 numpy**，
   所以這不是「某個邊緣功能壞掉」，而是**整個 client 無法啟動**。

附帶觀察（bench 值得知道）：這個壞掉的 onefile exe **crash 後 bootloader 行程仍然
駐留並鎖住 `dist/SDPRS_Webcam.exe`**（`cp` 覆蓋時得到 *Device or resource busy*，
`tasklist` 顯示 2 個 PID）。要覆蓋檔案前得先 `taskkill /F /IM SDPRS_Webcam.exe`。

已還原：`EXCLUDED_BINARIES` 回到 `('opencv_videoio_ffmpeg', '_avif')`，並在
`buildconfig.py` 就地留下「DO NOT ADD」註解與理由，讓下一個人在改動點上直接看到，
不必翻文件。還原後的 exe `--check` 連續 7 次全部 `exit=0`。

因此 **247.5 MB 就是這個功能集合下的實際地板**：剩下最大兩項是 ffmpeg 101.5 MB
（已是 essentials build）與 cv2 74.5 MB（OpenCV 本體），兩者都不可能在不砍功能的
前提下再縮。若未來要再降，只剩「換掉 cv2」或「不打包 ffmpeg、改為外部相依」這類
架構決策，不是打包參數微調。

**誠實結論：** 在 onefile 前提下，剩餘 payload 的最大宗是 `cv2.pyd`（74.5 MB，
單體模組無法拆）與 ffmpeg（約 101.5 MB，除非自行編譯只含 h264+hls 的最小版本）。
啟動時間要再往下壓，就只剩 onedir 一途。**真正解決「使用者盯著空白畫面」的是
splash，不是這 40% 的體積縮減**——體積縮減把痛苦從 39 秒降到 20 幾秒，
splash 則讓那段時間不再像當機。

**實測驗證此假設成立，且 2026-07-26 fix round 1 以更嚴謹的方法重新量測並鎖定歸屬。**

第一輪量測（見上方 §5.3 表格首次填入時）僅記錄「偵測到可見視窗」的時間點
（0.86–0.95 秒），但未區分該視窗屬於 onefile 的哪一個行程——bootloader
**parent**（負責解壓＋繪製 splash）或其啟動的真正應用程式 **child**。這個區分
本身就是 §3.1 風險評估的核心，用推論帶過並不足夠。

**Fix round 1 補測（同一份 `dist/SDPRS_Webcam.exe`，未重新 build）：** 改用
`Get-Process`／`Get-CimInstance` 每 100ms 對**每一個** `SDPRS_Webcam.exe` 行程
同時記錄 PID、`ParentProcessId`、`MainWindowHandle`、視窗標題，並以單調時脈
（`Stopwatch`）記錄**自啟動起算的經過時間**——絕對行程啟動時間戳記
（`$p.StartTime`）僅在量測初次嘗試（已判定有 edge-detection 瑕疵、不採信為
證據的那一輪）中記錄過，本節採信的兩次乾淨重跑並未記錄此欄位。
連續兩次乾淨量測，結果完全一致且無歧義：

- 兩次量測中，擁有可見視窗（標題 `'tk'`，PyInstaller splash 樣板從不呼叫
  `wm title` 設定自訂標題，故顯示為 Tk 預設值）的行程，其 `ParentProcessId`
  **不是**另一個 `SDPRS_Webcam.exe` PID，而是啟動它的外部 shell——即該行程本身
  就是 onefile **bootloader parent**。
- 對應的 **child** 行程（`ParentProcessId` 指向上述 parent PID）在 parent 啟動約
  12.5 秒後才**出現在行程清單中**（代表 child 直到此時才被 parent 建立），且
  從出現到量測結束，`MainWindowHandle` 全程為 0——child **從未擁有任何視窗**，
  與其為純系統匣（tray-only）app、無主視窗的設計相符。
- **結論：splash 視窗由 bootloader parent 持有，不是 child。** 這與
  `main.py` 內 `_close_splash()` 呼叫 `pyi_splash.close()`（在 child 行程中執行，
  透過 IPC 通知 parent 關閉其自行持有的 splash 視窗）的架構完全吻合。

**新量測數字（0.45–0.49 秒出現，兩次乾淨重跑一致）比第一輪的 0.86–0.95 秒更快，
兩者不予調和——如實記錄新數字，不回頭修改或平均：**

| 量測輪次 | Splash 出現時間 | Splash 消失時間 | 方法 |
|---|---|---|---|
| 第一輪（本文件首次記錄） | 約 0.86–0.95 s | 約 16.1–16.2 s | 逐一輪詢，未記錄 PPID |
| Fix round 1，run B | 0.451 s | 介於 15.634–15.772 s 之間 | 每 100ms 全量快照，含 PID/PPID/title/經過時間（非行程絕對啟動時間戳記） |
| Fix round 1，run C | 0.493 s | 介於 15.645–15.785 s 之間 | 同上 |

無論採用哪一輪的數字，皆遠低於 3 秒門檻，**達標**的結論不變；消失時間兩輪
落在同一量級（15.6–16.2 秒），出現時間的落差（0.45–0.49 s vs. 0.86–0.95 s）
較可能來自量測迴圈本身的啟動開銷差異（第一輪迴圈在偵測前先做較重的
一次性 `Get-CimInstance` 快取查詢；fix round 1 已將該查詢移出熱路徑並個別
快取），而非 splash 真的隨機變慢——但兩者皆未達到可以完全排除系統負載變異
的程度，故此處誠實地並列兩輪數字，不宣稱哪一個「更正確」。

**Phase 2/3 設計可以放心地假設 S4（啟動期間畫面空白）已由 Phase 1 的 splash
解決**（不需要額外的小型前置 launcher 退路方案），因為無論用哪一輪數字，
「使用者雙擊後多久看到畫面回饋」都遠低於 3 秒，且已確認該回饋（splash）
在整個 onefile 解壓＋Python 匯入期間（parent 行程的生命週期內）持續可見。

| 指標 | 現況 | 目標 | 量測方式 | 實測（2026-07-26） |
|---|---|---|---|---|
| 重複啟動 | 兩個行程搶攝影機 | 第二次顯示提示後結束 | 指令碼化：`Start-Process` 觸發第二次啟動，以 `user32.dll EnumWindows` 確認訊息框視窗存在後，用 `PostMessage(WM_CLOSE)` 程式化關閉以完成量測（非人工雙擊） | **達標** — 第二次啟動彈出「SDPRS 監控已在執行中。」訊息框，關閉後 exit code=0；關閉前後皆確認只有原本一組 parent+child（onefile 兩個 PID）留存，無重複 instance |
| Log 檔 | 不存在 | `%APPDATA%\SDPRSWebcam\logs\webcam.log` 有內容且**不含 API key** | 單元測試 + 指令碼檢查（讀檔＋逐字串比對，非人工翻閱） | **達標** — log 檔存在，43,658 bytes 有內容；已設定 API key，逐字串搜尋未出現於 log 內容中，VERDICT: clean |

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
  實際門檻是 `status.py` 的 `NOTIFY_DEBOUNCE_SECONDS = 30.0`。
  ~~恢復方向**不去抖動**——恢復正常應立即反映，讓保全盡快知道問題已排除。~~
  **已於 2026-07-30 由人的決定推翻（人的決定 5），實作於 `720a1dd`：恢復方向
  同樣去抖動。** 現行行為分兩層，刻意不同步：
  - **系統匣顏色：立即**反映真實狀態（故障轉紅、恢復轉綠都不等待）。顏色是「現在
    的狀態」，不是「事件」。
  - **toast 通知：一個 episode 只講一次。** 故障 toast 在故障累積滿 30 秒時跳一次，
    同一個 episode 內反覆抖動不會再跳；**「監控中」的恢復 toast 也要等健康持續滿
    30 秒**才跳。理由：閃斷會讓保全在幾分鐘內收到成對的「壞了／好了」通知，
    是最快讓人學會無視所有通知的做法。
  - **注意 dwell 是累積制而非連續制**：一個 episode 內的故障時間會加總，
    episode 只在健康持續滿一整個 window 後才結束。因此一支「每 5 秒抖一次」的
    線路仍然會在累積滿 30 秒時被通知，即使它從來沒有連續壞滿 30 秒。
- **`feat/webcam-auth-error-tray` 分支已於稽核中確認全面收編**（稽核明細見 §6.3 與
  `task-7-report.md`）：該分支「僅 401/403 → 單一 bool」的窄範圍設計，已推廣為
  `StatusHub` 的完整故障分類（`BAD_KEY` / `NO_SERVER` / `CAMERA_DOWN`，含 precedence
  與跨 worker 聚合）；該分支既有測試涵蓋的每一種邊界情況，在 Phase 2 都能指出對應的
  等效測試。**分支本身尚未刪除**——是否刪除是人的決定，本次稽核只負責給出稽核結論。
- 系統匣圖示顏色改由 `StatusHub` 驅動（修正 U6）。
- 狀態視窗提供「開啟記錄檔」（修正 U7 的最後一哩）與「重新連線」（免重啟重試）。

### 6.3 實作狀態（2026-07-26 稽核）

**目前狀態：已實作、程式碼審查通過（review-clean）。對真實硬體／真實伺服器的人工
驗證尚未執行**——見 §6.4 的人工驗證清單。在該清單被實際勾選完成之前，本節不可視為
「已驗證上線」，只能記為「已實作、審查乾淨、人工驗證待進行」。

**Commit range：** `c8b501a..aaf76d0`（`feat/webcam-truthful-status` 分支，12 個 commit，
皆為 2026-07-26；由 `docs(webcam): Phase 2 truthful-status implementation plan` 起，至
`fix(webcam): the toast must carry the state that matured, not the state at drain` 止）。
最後兩個 commit（`fe49e5f`、`aaf76d0`）是全分支審查後的修正回合，處理 5 個 Important
發現；其中 3 個是「UI 說謊」的殘留，由審查者以可執行的 probe 重現，不是靠推論。
測試 176 → 211。

**與原設計的刻意偏離**（已決定，不需再討論）：

1. **狀態視窗不逐一列出每支攝影機清單。** 視窗的呼叫介面只帶 `camera_count`（數量）與
   `faulty_names`（故障攝影機名稱），不帶完整清單；故障名稱已經出現在
   `camera_down` 那句說明文字裡，再列一次清單只是重複資訊。
2. **操作端的攝影機名稱清單捨棄 `CONTROL_SOURCE`。** 這個捨棄是無損的：control channel
   只會回報 `NO_SERVER` 或 `BAD_KEY`，兩者在 `StatusHub` 的 precedence 都贏過
   `CAMERA_DOWN`，而 `CAMERA_DOWN` 的文字樣板是唯一會內插攝影機名稱的一個——因此
   不存在任何實際渲染出的句子，會因為少了 `CONTROL_SOURCE` 這個內部識別碼而缺東西。
3. ~~**狀態視窗開啟期間會阻塞 dispatch loop**~~（與既有設定精靈一致的行為）。若去抖動
   計時器在視窗開著的期間到期，會在視窗關閉後的下一個 idle tick 觸發 toast——也就是
   toast 會被延後，但不會遺失。
   **更正紀錄（2026-07-26，全分支審查）：** 這段「不會遺失」在最初寫下時**是錯的**，
   而且是被 probe 實際重現的錯——NOTIFY 佇列 token 不帶狀態，`tick()` 在**觸發時**
   鎖定的是成熟的那個狀態，`_handle_notify` 卻在**取出時**重讀 `hub.state`。結果：
   伺服器斷線 30 秒以上、卻在 dispatch loop 取出前恢復時，保全會看到兩次「監控中」，
   完全不會知道曾經斷過；而另一個新故障會在 0 秒被 toast 出來，直接繞過去抖動。
   已於 `aaf76d0` 修正：狀態改為隨 token 一起傳遞，`_handle_notify` 只 toast 它被交付
   的那個狀態。修正後這句「延後但不遺失」才成立。`_handle_health` 維持重讀 `hub.state`
   ——把重繪合併到最新狀態是刻意且正確的。

   **本項已於 2026-07-30 整項失效（`2bc8cc8`）：狀態視窗不再阻塞 dispatch loop。**
   視窗改為以 250 ms 的 Tk timer 自行更新，dispatch loop 全程照跑，因此上面
   「toast 會被延後」的前提已不存在——視窗開著時 toast 照時間跳。上面整段更正紀錄
   仍然保留，因為它記錄的 bug（token 不帶狀態）與其修正是真實的，而且那個
   「toast 必須攜帶成熟時的狀態」的契約在視窗不再阻塞之後依然要成立：
   `NOTIFY_DEBOUNCE_SECONDS` 到期與 drain 之間永遠存在非零間隔。
   `test_a_matured_notification_is_not_lost_when_recovery_beats_the_drain` 釘住它。
4. **狀態視窗的內容是開啟當下的快照。** 視窗只在開啟時算一次文字，而 dispatch loop
   在視窗關閉前是被擋住的，所以視窗開著時發生的健康狀態變化不會反映在已開啟的視窗上。
   對一個看完就關的暫時性視窗而言可接受，但要知道它不是即時面板。

**去抖動門檻與警告：** `NOTIFY_DEBOUNCE_SECONDS = 30.0`。**若任何一次 toast 是針對持續
時間短於 30 秒的瞬斷觸發，代表去抖動機制本身壞了，必須如實回報，絕不可以調高門檻把
它蓋過去。** 此警告已一併寫入 §6.4 的人工驗證清單。

完整的逐行為稽核表（老分支的每個行為 → Phase 2 對應的 file:line）記錄於
`.superpowers/sdd/2026-07-26-webcam-phase2-truthful-status/task-7-report.md`。

### 6.4 人工驗證清單（尚未執行——待實機測試）

以下每一項都需要真實跑起來的 client（已設定 server_url / api_key）、真實伺服器與
真實攝影機，且需要有人盯著系統匣觀察，因此無法由稽核自動完成，只能列成清單交給
之後在實機前執行的人：

- [ ] 停止伺服器（或拔掉網路）→ 約 30 秒內系統匣圖示轉紅，並跳出 toast「無法連線到
      伺服器」，且訊息含建議動作句。恢復伺服器／網路後 → **圖示應很快轉綠（顏色不
      去抖動），但「監控中」的恢復 toast 要等健康持續滿 30 秒才跳**——兩者刻意不同步。
      （~~原文：「並立即跳出恢復通知（不必等待 30 秒去抖動）」~~ 已由人的決定 5
      於 2026-07-30 推翻，實作於 `720a1dd`。若恢復 toast 在圖示轉綠的同時就跳出來，
      那是**回歸**，不是好消息——如實回報。）
- [ ] 從系統匣 →「設定」，把連線密碼改成伺服器會拒絕的值 → 圖示轉紅，toast 顯示
      「連線密碼已失效」。**log 檔必須留有 401／403 紀錄；toast 內容中絕對不可出現
      任何狀態碼。**
- [ ] **啟動時**就把一支攝影機的 USB 線拔掉再開 app → toast 顯示「攝影機沒有畫面」，
      且訊息中指名的是操作員在設定裡自己命名的攝影機名稱，不是 node_id 或其他內部識別碼。
- [ ] **執行中**拔掉一支攝影機的 USB 線 → 約 3 秒（`BAD_READ_LIMIT × BAD_READ_SLEEP`）
      後判定為 CAMERA_DOWN，再經 30 秒去抖動才跳 toast，因此**從拔線到看到通知約 33 秒**，
      這是預期行為不是延遲。插回後應自行恢復（此路徑的 engine thread 不會結束）。
      （2026-07-26 修正前，執行中拔線是**完全偵測不到**的——`cap.read()` 永遠回傳 False，
      engine 不再回報任何東西，系統匣一直是綠的。修正 commit `fe49e5f`。）
- [ ] 從系統匣按「暫停上傳」→ 圖示轉為琥珀色（amber）；暫停期間即使背後其實有故障，
      也不應該跳出任何故障 toast。按「恢復上傳」後，圖示應恢復成當下實際狀態該顯示
      的顏色。
- [ ] **在故障進行中**按「暫停上傳」→ **不應該**跳出任何 toast。（暫停是保全自己按的，
      他知道；而且在故障還在時跳一則通知，讀起來像「問題解決了」。`STARTING` 早就基於
      同樣理由被排除在恢復通知外，`PAUSED` 於 `aaf76d0` 一併排除。）恢復推送後，若故障
      仍在，不應重複 toast；若故障已在暫停期間排除，才應跳恢復通知。
- [ ] 雙擊系統匣圖示 → 開啟「監控狀態」視窗；視窗內「開啟記錄檔」按鈕會開啟 log
      資料夾；「重新連線」按鈕能在不重啟整個 app 的情況下重建所有 engine。
- [ ] **去抖動邊界檢查：** 刻意製造一次明顯短於 30 秒的瞬斷（例如快速拔插網路線後立刻
      插回），確認**沒有**跳出對應的故障 toast。**若跳出了，代表去抖動機制壞了——
      如實回報這個事實，不要靠調高 `NOTIFY_DEBOUNCE_SECONDS` 的門檻把它藏起來。**

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

不得硬編任何憑證；`Msc@***`、`MSC-***` 不得出現於任何位置；正式路徑不得出現
`broker.emqx.io`；**不得對 edge 裝置新增 `stream_start` / `stream_stop` 以外的任何指令
下行介面**。

本設計全為 client 端，未觸及以上任一。另新增一項約束：
**API key 不得出現在 log 檔中**（Phase 1 新增檔案 logging 後才成立的新風險，已納入測試）。
