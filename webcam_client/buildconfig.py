"""Pure, importable packaging-decision logic for build.spec.

build.spec cannot be imported normally by pytest -- it depends on
PyInstaller-injected globals (SPECPATH, Analysis, EXE, block_cipher, ...)
that only exist while PyInstaller is executing the spec file. The actual
DECISIONS that need real test coverage -- which binaries get dropped, which
ffmpeg wins when more than one is available, what counts as an oversized
ffmpeg build -- live here instead, as plain functions with no PyInstaller or
filesystem/env dependency, so tests can exercise the real logic directly.

build.spec imports this module and is reduced to a thin caller: it reads
os.environ, computes the vendor path from SPECPATH, calls shutil.which, and
passes the results into resolve_ffmpeg(...)/ffmpeg_is_oversized(...)/
filter_binaries(...). All side-effecting reads (env vars, disk stats, PATH
lookup) stay in build.spec; everything here is pure.
"""

# onefile re-extracts the ENTIRE payload to %TEMP% on every launch, so payload
# size IS startup time. Measured 2026-07-26: the build machine's PATH ffmpeg
# was the 227MB *full* build = 55.5% of a 409MB payload, and nobody noticed
# because nothing checked. The `essentials` build (~85-100MB) has everything
# h264/HLS needs:
#     winget install Gyan.FFmpeg.Essentials
FFMPEG_MAX_MB = 120

# Binaries this client provably never calls. Each one is decompressed and
# written to %TEMP% on every single launch.
#   opencv_videoio_ffmpeg*  28.6MB  OpenCV's video-FILE i/o backend. We only
#                                   use VideoCapture(index, CAP_DSHOW) for
#                                   live capture plus resize/imencode/
#                                   cvtColor/GaussianBlur/absdiff. No file
#                                   i/o anywhere.
#   _avif                    7.8MB  PIL AVIF codec. PIL draws a 64x64 tray
#                                   circle and one Tk thumbnail.
#   libscipy_openblas64_*   20.4MB  DO NOT ADD. Attempted and rejected
#                                   2026-07-30: it is a hard (non-delay-loaded)
#                                   import-table entry of numpy's
#                                   _multiarray_umath.pyd, so the Windows loader
#                                   needs it before any Python runs. Excluding it
#                                   builds fine and cuts the payload to 227.1 MB,
#                                   then the exe dies at startup with "DLL load
#                                   failed while importing _multiarray_umath"
#                                   (cv2 imports numpy). 20 MB is not worth the
#                                   client failing to start.
EXCLUDED_BINARIES = ('opencv_videoio_ffmpeg', '_avif')


def should_exclude_binary(name: str) -> bool:
    """True if ``name`` (a binary's bundle path/name) matches one of
    EXCLUDED_BINARIES, case-insensitively."""
    lowered = name.lower()
    return any(pattern in lowered for pattern in EXCLUDED_BINARIES)


def filter_binaries(binaries):
    """Drop entries matching EXCLUDED_BINARIES; keep everything else.

    ``binaries`` is PyInstaller 6.x's ``a.binaries``: an iterable of
    ``(name, path, typecode)`` tuples (or any sequence whose first element
    is the bundle name). Returns a new list; does not mutate the input.
    """
    return [b for b in binaries if not should_exclude_binary(b[0])]


def resolve_ffmpeg(env_value, vendor_path, which_result):
    """Pick the ffmpeg binary to bundle, in documented precedence order:
    explicit SDPRS_FFMPEG override > vendored vendor/ffmpeg.exe > whatever
    ``shutil.which('ffmpeg')`` finds on the build machine's PATH.

    Pure: this function does no os.environ / Path.is_file / shutil.which
    reads itself -- the caller resolves each candidate (confirming it
    actually exists on disk where relevant) and passes in the result, so
    this stays trivially testable with plain values.

    ``env_value``: the resolved SDPRS_FFMPEG path, or a falsy value
        (None/'') if unset or if the caller found it doesn't point at a
        real file -- a falsy env value falls through to the next
        candidate rather than being returned.
    ``vendor_path``: the vendored ffmpeg.exe path if it exists on disk,
        else a falsy value.
    ``which_result``: ``shutil.which('ffmpeg')``'s result, or None.
    """
    if env_value:
        return env_value
    if vendor_path:
        return vendor_path
    return which_result


def ffmpeg_is_oversized(size_bytes: int) -> bool:
    """True if a binary of this size exceeds FFMPEG_MAX_MB."""
    return (size_bytes / 1e6) > FFMPEG_MAX_MB
