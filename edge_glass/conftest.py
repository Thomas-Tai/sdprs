# conftest.py — makes edge_glass modules importable under pytest regardless of
# the directory pytest is invoked from.
#
# edge_glass/ has an __init__.py, so pytest's automatic "prepend" mechanism
# walks past it to the repo root and inserts THAT into sys.path, never
# edge_glass/ itself. The test modules import their subpackages as top-level
# (`from detectors.trigger_engine import ...`, `from utils.mp4_encoder import
# ...`, `from comms.mqtt_client import ...`, `from buffer.circular_buffer
# import ...`, `from edge_glass_main import ...`), so every one of them failed
# at COLLECTION with ModuleNotFoundError — all 12 suites, silently, for as long
# as the package has had its __init__.py.
#
# Insert edge_glass/ explicitly. This is the identical fix edge_pump/conftest.py
# already carries for the identical cause; edge_glass simply never got it.
#
# The repo root stays on sys.path via pytest's own prepend, which is what makes
# `from shared.mqtt_topics import ...` resolve — so this file deliberately adds
# only edge_glass/, not the root.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
