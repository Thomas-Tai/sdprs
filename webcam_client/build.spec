# sdprs/webcam_client/build.spec
# -*- mode: python ; coding: utf-8 -*-
import shutil
import sys
from pathlib import Path

block_cipher = None

# Bundle ffmpeg so the packaged exe is fully standalone INCLUDING live view.
# hls_encoder._resolve_ffmpeg() prefers a bundled ffmpeg.exe (unpacked into
# sys._MEIPASS at runtime) over PATH. We locate it on the BUILD machine's PATH;
# if it is missing we still build, but the resulting exe will need ffmpeg on the
# TARGET PC's PATH for live view (1Hz snapshots work regardless). Use a static
# ffmpeg build (single self-contained ffmpeg.exe, no side-car DLLs).
_ffmpeg = shutil.which('ffmpeg')
_binaries = [(_ffmpeg, '.')] if _ffmpeg else []
if _ffmpeg:
    print(f'[build.spec] bundling ffmpeg from {_ffmpeg} -> standalone live view')
else:
    print('[build.spec] WARNING: ffmpeg not found on PATH; exe will require '
          'ffmpeg on the target PC PATH for live view (snapshots still work)')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_binaries,
    datas=[],
    hiddenimports=['cv2', 'numpy', 'httpx', 'pystray', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    upx=True,
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
