# sdprs/webcam_client/app.py
"""Frozen-exe entry point (PyInstaller builds THIS, not main.py).

PyInstaller runs the entry script as ``__main__``, which has no parent package.
``main.py`` uses relative imports (``from .config import ...``), so using it as
the entry made the packaged exe crash at startup with

    ImportError: attempted relative import with no known parent package

This launcher avoids that: it makes the ``webcam_client`` package importable and
imports it ABSOLUTELY, so every module keeps its package context and its
relative imports resolve. ``--check`` resolves the imports and exits 0 without
launching the GUI — a smoke test for a freshly built exe.
"""
import os
import sys


def _ensure_package_importable():
    # Loose-script / frozen run: put the package's PARENT dir on sys.path so
    # `import webcam_client` resolves. When frozen, PyInstaller already exposes
    # the collected package, so this is a harmless no-op there.
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)


_ensure_package_importable()

from webcam_client.main import main  # noqa: E402 -- must follow the path fix above


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        # Import chain resolved (this line is only reached if it did).
        print("SDPRS_Webcam entry OK: package imports resolved")
        sys.exit(0)
    main()
