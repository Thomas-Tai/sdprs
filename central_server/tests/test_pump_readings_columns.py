# This project's tests import via the `central_server.` package prefix with
# the sdprs repo root on sys.path (matches tests/test_alerts_api.py and
# tests/test_retention.py — there is no conftest.py). A bare `import database`
# does NOT resolve under pytest here.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server import database


def test_insert_pump_reading_accepts_new_flags(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite mode
    database.init_db(db_file)
    database.insert_pump_reading("pump_node_01", "2026-07-10T00:00:00Z",
                                 82.4, "ON", raining=True, sensor_conflict=False)
    rows = database.get_pump_readings("pump_node_01",
                                      "2026-07-09T00:00:00Z", "2026-07-11T00:00:00Z")
    assert rows and rows[-1]["raining"] in (1, True)
    assert rows[-1]["sensor_conflict"] in (0, False)
