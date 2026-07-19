# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Weather config persistence regression tests.

Guards against the data-loss bug where the DB init path wiped the operator's
configured weather location (site_lat / site_lon / station_name) on EVERY
server restart. The fix removed an unconditional
`UPDATE weather_config SET site_lat = NULL ...` from the PostgreSQL init path;
the SQLite path was already schema-guarded and non-destructive.

The functional test below proves the end-to-end invariant on SQLite: writing a
config via the public API and then re-running init_db (i.e. a restart) must NOT
clear it. The source-level guard directly asserts the PG init path no longer
contains an unconditional wipe (the SQLite functional test can't cover the PG
branch since it never runs against Postgres in CI).
"""

# This project's tests import via the `central_server.` package prefix with
# the sdprs repo root on sys.path (matches tests/test_pump_readings_columns.py
# and tests/test_alerts_api.py — there is no conftest.py). A bare
# `import database` does NOT resolve under pytest here.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import inspect

from central_server import database


def test_reinit_preserves_weather_config(tmp_path, monkeypatch):
    """Re-running init_db (simulating a server restart) must keep an existing
    weather location config intact — proving DB setup is non-destructive."""
    db_file = str(tmp_path / "weather.db")
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite mode

    # First boot: init + operator configures a location via the public API.
    database.init_db(db_file)
    assert database.set_weather_config(22.15, 113.55, "Macau Peninsula") is True

    cfg = database.get_weather_config()
    # Assert on the fields this test is responsible for; the Option-C
    # selector fields (smg_station, hko_station, fallback_provider) are
    # covered by test_option_c_config_round_trip below.
    assert cfg["site_lat"] == 22.15
    assert cfg["site_lon"] == 113.55
    assert cfg["station_name"] == "Macau Peninsula"

    # Second boot: re-run the exact startup init path against the same DB file.
    database.init_db(db_file)

    # The location must survive the restart (would fail if a wipe were present).
    cfg_after = database.get_weather_config()
    assert cfg_after["site_lat"] == 22.15
    assert cfg_after["site_lon"] == 113.55
    assert cfg_after["station_name"] == "Macau Peninsula"


def test_fresh_install_weather_config_is_empty(tmp_path, monkeypatch):
    """A brand-new install has no location configured (SMG Macau XML only until
    the operator sets Open-Meteo) — the intended default, unaffected by the fix."""
    db_file = str(tmp_path / "fresh.db")
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite mode

    database.init_db(db_file)

    cfg = database.get_weather_config()
    # All selector fields default to None on a fresh install (SMG Macau
    # only mode until operator configures via the settings pane).
    assert cfg == {
        "site_lat": None, "site_lon": None, "station_name": None,
        "smg_station": None, "hko_station": None, "fallback_provider": None,
    }


def test_option_c_config_round_trip(tmp_path, monkeypatch):
    """Option C (2026-07-19): the 3 new multi-source selector fields —
    smg_station, hko_station, fallback_provider — round-trip through
    set/get and survive re-init."""
    db_file = str(tmp_path / "option_c.db")
    monkeypatch.setenv("DATABASE_URL", "")

    database.init_db(db_file)
    assert database.set_weather_config(
        22.15, 113.55, "Macau Peninsula",
        smg_station="澳門大學",
        hko_station="Central Weather Station",
        fallback_provider="hko",
    ) is True

    cfg = database.get_weather_config()
    assert cfg["smg_station"] == "澳門大學"
    assert cfg["hko_station"] == "Central Weather Station"
    assert cfg["fallback_provider"] == "hko"

    # Re-init keeps the selectors too.
    database.init_db(db_file)
    cfg_after = database.get_weather_config()
    assert cfg_after["smg_station"] == "澳門大學"
    assert cfg_after["hko_station"] == "Central Weather Station"
    assert cfg_after["fallback_provider"] == "hko"


def test_option_c_migration_adds_columns_to_pre_option_c_db(tmp_path, monkeypatch):
    """Simulates upgrading from a pre-Option-C DB (only site_lat/lon/
    station_name columns). init_db must ALTER TABLE ADD COLUMN
    idempotently so the schema catches up without wiping existing data."""
    db_file = str(tmp_path / "pre_c.db")
    monkeypatch.setenv("DATABASE_URL", "")

    # Manually create the pre-Option-C schema
    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE weather_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            site_lat REAL DEFAULT NULL,
            site_lon REAL DEFAULT NULL,
            station_name TEXT DEFAULT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "INSERT INTO weather_config (id, site_lat, site_lon, station_name) VALUES (1, ?, ?, ?);",
        (22.15, 113.55, "Pre-migration Location"),
    )
    conn.commit()
    conn.close()

    # Now boot the app — migration should add the 3 new columns.
    database.init_db(db_file)

    # Existing row survives + new columns readable as NULL
    cfg = database.get_weather_config()
    assert cfg["site_lat"] == 22.15
    assert cfg["site_lon"] == 113.55
    assert cfg["station_name"] == "Pre-migration Location"
    assert cfg["smg_station"] is None
    assert cfg["hko_station"] is None
    assert cfg["fallback_provider"] is None

    # And writing to the new columns works
    database.set_weather_config(
        22.15, 113.55, "Pre-migration Location",
        smg_station="外港", hko_station="Hong Kong Observatory",
        fallback_provider="openmeteo",
    )
    cfg2 = database.get_weather_config()
    assert cfg2["smg_station"] == "外港"
    assert cfg2["hko_station"] == "Hong Kong Observatory"
    assert cfg2["fallback_provider"] == "openmeteo"


def test_pg_init_has_no_unconditional_weather_wipe():
    """Source-level guard for the PostgreSQL init path (not exercised by the
    SQLite functional test): the every-startup wipe must stay removed."""
    src = inspect.getsource(database._create_tables_postgresql)
    assert "UPDATE weather_config SET site_lat = NULL" not in src, (
        "Unconditional weather_config wipe re-introduced in the PostgreSQL "
        "init path — this destroys the operator's configured location on every "
        "restart."
    )
