# Webcam Client 設定可編輯 + 啟動加速（聚焦版）設計文件

**日期：** 2026-07-25
**狀態：** 已核准（brainstorming），待撰寫實作計畫
**範圍：** 聚焦——修 tray 設定 bug + 啟動加速 + 4 項高影響 UX 修正
**相關：** [[2026-07-21-webcam-client-design]]（原始功能設計，已 SHIPPED）

---

## 概述

`SDPRS_Webcam.exe` 首次設定完成後，**從系統匣（tray）無法再修改設定**；同時 exe
冷啟動偏慢。本設計以「GUI 跑在主執行緒、工作執行緒在其下」的標準架構反轉現有的
執行緒模型，一次解決設定 bug 的多個根因，並附帶啟動加速與數項 UX 修正。

打包維持**單一 .exe**（使用者明確要求；不改為 onedir）。設定套用採**行程內即時
套用（in-process apply，免重啟 app）**。

## 根因（已用實際程式碼與探針驗證）

「首次設定後無法修改設定」不是單一 bug，而是**設定編輯路徑在架構上就是壞的**，
數個失效疊在一起。已排除的假設：「跨執行緒 Tkinter 必崩潰」——在本機 Win11／
Py3.14 以無視窗探針測試，daemon 執行緒建立全新 `tk.Tk()` + `mainloop()` 可正常
運作，故此非根因。真正的根因：

| # | 失效 | 證據 |
|---|------|------|
| A（主因）| **攝影機佔用衝突。** 首次設定後唯一的編輯入口是 tray →`開啟設定`→重跑整個首次設定精靈；此時每個 `PushEngine` 已長期持有攝影機控制代碼，精靈開啟時自動掃描（`root.after(100, do_scan)`）會用 `CAP_DSHOW` 重新開啟同一裝置 → Windows 下同一攝影機無法二次開啟 → 卡在「掃描中…」看似「無法修改設定」。 | `push_engine.py:76`（持續 read loop）、`setup_wizard.py:175`、`camera_manager.py:14-15` |
| B | **巢狀訊息迴圈。** 精靈的 `root.mainloop()` 跑在 pystray 選單 callback 內，而該 callback 又在 pystray 自身的 Win32 訊息迴圈內、在 daemon 執行緒上 → 焦點／modality 脆弱。 | `tray_app.py:53` |
| C | **改了也不會生效。** `_open_settings` 只 `save_config` 並記 log「restart required」；執行中的 engines 只建立一次、永不重建。 | `main.py:104-108`、`main.py:48-68` |
| D | **重複註冊。** `on_start` 每次都重新 POST `/api/webcam/cameras`，每次都產生新的 camera node_id → dashboard 上出現重複的 webcam tile。 | `setup_wizard.py:156` |

## 啟動慢的根因

- **最大宗：onefile + UPX + 內嵌 ffmpeg（約 160 MB）。** onefile 每次啟動都要把整包
  （ffmpeg ~80 MB、cv2、numpy）解壓到 `%TEMP%` 的 `_MEIPASS`，`upx=True` 再加一層
  解壓 CPU；Defender 也會重掃 160 MB 檔。（`build.spec:58,61`）
- **`scan_cameras(max_index=10)` 落在啟動關鍵路徑上**——首次精靈自動掃描要探測 10 個
  DSHOW 索引，數秒後使用者才能操作。（`camera_manager.py:12-14`）

---

## 架構：GUI 上主執行緒，工作執行緒在其下

現況：`main()` 建 engines → 於 daemon 執行緒啟動 tray → 主執行緒停在
`while _running: time.sleep(1)`。GUI 只能從 tray 的 daemon 執行緒觸達——正是 bug 的
根。反轉為：

- **新增 `webcam_client/app_controller.py` 的 `AppController`**：持有 `config`、
  `PushEngine` 清單、`ControlChannel`，對外提供
  - `start_engines()`
  - `stop_engines()`（**釋放攝影機**：對每個 engine `stop()` 後 join 逾時，確保
    `cv2.VideoCapture` 已 release）
  - `apply(new_config)`（stop → 依新 config 重建 engines/control → start）
  - `pause_all()` / `resume_all()`（tray 暫停經此轉發，**而非**閉包直接抓 engine
    清單——`apply()` 重建後閉包會指向舊的死 engine，故暫停／恢復必須走 controller）
- **主執行緒跑一個 `queue.Queue` 派工迴圈。** tray daemon 執行緒**完全不碰 Tk**：其
  `開啟設定` callback 只做 `queue.put("OPEN_SETTINGS")`。主執行緒收到後：
  1. `controller.stop_engines()`（釋放攝影機，消除衝突 A）
  2. **於主執行緒**開啟設定視窗（消除巢狀迴圈 B；pystray 續留自己的執行緒）
  3. 存檔 → `controller.apply(new_config)`（engines 即時重建，免重啟——消除 C）
  4. 取消／關閉未存檔 → `controller.start_engines()` 以**原 config** 恢復

此單一改動同時消滅 A／B／C。

### 派工協定（主執行緒事件迴圈）

```
main():
  controller = AppController(config)
  controller.start_engines()
  q = queue.Queue()
  tray = TrayApp(on_open_settings=lambda: q.put("OPEN_SETTINGS"),
                 on_quit=lambda: q.put("QUIT"), on_pause=..., on_resume=...)
  tray.start()
  while running:
    try: req = q.get(timeout=1.0)
    except Empty: continue
    if req == "OPEN_SETTINGS":
      controller.stop_engines()
      new_cfg = run_settings_window(controller.config, mode="edit")  # 主執行緒 Tk
      if new_cfg: controller.apply(new_cfg)
      else:       controller.start_engines()
    elif req == "QUIT":
      controller.shutdown(); running = False
```

## 聚焦的 5 項變更

1. **設定首次設定後可編輯**——上述 controller + 主執行緒視窗（涵蓋 A／B／C）。
2. **首次設定 vs 編輯分流**——`run_setup_wizard` 重構為帶 `mode` 的視窗：
   - `first-run`：空白，開啟時**非同步**掃描攝影機（見第 4 項），註冊所有選取的攝影機
   - `edit`：以 config 預填；攝影機清單直接顯示自 config，**不自動掃描**（engines 剛被
     停下、攝影機雖已釋放，但編輯常態是改 URL／key，不必每次重掃）；重掃為**按鈕觸發**；
     **不自動全量重新註冊**
   共用 widget、不同行為。
3. **不再重複註冊**（`register_cameras` 冪等化）——只 POST **沒有 `node_id`** 的攝影機；
   既有攝影機保留其 `node_id`。修正 D。
4. **掃描不凍結**——`scan_cameras` 移出 Tk 執行緒（worker 執行緒；視窗立即繪出、結果
   非同步填入、含 spinner／狀態文字），同時把 10 次 DSHOW 探測移出啟動關鍵路徑。
5. **tray 暫停狀態如實反映**——`暫停推送 ⇄ 恢復推送` 文字切換、圖示顯示暫停（琥珀）
   狀態，讓控制項不再「說謊」。

## 啟動加速（維持單一 .exe）

`build.spec`：維持 `onefile`，設 **`upx=False`**（或將 ffmpeg／cv2／numpy 二進位排除於
UPX 之外）省去解壓 CPU，並收斂 `excludes`。仍是單一 `.exe`，冷啟動明顯變快、無資料夾。
**誠實限制：** onefile 每次啟動仍會解壓到 `%TEMP%`，這是單檔本質；最大的贏面是 onedir，
使用者已明確不採用，可接受。

## 錯誤處理

- 存檔失敗（網路／註冊）→ messagebox，engines **以原 config 恢復**（絕不留在死掉狀態）。
- `stop_engines()` 必須在視窗掃描前**確實 release** 所有 `cv2.VideoCapture`；controller
  以逾時 join engine 執行緒，攝影機無法釋放時記 log。
- 取消／關閉設定且未存檔 → 原 engines 原封不動恢復。

## 測試策略（無硬體、無顯示器；全部可證偽，遵守本專案 CI 陷阱）

- `AppController`：以 fake engine → 驗 `apply()` 先停後建、`stop_engines()` 有 release、
  存檔失敗會以原 config 恢復。（純單元）
- 主執行緒派工：把 `OPEN_SETTINGS` 放入 queue → 驗走到 controller 路徑；tray callback
  **只入列、從不呼叫 Tk**。（純單元，無 Tk）
- `register_cameras` 冪等性：mock httpx → 有 `node_id` 的攝影機**不**重 POST，只註冊新的。
- 掃描不在 UI 執行緒：驗 `scan_cameras` 由非主執行緒觸發／視窗建構不內嵌呼叫它。
- `build.spec`：驗 `upx=False`（守住此回歸），擴充既有 `test_packaging.py`。
- 每個新測試皆走 RED→GREEN 並證偽；每檔單獨執行、帶 `-p no:cacheprovider`。

## 明確非目標（YAGNI）

不改 onedir、不做分頁式設定 UI、不做即時多鏡頭預覽面板、**不新增任何 server／edge
指令下行介面**、不動認證／憑證。

## 受影響檔案

- 新增：`webcam_client/app_controller.py`、對應 `tests/test_app_controller.py`
- 修改：`webcam_client/main.py`（派工迴圈）、`webcam_client/gui/setup_wizard.py`
  （mode 分流、掃描移出 UI 執行緒、register 冪等）、`webcam_client/gui/tray_app.py`
  （暫停標籤／圖示狀態）、`webcam_client/build.spec`（`upx=False`）
- 測試：`tests/test_setup_wizard.py`、`tests/test_tray_app.py`、`tests/test_packaging.py` 擴充

## 安全約束（沿用，不得違反）

不得硬編任何憑證；`Msc@***`、`MSC-***` 不得出現於任何位置；正式路徑不得出現
`broker.emqx.io`；**不得對 edge 裝置新增 `stream_start`／`stream_stop` 以外的任何指令下行
介面**。本設計皆為 client 端，未觸及以上任一。
