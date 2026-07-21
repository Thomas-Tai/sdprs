# sdprs/webcam_client/tests/test_packaging.py
"""Frozen-exe entry point must not be a package module run as __main__.

PyInstaller runs the entry script as ``__main__`` (no parent package). The
original build.spec used the package module ``main.py`` directly, whose
``from .config import ...`` then failed at startup with

    ImportError: attempted relative import with no known parent package

so the packaged exe crashed before doing anything. The fix is a thin launcher
(``app.py``) that makes the package importable and imports it ABSOLUTELY; the
spec points at the launcher. These tests pin both the bug and the fix.
"""
import subprocess
import sys
from pathlib import Path

WEBCAM_DIR = Path(__file__).resolve().parent.parent          # .../webcam_client
REPO_ROOT = WEBCAM_DIR.parent                                # .../sdprs
sys.path.insert(0, str(REPO_ROOT))


def test_package_module_as_loose_script_reproduces_the_bug(tmp_path):
    """Running main.py as a loose script (PyInstaller's __main__ model) still
    fails the same way — this is WHY a launcher is required, pinned so nobody
    'fixes' it by pointing the spec back at main.py."""
    proc = subprocess.run(
        [sys.executable, str(WEBCAM_DIR / "main.py")],
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    assert proc.returncode != 0
    assert "attempted relative import with no known parent package" in proc.stderr


def test_launcher_run_as_loose_script_resolves_imports(tmp_path):
    """app.py run as a loose script (exactly how the frozen entry runs) must
    resolve the WHOLE package import chain and NOT hit the relative-import
    error. `--check` short-circuits before the GUI so this stays headless."""
    proc = subprocess.run(
        [sys.executable, str(WEBCAM_DIR / "app.py"), "--check"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert "attempted relative import" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "entry OK" in proc.stdout


def test_launcher_has_no_relative_imports():
    """The launcher is the __main__ entry, so it must never use relative
    imports (that is the exact regression)."""
    src = (WEBCAM_DIR / "app.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("from ."), f"relative import in entry: {line}"
        assert not stripped.startswith("from .."), f"relative import in entry: {line}"


def test_build_spec_entry_is_the_launcher_not_a_package_module():
    """Guard against reverting the spec to a package module entry."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "'app.py'" in spec or '"app.py"' in spec, "spec must build the launcher"
    assert "['main.py']" not in spec and '["main.py"]' not in spec, \
        "spec entry must NOT be the package module main.py (relative-import crash)"
