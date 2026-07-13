# conftest.py — makes edge_pump modules importable under pytest regardless of
# the directory pytest is invoked from. edge_pump/ has an __init__.py (it is a
# MicroPython package), so pytest's automatic "prepend" mechanism walks past it
# to the repo root instead of adding edge_pump/ itself. Insert edge_pump/
# explicitly so `from control_logic import decide` and `from tests.fakes import ...`
# resolve from any cwd — the convention central_server/tests already use.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
