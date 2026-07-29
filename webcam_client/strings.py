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
ALREADY_RUNNING = "SDPRS 監控已在執行中。"

MENU_STATUS = "監控狀態"
BTN_OPEN_LOGS = "開啟記錄"
BTN_RECONNECT = "重新連線"
BTN_SETTINGS = "設定"

# Shown when 開啟記錄 cannot hand the folder to the shell. The guard pressing
# this button is almost always already on the phone with a technician who asked
# for it, so a button that silently does nothing leaves them with nothing to
# say. No path is quoted here: a filesystem path is developer text, and the
# technician can be told where the folder is by other means.
LOG_FOLDER_FAILED = "無法開啟記錄資料夾，請通知管理員。"

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
        "目前由操作員手動暫停，畫面不會上傳。",
        "在系統匣圖示按右鍵，選「恢復推送」即可繼續。",
    ),
    "no_server": (
        "無法連線到伺服器",
        "畫面目前無法上傳。",
        "請檢查網路連線是否正常；若網路正常仍無法連線，請通知管理員。",
    ),
    # Both of the actions below NAME the 重新連線 button on purpose.
    #
    # Neither fault is guaranteed to self-heal. open_camera() returning None
    # ends the push engine's thread outright, and a 401 stops the control
    # channel; a mid-run unplug or a 403 leaves the worker alive but retrying
    # something that cannot work until a human changes the physical world. So
    # the physical fix -- re-seating the USB cable, or the administrator
    # resetting the key -- may recover nothing on its own, because in the
    # stopping cases no worker is left alive to reopen the device or retry.
    # 重新連線 is the one action that does, and it sits three centimetres below
    # this text in the status window. Text that describes the physical fix and
    # then sends the guard straight to "通知管理員" turns every re-seated cable
    # into an avoidable escalation, on the single most likely fault a guard
    # meets. The button is located ("「監控狀態」視窗下方") rather than merely
    # named because this same action line is also the body of the toast, where
    # there is no button below anything.
    "bad_key": (
        "連線密碼已失效",
        "伺服器不接受這台電腦目前的連線密碼。",
        "請通知管理員重新設定連線密碼；設定好之後，"
        "請按「監控狀態」視窗下方的「重新連線」。",
    ),
    "camera_down": (
        "攝影機沒有畫面",
        "{camera_names} 目前沒有畫面。",
        "請檢查攝影機的 USB 線是否鬆脫；重新插好後，"
        "請按「監控狀態」視窗下方的「重新連線」；仍沒有畫面請通知管理員。",
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
