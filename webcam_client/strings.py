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
    "bad_key": (
        "連線密碼已失效",
        "伺服器不接受這台電腦目前的連線密碼。",
        "請通知管理員重新設定連線密碼。",
    ),
    "camera_down": (
        "攝影機沒有畫面",
        "{camera_names} 目前沒有畫面。",
        "請檢查攝影機的 USB 線是否鬆脫；重新插好後仍沒有畫面，請通知管理員。",
    ),
}

_DEFAULTS = {"camera_count": 0, "camera_names": "攝影機"}


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
    try:
        detail = detail_tpl.format(**merged)
    except (KeyError, IndexError):
        detail = detail_tpl
    return title, detail, action
