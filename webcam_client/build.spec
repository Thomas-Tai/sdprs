# sdprs/webcam_client/build.spec
# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
import sys
from pathlib import Path

block_cipher = None

# --- ffmpeg -------------------------------------------------------------------
# onefile re-extracts the ENTIRE payload to %TEMP% on every launch, so payload
# size IS startup time. Measured 2026-07-26: the build machine's PATH ffmpeg was
# the 227MB *full* build = 55.5% of a 409MB payload, and nobody noticed because
# nothing checked. The `essentials` build (~85MB) has everything h264/HLS needs:
#     winget install Gyan.FFmpeg.Essentials
# Resolution order makes the choice explicit instead of "whatever is first on
# PATH", and an oversized binary now warns at build time.
#
# build.spec cannot be imported by pytest (it needs PyInstaller-injected
# globals like SPECPATH/Analysis/EXE), so the actual decisions -- ffmpeg
# precedence, oversize threshold, which binaries get dropped and WHY -- live
# in the plain, unit-testable webcam_client/buildconfig.py. This file only
# does the side-effecting reads (env, disk, PATH) and calls into it.
sys.path.insert(0, str(Path(SPECPATH).parent))
from webcam_client import buildconfig

_env_ffmpeg = os.environ.get('SDPRS_FFMPEG')
if _env_ffmpeg and not Path(_env_ffmpeg).is_file():
    _env_ffmpeg = None
_vendor_ffmpeg = Path(SPECPATH) / 'vendor' / 'ffmpeg.exe'
_vendor_ffmpeg = str(_vendor_ffmpeg) if _vendor_ffmpeg.is_file() else None
_which_ffmpeg = shutil.which('ffmpeg')

_ffmpeg = buildconfig.resolve_ffmpeg(_env_ffmpeg, _vendor_ffmpeg, _which_ffmpeg)
_binaries = []
if _ffmpeg:
    _binaries = [(_ffmpeg, '.')]
    _size = Path(_ffmpeg).stat().st_size
    _mb = _size / 1e6
    if buildconfig.ffmpeg_is_oversized(_size):
        print('=' * 78)
        print(f'[build.spec] WARNING: ffmpeg is {_mb:.0f} MB -- that is a FULL build.')
        print(f'[build.spec] It is re-extracted on EVERY launch. Expected <= {buildconfig.FFMPEG_MAX_MB} MB.')
        print('[build.spec]   winget install Gyan.FFmpeg.Essentials')
        print('[build.spec]   set SDPRS_FFMPEG=<path to the essentials ffmpeg.exe>')
        print('=' * 78)
    else:
        print(f'[build.spec] ffmpeg {_mb:.0f} MB from {_ffmpeg}')
else:
    print('[build.spec] WARNING: ffmpeg not found on PATH; exe will require '
          'ffmpeg on the target PC PATH for live view (snapshots still work)')

# Binaries this client provably never calls (opencv_videoio_ffmpeg*, PIL's
# _avif codec). Each one is decompressed and written to %TEMP% on every
# single launch -- see buildconfig.EXCLUDED_BINARIES for the measured sizes
# and the why-we-never-call-this reasoning behind each one.

# Build the launcher (app.py), NOT the package module main.py. PyInstaller runs
# the entry as __main__, which has no parent package -- main.py's relative
# imports (`from .config import ...`) would then crash the exe at startup. app.py
# imports the package absolutely. pathex includes the package's PARENT dir so
# `import webcam_client` resolves and the whole package is collected (keeping
# every submodule's relative imports valid).
a = Analysis(
    ['app.py'],
    pathex=[str(Path(SPECPATH).parent)],
    binaries=_binaries,
    datas=[],
    hiddenimports=['webcam_client', 'cv2', 'numpy', 'httpx', 'pystray', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'PIL._avif'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# `excludes` only reaches modules the analyser resolved as imports; these ship as
# plain DLL/pyd payload, so filter the binaries list directly via
# buildconfig.filter_binaries (unit-tested against a realistic fixture list).
# PyInstaller 6.x treats a.binaries as a plain list of (name, path, typecode)
# tuples.
_before = len(a.binaries)
a.binaries = buildconfig.filter_binaries(a.binaries)
print(f'[build.spec] dropped {_before - len(a.binaries)} excluded binaries')

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SDPRS_Webcam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # skip UPX: its per-launch decompression slows onefile cold start
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
