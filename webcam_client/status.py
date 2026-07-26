# sdprs/webcam_client/status.py
"""Shared vocabulary for the webcam client's health/fault reporting.

This module currently defines only the enums and constants that name every
state the client can be in, plus the shared control-source marker and the
notification debounce window. They are the vocabulary that `webcam_client.strings`
keys its operator-facing text on (by `.value`, precisely to avoid importing this
module and creating a circular import).

`StatusHub` -- the class that aggregates raw signals (HTTP failures, camera
reads, etc.) into one of these `Health` states and dispatches notifications --
arrives in the next task. This file is not a stub left half-done; it is the
complete, intentional scope of this task.
"""

from enum import Enum


class Fault(Enum):
    NONE = "none"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


class Health(Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


CONTROL_SOURCE = "__control__"
NOTIFY_DEBOUNCE_SECONDS = 30.0
