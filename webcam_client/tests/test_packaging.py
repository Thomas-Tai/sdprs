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

from webcam_client import buildconfig  # noqa: E402  (needs the sys.path insert above)


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


def test_build_spec_disables_upx_for_faster_launch():
    # UPX decompression runs on every onefile launch; turning it off trades a
    # slightly larger exe for a faster cold start.
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "upx=False" in spec, "UPX must be off — decompression slows cold start"


def test_build_spec_stays_onefile():
    # Single-file drop is a hard product requirement: no onedir COLLECT.
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "COLLECT(" not in spec, "must remain a one-file build (no onedir COLLECT)"


def test_build_spec_imports_buildconfig_for_packaging_decisions():
    """build.spec itself can't be imported by pytest (needs PyInstaller's
    injected SPECPATH/Analysis/EXE globals), so it must delegate the actual
    ffmpeg-resolution / oversize / binary-filter decisions to the plain,
    importable buildconfig module rather than re-inlining brittle logic that
    only string-matching tests could ever see."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "import buildconfig" in spec
    assert "buildconfig.resolve_ffmpeg(" in spec
    assert "buildconfig.ffmpeg_is_oversized(" in spec
    assert "buildconfig.filter_binaries(" in spec


# -- buildconfig: behavioral coverage of the actual packaging decisions -------
#
# The four tests these replace only checked that certain substrings appeared
# somewhere in build.spec's text -- which cannot distinguish "the exclusion
# constant is wired into the filter" from "the same substring merely appears
# in a nearby comment", and cannot detect an inverted predicate that would
# drop every binary or ship every excluded one. These call the real
# buildconfig functions instead.

def test_filter_binaries_drops_excluded_and_keeps_survivors():
    """28.6MB opencv_videoio_ffmpeg* (OpenCV's video-FILE i/o backend -- this
    client only ever does VideoCapture(index, CAP_DSHOW) live capture, never
    opens a video file) and 7.8MB PIL _avif (PIL only draws a 64x64 tray
    circle and one Tk thumbnail) must be dropped, in ANY case combination,
    while everything else in a realistic binaries list survives untouched."""
    fixture = [
        ("cv2\\cv2.pyd", "C:\\build\\cv2\\cv2.pyd", "EXTENSION"),
        ("python314.dll", "C:\\build\\python314.dll", "BINARY"),
        ("libcrypto-3.dll", "C:\\build\\libcrypto-3.dll", "BINARY"),
        ("PIL\\_imaging.cp314-win_amd64.pyd", "C:\\build\\PIL\\_imaging.cp314-win_amd64.pyd", "EXTENSION"),
        ("opencv_videoio_ffmpeg4130_64.dll", "C:\\build\\opencv_videoio_ffmpeg4130_64.dll", "BINARY"),
        ("OPENCV_VIDEOIO_FFMPEG4130_64.DLL", "C:\\build\\OPENCV_VIDEOIO_FFMPEG4130_64.DLL", "BINARY"),
        ("PIL\\_avif.cp314-win_amd64.pyd", "C:\\build\\PIL\\_avif.cp314-win_amd64.pyd", "EXTENSION"),
        ("PIL\\_AVIF.cp314-win_amd64.pyd", "C:\\build\\PIL\\_AVIF.cp314-win_amd64.pyd", "EXTENSION"),
    ]
    result = buildconfig.filter_binaries(fixture)
    names = {b[0] for b in result}

    survivors = {
        "cv2\\cv2.pyd",
        "python314.dll",
        "libcrypto-3.dll",
        "PIL\\_imaging.cp314-win_amd64.pyd",
    }
    assert survivors <= names, f"survivors wrongly dropped: {survivors - names}"

    excluded = {
        "opencv_videoio_ffmpeg4130_64.dll",
        "OPENCV_VIDEOIO_FFMPEG4130_64.DLL",
        "PIL\\_avif.cp314-win_amd64.pyd",
        "PIL\\_AVIF.cp314-win_amd64.pyd",
    }
    assert not (excluded & names), f"excluded binaries wrongly kept: {excluded & names}"
    assert len(result) == len(survivors)


def test_resolve_ffmpeg_precedence_env_then_vendor_then_which():
    """Documented precedence: explicit SDPRS_FFMPEG > vendored ffmpeg.exe >
    shutil.which('ffmpeg') on the build machine's PATH. A falsy/missing env
    value must fall through to the next candidate, not be returned as-is."""
    env, vendor, which = "C:\\env\\ffmpeg.exe", "C:\\vendor\\ffmpeg.exe", "C:\\which\\ffmpeg.exe"

    # env wins over both vendor and which.
    assert buildconfig.resolve_ffmpeg(env, vendor, which) == env

    # vendor wins over which once env is falsy (unset or not a real file).
    assert buildconfig.resolve_ffmpeg(None, vendor, which) == vendor
    assert buildconfig.resolve_ffmpeg("", vendor, which) == vendor

    # falls through all the way to which when env and vendor are both falsy.
    assert buildconfig.resolve_ffmpeg(None, None, which) == which

    # nothing resolves -> None (build.spec's "ffmpeg not found" branch).
    assert buildconfig.resolve_ffmpeg(None, None, None) is None


def test_ffmpeg_is_oversized_uses_the_two_real_measured_builds():
    """The build machine has both ffmpeg builds installed side by side:
    Gyan.FFmpeg.Essentials at 101,457,920 bytes (~101.5MB, must NOT warn) and
    the full Gyan.FFmpeg build at 227,398,656 bytes (~227.4MB, matches the
    brief's measured basis exactly, MUST warn)."""
    assert buildconfig.ffmpeg_is_oversized(101_457_920) is False
    assert buildconfig.ffmpeg_is_oversized(227_398_656) is True


def test_assets_exist():
    assert (WEBCAM_DIR / "assets" / "sdprs.ico").is_file()
    assert (WEBCAM_DIR / "assets" / "splash.png").is_file()


def test_icon_16px_is_handtuned_not_a_downsample():
    """Naive downsampling of the 256px master closes the aperture into an
    unreadable dot and reduces the mount nub to a stray pixel -- at 16px, the
    size Windows uses in the taskbar. Pin that the .ico carries DISTINCT small
    artwork rather than a resample."""
    from PIL import Image

    ico = WEBCAM_DIR / "assets" / "sdprs.ico"
    with Image.open(ico) as im:
        im.size = (256, 256)          # ICO frame selection
        im.load()
        naive = im.convert("RGBA").resize((16, 16), Image.LANCZOS)
    with Image.open(ico) as im:
        im.size = (16, 16)
        im.load()
        actual = im.convert("RGBA")
    assert list(actual.getdata()) != list(naive.getdata()), \
        "16px frame is just a downsample -- hand-tuned artwork did not make it in"


def test_build_spec_declares_a_splash():
    """S4: console=False + ~20s onefile extraction = a completely blank screen.
    The bootloader paints the splash before Python starts."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "Splash(" in spec
    assert "splash.binaries" in spec, "onefile EXE must receive splash.binaries"


def test_build_spec_sets_an_icon():
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "icon=None" not in spec
    assert "sdprs.ico" in spec
