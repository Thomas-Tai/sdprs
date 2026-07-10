# conftest.py — makes edge_pump modules importable under pytest.
# Presence of this file at the edge_pump/ root puts that dir on sys.path
# (pytest "prepend" import mode), so `from control_logic import decide` works.
