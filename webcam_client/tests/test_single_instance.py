# webcam_client/tests/test_single_instance.py
"""A second copy of the client fights the first for the same DSHOW camera
handles. Since the exe gives no feedback during onefile extraction, an operator
double-clicking while they wait is NORMAL behaviour -- so this guard is load
bearing, not a nicety."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import webcam_client.single_instance as si_mod
from webcam_client.single_instance import SingleInstance, DEFAULT_BASE_NAME


def _unique(tag):
    # Unique per test AND per run, so a crashed prior run cannot leak a held
    # mutex into this one and make the suite flaky.
    return f"SDPRSTest_{tag}_{os.getpid()}"


def test_first_acquire_succeeds():
    si = SingleInstance(_unique("first"))
    try:
        assert si.acquire() is True
    finally:
        si.release()


def test_second_acquire_fails_while_first_holds():
    base = _unique("second")
    a, b = SingleInstance(base), SingleInstance(base)
    try:
        assert a.acquire() is True
        assert b.acquire() is False, "second instance must be refused"
    finally:
        a.release()
        b.release()


def test_slot_is_reusable_after_release():
    base = _unique("reuse")
    a = SingleInstance(base)
    assert a.acquire() is True
    a.release()
    b = SingleInstance(base)
    try:
        assert b.acquire() is True, "releasing must free the slot"
    finally:
        b.release()


def test_release_without_acquire_is_safe():
    SingleInstance(_unique("norelease")).release()  # must not raise


def test_falls_back_to_local_when_global_is_not_permitted(monkeypatch):
    """A standard (non-admin) account may lack SeCreateGlobalPrivilege. That
    must degrade to a session-local guard, NOT disable the guard."""
    tried = []
    real = si_mod._try_create

    def fake(name):
        tried.append(name)
        if name.startswith("Global\\"):
            return None, False, si_mod._ERROR_ACCESS_DENIED
        return real(name)

    monkeypatch.setattr(si_mod, "_try_create", fake)
    # Global create was denied AND the object does not exist -> privilege issue.
    monkeypatch.setattr(si_mod, "_exists", lambda name: False)

    si = SingleInstance(_unique("fallback"))
    try:
        assert si.acquire() is True
        assert any(n.startswith("Global\\") for n in tried), "must try Global first"
        assert any(n.startswith("Local\\") for n in tried), "must fall back to Local"
    finally:
        si.release()


def test_access_denied_on_an_EXISTING_global_refuses_the_launch(monkeypatch):
    """The dangerous ambiguity: ACCESS_DENIED means either 'no privilege' or
    'another session owns it'. When the object EXISTS, a second instance must be
    refused -- falling back to Local here would let two copies fight the camera."""
    monkeypatch.setattr(
        si_mod, "_try_create",
        lambda name: (None, False, si_mod._ERROR_ACCESS_DENIED))
    monkeypatch.setattr(si_mod, "_exists", lambda name: True)
    assert SingleInstance(_unique("denied")).acquire() is False


def test_fails_open_when_both_namespaces_error(monkeypatch):
    """A guard bug must never keep the monitoring client off the air."""
    monkeypatch.setattr(si_mod, "_try_create", lambda name: (None, False, 1337))
    monkeypatch.setattr(si_mod, "_exists", lambda name: False)
    assert SingleInstance(_unique("failopen")).acquire() is True


def test_exists_is_false_for_an_absent_object():
    assert si_mod._exists(f"Local\\{_unique('absent')}") is False


def test_default_base_name_is_unqualified():
    # The class adds the Global\ / Local\ prefixes itself.
    assert not DEFAULT_BASE_NAME.startswith(("Global\\", "Local\\"))
