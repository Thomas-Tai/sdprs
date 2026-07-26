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
_FFMPEG_MAX_MB = 120


def _resolve_ffmpeg():
    explicit = os.environ.get('SDPRS_FFMPEG')
    if explicit and Path(explicit).is_file():
        return explicit
    vendored = Path(SPECPATH) / 'vendor' / 'ffmpeg.exe'
    if vendored.is_file():
        return str(vendored)
    return shutil.which('ffmpeg')


_ffmpeg = _resolve_ffmpeg()
_binaries = []
if _ffmpeg:
    _binaries = [(_ffmpeg, '.')]
    _mb = Path(_ffmpeg).stat().st_size / 1e6
    if _mb > _FFMPEG_MAX_MB:
        print('=' * 78)
        print(f'[build.spec] WARNING: ffmpeg is {_mb:.0f} MB -- that is a FULL build.')
        print(f'[build.spec] It is re-extracted on EVERY launch. Expected <= {_FFMPEG_MAX_MB} MB.')
        print('[build.spec]   winget install Gyan.FFmpeg.Essentials')
        print('[build.spec]   set SDPRS_FFMPEG=<path to the essentials ffmpeg.exe>')
        print('=' * 78)
    else:
        print(f'[build.spec] ffmpeg {_mb:.0f} MB from {_ffmpeg}')
else:
    print('[build.spec] WARNING: ffmpeg not found on PATH; exe will require '
          'ffmpeg on the target PC PATH for live view (snapshots still work)')

# Binaries this client provably never calls. Each one is decompressed and written
# to %TEMP% on every single launch.
#   opencv_videoio_ffmpeg*  28.6MB  OpenCV's video-FILE i/o backend. We only use
#                                   VideoCapture(index, CAP_DSHOW) for live
#                                   capture plus resize/imencode/cvtColor/
#                                   GaussianBlur/absdiff. No file i/o anywhere.
#   _avif                    7.8MB  PIL AVIF codec. PIL draws a 64x64 tray circle
#                                   and one Tk thumbnail.
_EXCLUDED_BINARIES = ('opencv_videoio_ffmpeg', '_avif')

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
# plain DLL/pyd payload, so filter the binaries list directly. PyInstaller 6.x
# treats a.binaries as a plain list of (name, path, typecode) tuples.
_before = len(a.binaries)
a.binaries = [b for b in a.binaries
              if not any(p in b[0].lower() for p in _EXCLUDED_BINARIES)]
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
