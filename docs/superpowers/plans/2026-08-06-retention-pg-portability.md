# Retention PostgreSQL portability — SDD plan

**Spec:** `docs/superpowers/specs/2026-08-06-retention-pg-portability-design.md`
**Branch:** `feat/retention-pg-portability-2026-08-06` (worktree off `main` `ea96dc8`).
**Method:** Subagent-Driven Development. Controller orchestrates; subagents (Sonnet/Haiku) make edits; controller independently verifies each diff + RED→GREEN (A/B revert) before marking complete. One final whole-branch review → fix wave → finishing-a-development-branch. Build with subagents unless a session cap forces the controller to apply a fully-specified edit directly (still strict TDD).

Ordering is TDD-sound: the events path (the primary retention target) lands first behind a `get_backend()` branch, then `pump_readings` + the surviving-refs/orphan-sweep feed, then review.

---

## Task 1 — Backend-aware events retention (PG branch, native TIMESTAMP)

**Files:** `central_server/services/retention_service.py`; test `central_server/tests/test_retention_pg_dispatch.py` (new).
**Change:** in `run_retention_cleanup`, branch the DB section on `database.get_backend()`.
- **SQLite path:** unchanged (`sqlite3.connect(db_path)` + the two `datetime(created_at) < datetime(?)` statements for the events SELECT + DELETE).
- **PG path:** via `database._get_engine()` + `sqlalchemy.text()` — `SELECT id, mp4_path FROM events WHERE created_at < :cutoff` (collect mp4 paths) then `DELETE FROM events WHERE created_at < :cutoff` (rowcount). `:cutoff` = the naive-UTC `cutoff` datetime. **No `datetime()`.** MP4 file deletion + stats dict identical to the SQLite path.

**RED test (`test_retention_pg_dispatch.py`, new):** monkeypatch `database._backend = "postgresql"` and `database._get_engine` to a fake engine whose connection records executed SQL and returns canned expired rows `[(1, "/tmp/a.mp4"), …]`. Call `run_retention_cleanup(":memory:", tmp_storage, retention_days=30)`. Assert: (a) `sqlite3.connect` was **not** used for the events work (the fake engine received the SELECT+DELETE), (b) the executed events SQL contains `created_at <` and **not** `datetime(`, (c) `result["deleted_events"]` matches the fake DELETE rowcount and the returned mp4 paths were passed to file deletion.

**Controller RED proof:** on current code the fake engine is never touched (retention always `sqlite3.connect`s) → the "engine received datetime()-free events SQL" assertion fails; after the branch → pass. Restore-and-confirm-green after.

## Task 2 — PG pump_readings prune + surviving-refs (orphan sweep) parity

**Files:** `central_server/services/retention_service.py`; extend `test_retention_pg_dispatch.py`.
**Change:** in the PG branch add `DELETE FROM pump_readings WHERE timestamp < :cutoff` (rowcount → `deleted_pump_readings`; tolerate a missing table the way the SQLite branch tolerates `OperationalError`), and `SELECT mp4_path FROM events WHERE mp4_path IS NOT NULL` to build `surviving_refs` so the existing (backend-agnostic) on-disk orphan sweep runs identically under PG.

**RED tests (extend):** with the fake engine returning a canned pump-readings rowcount and a canned surviving-`mp4_path` set — assert `result["deleted_pump_readings"]` matches, the pump SQL contains `timestamp <` and no `datetime(`, and the orphan sweep receives the PG surviving-refs (an on-disk MP4 not in the surviving set is swept; one in it survives). Confirm the returned stats dict has the full key set on the PG path.

**Controller RED proof:** before the change the PG branch has no pump/surviving handling → the pump-count / surviving-refs assertions fail; after → pass.

## Task 3 — Final review → gate → finishing-branch

- Controller whole-branch review of the full diff (correctness of the PG SQL + bind, SQLite path provably untouched, no `datetime(` on the PG branch, no banned strings, no scope creep into other paths).
- Fix wave for any findings; scoped re-review.
- Gates: backend `pytest` green — **all existing `test_retention.py` + `test_retention_subdirs.py` unchanged/green** (proves zero SQLite regression) + the new `test_retention_pg_dispatch.py` green; watch the per-suite trap (`test_lightning_lifespan` / `test_node_allowlist` pass in isolation).
- `finishing-a-development-branch`: verify tests → present the 3-option menu → **await literal "approved" before any origin/main push**.

---

### Notes
- The PG SQL lives in retention_service's new branch reusing `database._get_engine()` (the established sync-PG pattern); no new public database.py surface needed. If a small `_pg_execute_sync(sql, params) -> rowcount` reads cleaner than inline engine use, it may be added to database.py — controller's call at implementation, kept minimal.
- `run_retention_cleanup` keeps its `db_path` parameter (ignored on the PG branch) so `setup_retention_scheduler`'s call site is untouched.
- **No real/ephemeral PostgreSQL** is introduced — the PG path is proven by the mocked-engine dispatch test only (live-PG smoke test deferred per scope).
