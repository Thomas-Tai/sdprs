# Retention PostgreSQL portability — design

**Date:** 2026-08-06
**Track:** Track 3 of the 4-track backlog sweep ("Postgres portability" — the Theme-1 / T4 tail). **Scope this slice:** the *code* half only — make `retention_service.run_retention_cleanup` backend-aware. The **live-PG smoke test is explicitly deferred** (needs a real PostgreSQL instance; this box is Windows/SQLite-LAN). PG SQL correctness is proven here via a mocked-engine dispatch test, not a live database.
**Branch:** `feat/retention-pg-portability-2026-08-06` (worktree off `main` `ea96dc8`).

## Problem

`central_server/services/retention_service.py::run_retention_cleanup` is the **last production path that is hardcoded to SQLite**. The request paths were made PG-safe in `e73718d` (Theme 1), but retention was left behind:

- It opens its **own** connection with `sqlite3.connect(db_path)` (retention_service.py:125) — it never consults `database.get_backend()`.
- Its three timestamp comparisons use the SQLite-only `datetime()` function:
  - `SELECT id, mp4_path FROM events WHERE datetime(created_at) < datetime(?)` (:147)
  - `DELETE FROM events WHERE datetime(created_at) < datetime(?)` (:163)
  - `DELETE FROM pump_readings WHERE datetime(timestamp) < datetime(?)` (:187)

PostgreSQL has **no `datetime()` function**, so under a PG deployment (`DATABASE_URL` set) retention would either connect to a nonexistent SQLite file or emit invalid SQL — i.e. **retention never runs, and events / `pump_readings` grow unbounded** on the very deployment (24/7 cloud) where that matters most.

## Key finding — PG columns are native TIMESTAMP

`_create_tables_postgresql` (database.py:331) declares `events.created_at`, `events.acknowledged_at/resolved_at`, and `pump_readings.timestamp` as native **`TIMESTAMP`** columns (not TEXT). So the PG branch needs **no date function at all** — a parameterized `WHERE created_at < :cutoff` binding the naive-UTC `cutoff` datetime compares natively. The `datetime()` wrapper exists only because SQLite stores `CURRENT_TIMESTAMP` as a space-delimited *string* and must normalize it against the `T`-delimited `cutoff.isoformat()`; that problem does not exist in PG.

The rows are written naive-UTC (`central_server.timeutil.utcnow()`), and `cutoff = utcnow() - timedelta(days=…)` is likewise naive-UTC, so the comparison is apples-to-apples on both backends.

## Approach

Branch the **database section** of `run_retention_cleanup` on `database.get_backend()`; leave the on-disk sweep (orphaned-MP4 + empty-dir cleanup) exactly as-is (it is backend-agnostic — it only needs the surviving-`mp4_path` set).

- **SQLite path (default, unchanged):** the current `sqlite3.connect(db_path)` + `datetime()` SQL. **Byte-for-byte the existing behavior** — all 13 `test_retention.py` + `test_retention_subdirs.py` cases stay green, zero regression, delimiter-robustness preserved.
- **PostgreSQL path (new):** use `database._get_engine()` + `sqlalchemy.text()` (mirroring the existing `_pg_*_sync` idiom in database.py) to run, with **native TIMESTAMP comparison, no `datetime()`**:
  1. `SELECT id, mp4_path FROM events WHERE created_at < :cutoff` — collect `mp4_path`s to delete on disk.
  2. `DELETE FROM events WHERE created_at < :cutoff` — capture rowcount.
  3. `DELETE FROM pump_readings WHERE timestamp < :cutoff` — capture rowcount (tolerate a missing table like the SQLite branch tolerates `OperationalError`).
  4. `SELECT mp4_path FROM events WHERE mp4_path IS NOT NULL` — the surviving-refs set that feeds the orphan sweep (same role as the SQLite branch's post-delete SELECT).
  The `:cutoff` bind is the naive-UTC `cutoff` datetime.

Both paths return the **identical stats dict** (`deleted_events`, `deleted_files`, `deleted_pump_readings`, `deleted_orphans`, `deleted_dirs`, `errors`) so every caller (the APScheduler job `retention_cleanup`, tests) is unaffected.

### Where the PG SQL lives
The PG statements live in the retention module's new branch (or a small `_pg_retention_*` helper), reusing `database._get_engine()`. This keeps retention's multi-step transaction (select → delete → select-surviving) coherent in one place while matching the established sync-PG pattern. No new public database.py surface is required beyond what already exists.

## Testing (no live PostgreSQL)

- **SQLite regression:** the full existing retention suites must stay green untouched — proves the default path is byte-for-byte unchanged.
- **PG dispatch (mocked engine):** force `database._backend = "postgresql"` and inject a fake engine/connection that records executed SQL + returns canned rows, then call `run_retention_cleanup`. Assert: (a) the PG branch is taken (no `sqlite3.connect`), (b) the emitted SQL contains a native `created_at < ` / `timestamp < ` comparison and **does NOT contain `datetime(`**, (c) the returned stats dict matches the fake rows (deleted counts, mp4 file deletions, surviving-refs feeding the orphan sweep). Mirrors `test_dual_backend_dispatch.py`'s approach.
- **RED proof:** on current code the PG-dispatch test fails (retention always calls `sqlite3.connect`, never touches the engine); GREEN after the branch lands.

## Non-goals / deferred
- **Live-PG smoke test** against a real/ephemeral PostgreSQL (`testing.postgresql` / Docker) — deferred to actual cloud-cutover time per the user's Track-3 scope decision. No new test dependency is added here.
- **Retention scheduler / `db_path` plumbing under PG** — `run_retention_cleanup` still accepts `db_path` (ignored on the PG branch); no change to `setup_retention_scheduler`'s call contract.
- **Other SQLite-only paths** — request paths are already PG-safe (`e73718d`); no audit of new drift in this slice.

## Invariants / discipline
- Naive-UTC only via `central_server.timeutil.utcnow()`; no tz-aware datetimes.
- No new production dependency; no new test dependency (mocked engine only).
- Banned strings (`Msc@***` / `MSC-***` / the EMQX public broker) must never appear.
- Strict TDD: the PG-dispatch test fails RED before the branch, passes GREEN after; controller A/B-verifies.
- Nothing reaches `origin/main` without the literal "approved".

## Follow-up (post-merge, out of scope)
When a real cloud cutover is scheduled: stand up an ephemeral Postgres and run retention end-to-end (create tables → insert aged rows → cleanup → assert deletes) to close the live-PG gap, plus the same for the already-unit-tested dual-backend dispatch.
