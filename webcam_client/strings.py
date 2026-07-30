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
