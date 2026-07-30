# sdprs/webcam_client/strings.py
"""Every operator-facing string, in one place.

The operator is a security guard, not a technician. Two rules follow, and
tests/test_strings.py enforces both:
  1. No status code, exception text, or English developer vocabulary.
  2. Every fault names an ACTION. "something went wrong" is unactionable.

The status code is NOT discarded -- it goes to the log file for whoever the
guard calls. This module is only what the guard reads.

Keyed on the Health enum's .value (a str) rather than on the enum itself, so
status.py can import strings without a circular import.
"""

WINDOW_TITLE = "SDPRS 監控狀態"
TRAY_TOOLTIP_PREFIX = "SDPRS 監控"

# --- Every label the guard can read, or be told to press -------------------
# The tray menu's labels used to live in gui/tray_app.py, outside the guarantee
# this module's docstring makes for the whole app -- and it cost exactly what
# you would expect. The tray spelled its settings item 「開啟設定」 while the
# status window's button said 「設定」 and bad_key's action told the guard to
# press 「設定」: three spellings, two of them wrong, for one control. The pause
# label had already drifted the same way once (推送 → 上傳), and the only reason
# that was caught is that a test happened to pin the literal.
#
# So every such label is a constant here, and the instruction text further down
# interpolates these rather than retyping them. A control and the sentence that
# tells the guard to press it can then no longer disagree.
MENU_STATUS = "監控狀態"
BTN_OPEN_LOGS = "開啟記錄檔"
BTN_RECONNECT = "重新連線"
BTN_SETTINGS = "設定"
MENU_QUIT = "離開"
MENU_PAUSE = "暫停上傳"
MENU_RESUME = "恢復上傳"

# Shown when the guard launches the app while it is already running. That is the
# one moment they are actively hunting for it, which makes it the only good place
# to teach the double-click that opens the status window: nothing else in the app
# ever mentions it, and the tooltip cannot carry the hint -- szTip is capped at
# 127 characters and already holds the title, the detail and the action.
ALREADY_RUNNING = (f"SDPRS 監控已經在執行中，圖示在螢幕右下角。"
                   f"在圖示上點兩下，就會打開「{MENU_STATUS}」。")

# --- The setup window ------------------------------------------------------
# This window used to hold its own strings. That put the FIRST screen a guard
# ever meets outside the guarantee this module's docstring claims for the whole
# app, and it showed: its error paths still shipped a raw status code, an
# English exception repr, and the word JSON long after every other surface had
# been cleaned up. They live here now so the same tests cover them.
#
# The confirm button is labelled by MODE. bad_key sends the guard here to SAVE
# a key an administrator just issued, and 「開始」 -- right for a first run --
# reads like "start something new" at exactly that moment, so edit mode says
# 儲存 instead. Both labels are named by the fault text below rather than
# retyped there, so the instruction and the control cannot drift apart. They
# already had: the text said 儲存 while the only buttons were 開始 and 取消.
WIZ_TITLE = "SDPRS 監控設定"
WIZ_TITLE_EDIT = "SDPRS 監控設定（編輯）"
BTN_WIZARD_START = "開始"
BTN_WIZARD_SAVE = "儲存"
BTN_WIZARD_CANCEL = "取消"
BTN_WIZARD_RESCAN = "重新掃描"
LBL_SERVER_URL = "連線位址"
LBL_API_KEY = "連線密碼"

# These fire on the day the guard is standing at an unfamiliar PC trying to get
# the app working -- the day they can least afford a status code. The code and
# the exception are NOT discarded: setup_wizard.py logs both for the technician
# before showing any of these. None names the confirm button, because the guard
# just pressed it and it is still in front of them; 再試一次 stays true whether
# that button says 開始 or 儲存.
WIZ_NEED_URL_AND_KEY = f"請填入「{LBL_SERVER_URL}」和「{LBL_API_KEY}」，兩項都不能空白。"
WIZ_NEED_A_CAMERA = (f"請至少勾選一支攝影機。若清單是空的，"
                     f"請確認攝影機的 USB 線已插好，再按「{BTN_WIZARD_RESCAN}」。")
WIZ_NO_CAMERA_FOUND = (f"找不到攝影機。請確認攝影機的 USB 線已插好，"
                       f"再按「{BTN_WIZARD_RESCAN}」；仍找不到請通知管理員。")
WIZ_CANNOT_REACH_SERVER = (f"連不到伺服器。請檢查電腦後方的網路線是否鬆脫，"
                           f"並確認「{LBL_SERVER_URL}」與管理員給的完全相同，"
                           f"然後再試一次；仍然連不到請通知管理員。")
WIZ_KEY_REJECTED = (f"連線密碼不正確。請向管理員索取正確的連線密碼，"
                    f"重新填入「{LBL_API_KEY}」後再試一次。")
WIZ_SERVER_REFUSED = (f"伺服器拒絕了這次設定。請確認「{LBL_SERVER_URL}」"
                      f"與管理員給的完全相同後再試一次；"
                      f"仍然失敗請通知管理員「攝影機登記不成功」。")
WIZ_BAD_RESPONSE = "伺服器的回覆無法辨識，設定沒有完成。請通知管理員「攝影機登記回應異常」。"

# --- The guided setup page: three numbered sections ------------------------
# The window used to be two unnumbered LabelFrames titled 伺服器連線 and 攝影機
# -- a layout that assumes the reader already knows setup has an order. The
# guard doing this is the same person who will work the night shift with it,
# and they meet it once. Numbering the sections is the whole navigation aid:
# it says how many steps there are, which one you are on, and that there is an
# end. 伺服器連線 also leaked the machine's role into a heading no guard needs
# to reason about -- they are connecting, not administering a server.
WIZ_SECTION_CONNECT = "1 連線"
WIZ_SECTION_CAMERAS = "2 選擇要監控的攝影機"
WIZ_SECTION_START = "3 開始監控"

# Field and control labels. No trailing colon: the widget appends 「：」 the way
# LBL_SERVER_URL / LBL_API_KEY already are at their call sites, so the colon
# style stays in one place instead of being baked into half the constants.
LBL_CAMERA_NAME = "名稱"
BTN_WIZARD_TEST = "測試連線"
BTN_REVEAL_KEY = "顯示"
CHK_AUTOSTART = "開機時自動啟動"

# Camera row labels. str.format templates, NOT finished strings, matching the
# _TEXT table's convention -- and there is a second reason here. The constant
# scan in tests bans any bare 3-digit number in 100-599 (it is how a status
# code leaks), and a resolution written out as "640x480" trips it on the 480.
# Keeping width and height as placeholders means the literal carries no digits
# at all, so the check stays strict without the copy having to lie about what
# it shows. Three call sites share WIZ_CAMERA_LABEL -- the checkbox subtitle,
# the default value of the 名稱 box, and the fallback used when the guard
# clears that box -- so a rename cannot leave the three disagreeing.
WIZ_CAMERA_LABEL = "攝影機 {index}"
WIZ_CAMERA_LABEL_WITH_SIZE = "攝影機 {index}（畫面 {width}x{height}）"
WIZ_SCAN_FOUND = "找到 {count} 支攝影機"

# In-progress lines. 掃描中 / 連線中 became 正在… ，請稍候: the old form states a
# mode, this one states that waiting is the correct thing to do. A full sweep
# takes seconds, and a guard who reads a bare 掃描中... presses the button
# again. 掃描 itself is also mildly technical; 尋找攝影機 is not.
WIZ_SCANNING = "正在尋找攝影機，請稍候…"
WIZ_CONNECTING = "正在連線，請稍候…"

# 測試連線 exists so the guard can prove the address and password before they
# have chosen a single camera -- the old window only found out at 開始, after
# the guard had done all the work, and then threw all of it away behind a
# modal. Only the two OUTCOMES that are new live here. Its failures are the
# SAME two faults the save path already names, so they reuse
# WIZ_KEY_REJECTED and WIZ_CANNOT_REACH_SERVER rather than getting a second
# wording: one fault with two spellings is precisely what this module exists
# to prevent, and the drift would be invisible because each is only reachable
# down its own branch.
WIZ_TEST_IN_PROGRESS = "正在測試連線，請稍候…"
WIZ_TEST_OK = f"連線成功。請接著完成「{WIZ_SECTION_CAMERAS}」。"

# The password box is masked, and the guard may well be typing a key an
# administrator read out to them over the phone. Masked + typed-from-dictation
# is the combination that produces a rejection the guard cannot diagnose,
# because the one thing that would settle it -- looking at what they typed --
# is the one thing the field forbids.
WIZ_REVEAL_KEY_HINT = (f"「{LBL_API_KEY}」預設會遮起來。"
                       f"想確認有沒有打錯，請按「{BTN_REVEAL_KEY}」。")

# A camera sitting past a gap in the device indices is missed by the quick
# scan, and the guard's evidence is simply "mine isn't in the list" -- a state
# with no error and no message, which is why it needs copy at all. Physical
# action first (same rule as camera_down), and no 管理員: a missing camera is
# very often an unseated cable, and escalating on the first try would turn
# every one of those into a phone call. WIZ_NO_CAMERA_FOUND still carries the
# escalation for the case where nothing is found at all.
WIZ_RESCAN_HINT = (f"清單裡少了某一支攝影機？請先確認它的 USB 線已插好，"
                   f"再按「{BTN_WIZARD_RESCAN}」重新找一次；"
                   f"這會多花幾秒，請等它跑完。")

# autostart.py writes a Run entry; config defaults it to False. Worth
# recommending in words rather than just defaulting it on, because the reason
# is one only the guard can weigh: the site loses power, and without this
# nothing restarts monitoring until a person walks to the machine.
WIZ_AUTOSTART_HINT = (f"建議勾選「{CHK_AUTOSTART}」。勾選後這台電腦一開機就會自己"
                      f"開始監控，停電後電力恢復也不必有人來按。")

# Cancel is TWO messages because it has two consequences, and one text would
# have to be false about one of them.
#
# On a first run, main() takes run_setup_wizard() returning None and returns
# immediately -- before TrayApp is ever constructed. There is no tray icon
# afterwards, so telling the guard to right-click one and choose 設定 sends
# them hunting for something that is not on the screen. The program closing is
# itself the fact they need, so the first-run text says that and names the only
# way back that actually exists: open it again.
#
# In edit mode the tray is already running and cancelling changes nothing --
# monitoring carries on with the settings it already had. 監控不會啟動 would be
# a lie there, so that variant states what is really at stake (the edits) and
# CAN name the tray item, because this time there is one.
WIZ_CONFIRM_CANCEL = ("取消就不會儲存這次的設定，監控不會啟動，畫面也不會上傳。"
                      "取消後這個程式會關閉；要重新設定，請再開啟一次這個程式。")
WIZ_CONFIRM_CANCEL_EDIT = (f"取消就不會儲存這次的修改，監控會繼續照原本的設定執行。"
                           f"要再回來修改，請在螢幕右下角的圖示上按滑鼠右鍵，"
                           f"選「{BTN_SETTINGS}」。")

# Shown when 開啟記錄檔 cannot hand the folder to the shell. The guard pressing
# this button is almost always already on the phone with a technician who asked
# for it, so a button that silently does nothing leaves them with nothing to
# say. No path is quoted here: a filesystem path is developer text, and the
# technician can be told where the folder is by other means.
LOG_FOLDER_FAILED = "無法開啟記錄檔，監控仍在正常運作。請通知管理員「記錄檔打不開」。"

# state value -> (title, detail template, action). Templates use str.format;
# describe() supplies defaults so a missing key can never raise.
_TEXT = {
    "starting": (
        "啟動中",
        "正在連線並開啟攝影機，請稍候。",
        "",
    ),
    "running": (
        "監控中",
        "{camera_count} 支攝影機運作正常。",
        "",
    ),
    "paused": (
        "已暫停上傳",
        # 伺服器, not 監控中心: this was the ONE place in the app that gave the
        # destination a second name. A guard who reads 「不會傳到監控中心」 here
        # and 「無法連線到伺服器」 when it breaks has no way to know those are
        # the same machine, and reports two systems to a technician who has one.
        "上傳已被手動暫停，畫面不會傳到伺服器。",
        # 「恢復上傳」 is interpolated, not retyped: this sentence names a tray
        # menu item, and the tray used to spell its own labels independently.
        f"要繼續上傳，請在螢幕右下角的圖示上按滑鼠右鍵，選「{MENU_RESUME}」。",
    ),
    "no_server": (
        "無法連線到伺服器",
        "畫面目前無法上傳。",
        "請檢查電腦後方的網路線是否鬆脫；網路恢復後會自動繼續上傳，若仍無法連線再通知管理員。",
    ),
    # Both of the actions below NAME a button on purpose -- but not the same
    # button, and not for the same reason.
    #
    # Neither fault is guaranteed to self-heal. open_camera() returning None
    # ends the push engine's thread outright, and a 401 stops the control
    # channel; a mid-run unplug or a 403 leaves the worker alive but retrying
    # something that cannot work until a human changes the physical world. So
    # the physical fix -- re-seating the USB cable, or the administrator
    # issuing a new key -- may recover nothing on its own, because in the
    # stopping cases no worker is left alive to reopen the device or retry.
    # Neither line may stop once the world has been put right; each has to go
    # on to name what makes the fix take.
    #
    # camera_down is a fix the guard performs alone, so it ends at 重新連線 and
    # holds 通知管理員 back to last resort: describing the cable and then
    # escalating immediately would turn every re-seated cable into an avoidable
    # escalation, on the single most likely fault a guard meets. bad_key has to
    # escalate first instead -- a guard cannot mint a key, and 重新連線 would
    # only re-send the rejected one -- so it sends them to 設定 to enter
    # whatever the administrator issues back, and keeps 重新連線 only for the
    # case where the administrator says the key stands.
    #
    # Both name the window a button lives in rather than pointing at it
    # ("下方的按鈕"), because this same action line is also the body of the
    # toast, where there is no button below anything.
    "bad_key": (
        "連線密碼已失效",
        "伺服器不接受這台電腦目前的連線密碼。",
        f"請通知管理員重設連線密碼。在「{MENU_STATUS}」視窗按「{BTN_SETTINGS}」，"
        f"把新密碼填進「{LBL_API_KEY}」再按「{BTN_WIZARD_SAVE}」；"
        f"若管理員說不用換密碼，改按「{BTN_RECONNECT}」。",
    ),
    "camera_down": (
        "攝影機沒有畫面",
        "{camera_names}目前沒有畫面。",
        f"請檢查攝影機的 USB 線是否鬆脫；重新插好後，"
        f"請按「{MENU_STATUS}」視窗下方的「{BTN_RECONNECT}」；仍沒有畫面請通知管理員。",
    ),
}

_DEFAULTS = {"camera_count": None, "camera_names": "攝影機"}

# Used only when RUNNING is rendered with no camera_count in context (e.g. an
# error path with no data to hand). "0 支攝影機運作正常" would tell the guard
# zero cameras are working while claiming everything is fine -- a
# self-contradiction. This text stays true (monitoring is active) without
# inventing a count we don't have.
_RUNNING_DETAIL_UNKNOWN_COUNT = "攝影機運作正常。"


def describe(state, **ctx):
    """Return (title, detail, action) for a Health state.

    `state` is anything with a `.value` matching a key above. Missing context
    keys fall back to _DEFAULTS rather than raising -- an error path that has no
    context must still be able to render something.
    """
    key = getattr(state, "value", state)
    title, detail_tpl, action = _TEXT[key]
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in ctx.items() if v is not None})
    if key == "running" and merged.get("camera_count") is None:
        detail = _RUNNING_DETAIL_UNKNOWN_COUNT
    else:
        try:
            detail = detail_tpl.format(**merged)
        except (KeyError, IndexError):
            detail = detail_tpl
    return title, detail, action
