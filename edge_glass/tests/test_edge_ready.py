"""Readiness signal: cleared at startup, created once the node is functional.
The updater's health-check waits for it, so a 'service active but camera never
opened' update fails the check and rolls back."""
import os
import edge_glass_main as m


def test_mark_then_clear(tmp_path):
    p = str(tmp_path / "edge_ready")
    m.mark_edge_ready(p)
    assert os.path.exists(p)
    m.clear_edge_ready(p)
    assert not os.path.exists(p)


def test_clear_is_idempotent_when_absent(tmp_path):
    m.clear_edge_ready(str(tmp_path / "nope"))  # must not raise


def test_mark_is_idempotent(tmp_path):
    p = str(tmp_path / "edge_ready")
    m.mark_edge_ready(p)
    m.mark_edge_ready(p)  # second call must not raise
    assert os.path.exists(p)


def test_mark_missing_dir_does_not_raise(tmp_path):
    m.mark_edge_ready(str(tmp_path / "nodir" / "edge_ready"))  # swallowed


def test_default_path_from_env(monkeypatch, tmp_path):
    target = str(tmp_path / "custom_ready")
    monkeypatch.setenv("EDGE_READY_FILE", target)
    assert m.edge_ready_path() == target
