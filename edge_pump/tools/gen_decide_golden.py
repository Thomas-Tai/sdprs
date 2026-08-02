# -*- coding: utf-8 -*-
"""Regenerate the decide() golden baseline.

    cd edge_pump && /c/Python314/python tools/gen_decide_golden.py

Run this ONLY when a behaviour change is intended. After running, inspect
`git diff` on the baseline: every changed line is a behaviour change you
are asserting is correct. A refactor that changes even one line is not a
refactor (spec §8.1.1).
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # edge_pump/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "tests"))

import control_logic                                 # noqa: E402
import golden_grid                                   # noqa: E402


def main():
    os.makedirs(os.path.dirname(golden_grid.GOLDEN_PATH), exist_ok=True)
    n = 0
    with open(golden_grid.GOLDEN_PATH, "w", encoding="utf-8", newline="\n") as fh:
        for cid, readings, timing, state in golden_grid.iter_cases():
            decision = control_logic.decide(readings, timing, state,
                                            control_logic.DEFAULT_CONFIG)
            fh.write(json.dumps(golden_grid.record(cid, decision),
                                sort_keys=True, ensure_ascii=False))
            fh.write("\n")
            n += 1
    print("wrote %d cases -> %s" % (n, golden_grid.GOLDEN_PATH))


if __name__ == "__main__":
    main()
