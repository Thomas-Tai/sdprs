import json
import os

import control_logic
# NOT `import golden_grid`. conftest.py puts only edge_pump/ on sys.path and
# tests/ is a package (it has __init__.py), so tests/ itself is NOT on the
# path — a bare top-level import fails at COLLECTION time, taking both tests
# in this file with it. The generator script gets away with `import
# golden_grid` because it inserts tests/ into sys.path itself.
from tests import golden_grid


def _load_baseline():
    assert os.path.exists(golden_grid.GOLDEN_PATH), (
        "golden baseline missing — run tools/gen_decide_golden.py")
    with open(golden_grid.GOLDEN_PATH, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_grid_size_is_stable():
    # Guards against an accidental grid edit silently shrinking coverage.
    assert len(list(golden_grid.iter_cases())) == 17 * 7 * 3 * 3 * 3


def test_decide_matches_golden_baseline():
    baseline = _load_baseline()
    live = [golden_grid.record(cid, control_logic.decide(
                r, t, s, control_logic.DEFAULT_CONFIG))
            for cid, r, t, s in golden_grid.iter_cases()]

    assert len(live) == len(baseline), (
        "case count changed: grid edited without regenerating the baseline")

    diffs = [(b["case"], b, l) for b, l in zip(baseline, live) if b != l]
    assert not diffs, "behaviour changed in %d case(s); first: %s\n  was: %s\n  now: %s" % (
        len(diffs), diffs[0][0], diffs[0][1], diffs[0][2])
