# Per-Node Edge API Keys — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each edge-glass node its own API key (bound to its `node_id`), extending the proven `webcam_clients` per-key pattern, with a grace-period fallback to the shared `EDGE_API_KEY`.

**Architecture:** Add `api_key_hash` to the `nodes` table; SHA-256 key lookup binds a key to one node; `verify_api_key` checks the per-node key first and falls back to the shared key while it is set; the two write ingest paths enforce that a per-node key may only POST under its own node. Session-protected provisioning API + SPA panel mirror the existing webcam-key UX.

**Tech Stack:** FastAPI, dual-backend DB (SQLite + PostgreSQL via `get_backend()`), React SPA (in-browser Babel, no build step).

**Spec:** `docs/superpowers/specs/2026-08-05-per-node-edge-keys-design.md`

## Global Constraints

- **Dual-backend:** EVERY new DB helper has BOTH a `get_backend() == "postgresql"` branch and a SQLite (`get_db_cursor()`) branch, mirroring the webcam helpers at `database.py` `create_webcam_client`/`get_webcam_client_by_key`/`revoke_webcam_key`. Schema change mirrored in the SQLite CREATE/migration block AND `_create_tables_postgresql`.
- **Keys:** raw = `sk-edge-<secrets.token_urlsafe(32)>`; store ONLY `hashlib.sha256(key.encode()).hexdigest()`. NEVER log, return (beyond the one provisioning response), or persist the raw key.
- **Constant-time** shared-key compare via `auth._ct_equal`.
- **Datetime:** naive-UTC via `central_server.timeutil.utcnow()` only. Never `datetime.utcnow()` / tz-aware.
- **Banned strings** — must never appear in any diff: `Msc@2333`, `MSC-Person`, `broker.emqx.io`. No hardcoded credentials.
- **Backward compatibility:** the shared `EDGE_API_KEY` MUST keep working during the grace period. Existing suites (`test_auth_hardening`, `test_webcam_auth`, `test_node_allowlist`, `test_alerts_api`, `test_snapshot_api`, `test_nodes_api`) MUST stay green.
- **SPA:** no build step / in-browser Babel, no new deps, no `import`/`require`; driven through `window.SDPRS_API`; covered by a `tools/spa/render_extra/*.js` render test. zh-TW for all user-facing copy.
- **No merge/push to origin/main without the user typing literal "approved".**
- Python tests: `/c/Python314/python -m pytest <path> -v` from repo root. SPA gate: `node tools/spa/run_all.js`.

---

### Task 1: `nodes.api_key_hash` column + index (both backends)

**Files:**
- Modify: `central_server/database.py` (SQLite CREATE `nodes` ~L162 + migration ALTER block ~L176-190 + index block ~L316-324; PostgreSQL `_create_tables_postgresql` nodes CREATE ~L351)
- Test: `central_server/tests/test_edge_node_keys.py` (new)

**Interfaces:**
- Produces: a `nodes.api_key_hash TEXT` column (nullable) and index `idx_nodes_api_key_hash` on both backends. Consumed by Tasks 2-4.

- [ ] **Step 1: Write the failing test**

```python
# central_server/tests/test_edge_node_keys.py
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fresh_db(monkeypatch):
    """Point the DB layer at a fresh temp SQLite file and init the schema."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    from central_server import database
    database.init_db(db_path)
    return database, db_path


def test_nodes_table_has_api_key_hash_and_index(monkeypatch):
    database, db_path = _fresh_db(monkeypatch)
    import sqlite3
    con = sqlite3.connect(db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(nodes)").fetchall()}
    assert "api_key_hash" in cols
    idx = {r[1] for r in con.execute("PRAGMA index_list(nodes)").fetchall()}
    assert "idx_nodes_api_key_hash" in idx
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_nodes_table_has_api_key_hash_and_index -v`
Expected: FAIL — `api_key_hash` not in columns.

> Confirm `init_db(db_path)` is the correct init entrypoint by reading `database.py`; if the signature differs (e.g. `init_database()`), use the real one and adjust `_fresh_db`. The helper must produce a schema-initialised SQLite file.

- [ ] **Step 3: Implement**

In the SQLite `nodes` CREATE TABLE (the `CREATE TABLE IF NOT EXISTS nodes (...)` around L162), add a column line after `power_source    TEXT`:
```
            api_key_hash    TEXT
```
In the migration block (after the `power_source` ALTER, ~L190), add:
```python
    if "api_key_hash" not in existing_cols:            # per-node edge keys
        cursor.execute("ALTER TABLE nodes ADD COLUMN api_key_hash TEXT;")
```
In the SQLite index block (~L316-324, alongside `idx_webcam_clients_api_key_hash`), add:
```python
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_api_key_hash ON nodes(api_key_hash);")
```
In `_create_tables_postgresql` (the `CREATE TABLE IF NOT EXISTS nodes (...)` ~L351), add `api_key_hash TEXT` to the column list, and add the matching index create alongside the PG webcam index (~L466):
```python
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_nodes_api_key_hash ON nodes(api_key_hash);"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/database.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): add nodes.api_key_hash column + index (both backends)"
```

---

### Task 2: `get_edge_node_by_key` helper

**Files:**
- Modify: `central_server/database.py` (add helper near the webcam helpers ~L1404)
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Consumes: `nodes.api_key_hash` (Task 1).
- Produces: `get_edge_node_by_key(api_key: str) -> Optional[dict]` returning `{"node_id", "status"}` or `None`. Consumed by `auth.verify_api_key` (Task 5).

- [ ] **Step 1: Write the failing test**

```python
import hashlib

def test_get_edge_node_by_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    # Seed a node row with a known key hash via a direct upsert.
    database.upsert_node("glass_node_01", "glass", "OFFLINE", None)
    raw = "sk-edge-KNOWNTESTKEY"
    h = hashlib.sha256(raw.encode()).hexdigest()
    from central_server.database import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute("UPDATE nodes SET api_key_hash = ? WHERE node_id = ?", (h, "glass_node_01"))
    assert database.get_edge_node_by_key(raw)["node_id"] == "glass_node_01"
    assert database.get_edge_node_by_key("sk-edge-WRONG") is None
    assert database.get_edge_node_by_key("") is None
```

> Verify `upsert_node`'s real signature in `database.py` before relying on it (it is referenced at `api/nodes.py:730` as `upsert_node(node_id, "glass", "OFFLINE", None)`). If it differs, seed the row with a direct INSERT instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_get_edge_node_by_key -v`
Expected: FAIL — `get_edge_node_by_key` does not exist (AttributeError).

- [ ] **Step 3: Implement** (mirror `get_webcam_client_by_key` at `database.py` ~L1404)

```python
def get_edge_node_by_key(api_key: str) -> Optional[dict]:
    """Look up an edge node by its raw API key (SHA-256 hashed for comparison).

    Returns {"node_id", "status"} or None. An empty/blank key never matches
    (its hash is never stored; a NULL api_key_hash row must not be resolved by
    an empty key)."""
    if not api_key:
        return None
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT node_id, status FROM nodes WHERE api_key_hash = :h",
            {"h": api_key_hash},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT node_id, status FROM nodes WHERE api_key_hash = ?",
            (api_key_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/database.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): get_edge_node_by_key hash lookup (both backends)"
```

---

### Task 3: `provision_edge_node_key` (generate + upsert)

**Files:**
- Modify: `central_server/database.py`
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Produces: `provision_edge_node_key(node_id: str, node_type: str = "glass") -> dict` returning `{"api_key": <raw>}`. UPSERTs the `nodes` row (create if absent with the given `node_type` and `status='OFFLINE'`, else set `api_key_hash`). Consumed by the provisioning API (Task 9).

- [ ] **Step 1: Write the failing test**

```python
def test_provision_edge_node_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    out = database.provision_edge_node_key("glass_node_07")
    assert out["api_key"].startswith("sk-edge-")
    # The freshly provisioned key resolves back to the node.
    assert database.get_edge_node_by_key(out["api_key"])["node_id"] == "glass_node_07"
    # Re-provisioning rotates: the old key stops resolving.
    out2 = database.provision_edge_node_key("glass_node_07")
    assert out2["api_key"] != out["api_key"]
    assert database.get_edge_node_by_key(out["api_key"]) is None
    assert database.get_edge_node_by_key(out2["api_key"])["node_id"] == "glass_node_07"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_provision_edge_node_key -v`
Expected: FAIL — `provision_edge_node_key` does not exist.

- [ ] **Step 3: Implement** (mirror `create_webcam_client` ~L1382, but UPSERT onto `nodes`)

```python
def provision_edge_node_key(node_id: str, node_type: str = "glass") -> dict:
    """Provision (or rotate) a per-node API key for an edge node. Creates the
    nodes row if absent, else sets its api_key_hash. Returns {"api_key"} — the
    raw key is shown ONCE and never stored/returned again."""
    api_key = f"sk-edge-{secrets.token_urlsafe(32)}"
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    now = utcnow().isoformat()
    if get_backend() == "postgresql":
        _pg_execute_sync(
            "INSERT INTO nodes (node_id, node_type, status, api_key_hash) "
            "VALUES (:id, :t, 'OFFLINE', :h) "
            "ON CONFLICT (node_id) DO UPDATE SET api_key_hash = :h",
            {"id": node_id, "t": node_type, "h": api_key_hash},
        )
    else:
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO nodes (node_id, node_type, status, api_key_hash) "
                "VALUES (?, ?, 'OFFLINE', ?) "
                "ON CONFLICT(node_id) DO UPDATE SET api_key_hash = excluded.api_key_hash",
                (node_id, node_type, api_key_hash),
            )
    return {"api_key": api_key}
```

> `now` is computed for parity with the webcam helper but the `nodes` schema has no key-created column; drop the unused local if the reviewer flags it, or set `last_heartbeat` — do NOT add a column in this task. Confirm SQLite `ON CONFLICT(node_id) DO UPDATE` is supported (SQLite ≥ 3.24; the repo already relies on modern SQLite). If the installed SQLite rejects upsert, fall back to a SELECT-then-INSERT/UPDATE within the same cursor.

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/database.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): provision_edge_node_key upsert + key gen"
```

---

### Task 4: `rotate_edge_node_key` + `clear_edge_node_key`

**Files:**
- Modify: `central_server/database.py`
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Produces: `rotate_edge_node_key(node_id) -> dict {"api_key"}` (alias for re-provisioning an EXISTING node — 404-guarded by the caller); `clear_edge_node_key(node_id) -> bool` (set api_key_hash NULL; row remains). Consumed by Task 9.

- [ ] **Step 1: Write the failing test**

```python
def test_clear_edge_node_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    out = database.provision_edge_node_key("glass_node_09")
    assert database.get_edge_node_by_key(out["api_key"])["node_id"] == "glass_node_09"
    assert database.clear_edge_node_key("glass_node_09") is True
    # Key no longer resolves, but the node row still exists.
    assert database.get_edge_node_by_key(out["api_key"]) is None
    assert database.get_node("glass_node_09") is not None
    # Clearing an unknown node reports False.
    assert database.clear_edge_node_key("nope_404") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_clear_edge_node_key -v`
Expected: FAIL — `clear_edge_node_key` does not exist.

- [ ] **Step 3: Implement**

```python
def rotate_edge_node_key(node_id: str) -> dict:
    """Rotate an existing edge node's key (caller 404-guards existence)."""
    return provision_edge_node_key(node_id)


def clear_edge_node_key(node_id: str) -> bool:
    """De-provision: NULL the node's api_key_hash so it falls back to the shared
    key. The node registry row and its telemetry are preserved. Returns True if
    a row was updated, False if no such node."""
    if get_backend() == "postgresql":
        res = _pg_execute_sync(
            "UPDATE nodes SET api_key_hash = NULL WHERE node_id = :id",
            {"id": node_id},
        )
        # _pg_execute_sync returns rowcount if available; treat truthy as updated.
        return bool(res)
    with get_db_cursor() as cursor:
        cursor.execute("UPDATE nodes SET api_key_hash = NULL WHERE node_id = ?", (node_id,))
        return cursor.rowcount > 0
```

> Verify `_pg_execute_sync`'s return contract in `database.py`; if it does not return a rowcount, use a `_pg_fetch_one_sync` existence check before the UPDATE and return that. The SQLite branch is authoritative for the CI test.

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/database.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): rotate + clear edge node key"
```

---

### Task 5: `verify_api_key` per-node-first + shared fallback + identity stamp

**Files:**
- Modify: `central_server/auth.py` (`verify_api_key`)
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Consumes: `database.get_edge_node_by_key` (Task 2).
- Produces: `verify_api_key(request: Request, api_key = Depends(api_key_header)) -> str` — stamps `request.state.edge_auth_node` = the bound `node_id` (per-node key) or `None` (shared key). Return value (the key string) unchanged. Consumed by all edge ingest routes + `verify_node_binding` (Task 7).

- [ ] **Step 1: Write the failing test**

```python
import types, pytest
from fastapi import HTTPException

def _req():
    return types.SimpleNamespace(state=types.SimpleNamespace())

@pytest.mark.asyncio
async def test_verify_api_key_per_node_and_fallback(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth
    from central_server.config import get_settings
    monkeypatch.setattr(get_settings(), "EDGE_API_KEY", "shared-" + "x" * 32, raising=False)

    out = database.provision_edge_node_key("glass_node_11")
    # Per-node key: authenticates AND stamps the bound node identity.
    r1 = _req()
    assert await auth.verify_api_key(r1, out["api_key"]) == out["api_key"]
    assert r1.state.edge_auth_node == "glass_node_11"
    # Shared key: authenticates, identity is None (unbound / grace period).
    r2 = _req()
    assert await auth.verify_api_key(r2, "shared-" + "x" * 32) == "shared-" + "x" * 32
    assert r2.state.edge_auth_node is None
    # Invalid key: 401.
    with pytest.raises(HTTPException) as ei:
        await auth.verify_api_key(_req(), "sk-edge-bogus")
    assert ei.value.status_code == 401
```

> `get_settings()` returns a cached settings object; if `monkeypatch.setattr` on it does not stick (pydantic frozen), set the env var `EDGE_API_KEY` before `_fresh_db` and clear the settings cache, or patch `auth.get_settings` to return a stub. Confirm the repo's async-test convention: if `pytest.mark.asyncio` is unavailable, drive the coroutine with `asyncio.run(...)` as other tests here do.

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_verify_api_key_per_node_and_fallback -v`
Expected: FAIL — current `verify_api_key(api_key)` has no `request` param and does not stamp identity (TypeError / AttributeError).

- [ ] **Step 3: Implement**

Rewrite `verify_api_key` (keep the existing missing-key 401 and the SHA-256-digest invalid log):
```python
async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
) -> str:
    """Verify the X-API-Key header for edge nodes.

    Per-node key is checked first (binding the request to that node via
    request.state.edge_auth_node); the shared EDGE_API_KEY is accepted as a
    fallback while it is set (grace period), leaving edge_auth_node = None."""
    settings = get_settings()
    if api_key is None:
        logger.warning("API key missing in request")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")

    from .database import get_edge_node_by_key
    rec = get_edge_node_by_key(api_key)
    if rec is not None:
        request.state.edge_auth_node = rec["node_id"]
        return api_key

    shared = getattr(settings, "EDGE_API_KEY", "") or ""
    if shared and _ct_equal(api_key, shared):
        request.state.edge_auth_node = None
        return api_key

    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    logger.warning(f"Invalid API key attempt (sha256={key_digest})")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
```

> FastAPI injects `request` for any dependency that declares it, so existing routes using `Depends(verify_api_key)` need no change. Grep for DIRECT callers/tests of `verify_api_key(` and update them to pass a request (there is at least the auth-hardening suite). Do not change the header scheme.

- [ ] **Step 4: Run test + the auth regression suite**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py central_server/tests/test_auth_hardening.py -v`
Expected: PASS (new test green; auth-hardening still green — the shared key still authenticates).

- [ ] **Step 5: Commit**

```bash
git add central_server/auth.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): verify_api_key per-node-first with shared fallback + identity stamp"
```

---

### Task 6: `verify_api_key_or_session` accepts per-node keys

**Files:**
- Modify: `central_server/auth.py` (`verify_api_key_or_session`)
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Produces: `verify_api_key_or_session` accepts a valid per-node key OR the shared key OR a session. Stamps `edge_auth_node` on the key paths (node_id / None). Read paths do not enforce binding.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_verify_api_key_or_session_accepts_per_node(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth
    out = database.provision_edge_node_key("glass_node_13")
    r = types.SimpleNamespace(state=types.SimpleNamespace(),
                              session={}, headers={})
    assert await auth.verify_api_key_or_session(r, out["api_key"]) == out["api_key"]
    assert r.state.edge_auth_node == "glass_node_13"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_verify_api_key_or_session_accepts_per_node -v`
Expected: FAIL — current impl only compares the shared key, so a per-node key falls through to the session check and 401s.

- [ ] **Step 3: Implement**

In `verify_api_key_or_session`, before the shared-key compare, add the per-node lookup:
```python
    if api_key:
        from .database import get_edge_node_by_key
        rec = get_edge_node_by_key(api_key)
        if rec is not None:
            request.state.edge_auth_node = rec["node_id"]
            return api_key
    # Then shared-key fallback (stamp None), then session (unchanged):
    if api_key and _ct_equal(api_key, settings.EDGE_API_KEY):
        request.state.edge_auth_node = None
        return api_key
    ...
```

- [ ] **Step 4: Run test + regression**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py central_server/tests/test_auth_persistence_and_csrf.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/auth.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): verify_api_key_or_session accepts per-node keys"
```

---

### Task 7: `verify_node_binding` + enforce on `POST /alerts`

**Files:**
- Modify: `central_server/auth.py` (new helper), `central_server/api/alerts.py` (`create_alert`)
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Consumes: `request.state.edge_auth_node` (Task 5).
- Produces: `verify_node_binding(request, claimed_node_id)` — 403 when `edge_auth_node` is a specific node ≠ claimed; no-op when None (grace period). Called AFTER `verify_node_id`.

- [ ] **Step 1: Write the failing test** (drives `create_alert` directly, like `test_alerts_api`)

```python
@pytest.mark.asyncio
async def test_alert_binding_rejects_cross_node(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth
    from central_server.api import alerts as alerts_api
    out = database.provision_edge_node_key("glass_node_15")

    # Build a request already authed as glass_node_15 (per-node key).
    req = types.SimpleNamespace(state=types.SimpleNamespace(edge_auth_node="glass_node_15"))
    # Cross-node claim -> 403.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        auth.verify_node_binding(req, "glass_node_99")
    assert ei.value.status_code == 403
    # Same-node claim -> ok (no raise).
    auth.verify_node_binding(req, "glass_node_15")
    # Shared-key (unbound) -> ok for any node.
    auth.verify_node_binding(types.SimpleNamespace(state=types.SimpleNamespace(edge_auth_node=None)), "glass_node_99")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_alert_binding_rejects_cross_node -v`
Expected: FAIL — `verify_node_binding` does not exist.

- [ ] **Step 3: Implement**

In `auth.py`:
```python
def verify_node_binding(request: Request, claimed_node_id: str) -> None:
    """Enforce that a per-node key may only act under its own node_id.

    request.state.edge_auth_node is the authenticated node (set by
    verify_api_key). If it is a specific node and differs from the claimed
    node_id, reject with 403. A None value (shared-key / grace period, or
    session auth) skips the check. Call AFTER verify_node_id."""
    bound = getattr(getattr(request, "state", None), "edge_auth_node", None)
    if bound is not None and bound != claimed_node_id:
        logger.warning(f"Node binding mismatch: key bound to {bound!r}, claimed {claimed_node_id!r}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Node key/id mismatch")
```
Add `verify_node_binding` to `__all__`. In `api/alerts.py::create_alert`, import it and call right after `verify_node_id(alert.node_id)`:
```python
    verify_node_id(alert.node_id)
    verify_node_binding(request, alert.node_id)
```

- [ ] **Step 4: Run test + alerts regression**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py central_server/tests/test_alerts_api.py -v`
Expected: PASS (shared-key alert tests unaffected — those requests have `edge_auth_node=None`).

- [ ] **Step 5: Commit**

```bash
git add central_server/auth.py central_server/api/alerts.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): verify_node_binding + enforce on POST /alerts"
```

---

### Task 8: enforce binding on `POST /edge/{node_id}/snapshot`

**Files:**
- Modify: `central_server/api/snapshots.py` (`receive_snapshot`)
- Test: `central_server/tests/test_edge_node_keys.py`

- [ ] **Step 1: Write the failing test** (drive `receive_snapshot` directly)

```python
@pytest.mark.asyncio
async def test_snapshot_binding_rejects_cross_node(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server.api import snapshots as snap_api
    from fastapi import HTTPException
    req = types.SimpleNamespace(state=types.SimpleNamespace(edge_auth_node="glass_node_21"),
                                app=types.SimpleNamespace(state=types.SimpleNamespace()))
    with pytest.raises(HTTPException) as ei:
        await snap_api.receive_snapshot("glass_node_99", req, api_key="ignored")
    assert ei.value.status_code == 403
```

> `receive_snapshot` calls `request.body()`; a 403 must be raised BEFORE that read, so the binding check goes right after `verify_node_id(node_id)`. If the direct-call test trips on `verify_node_id` allowlist state, confirm `ALLOWED_NODE_IDS` is empty in the test env (default) so it is a no-op.

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_snapshot_binding_rejects_cross_node -v`
Expected: FAIL — no binding check; the handler proceeds to read an empty body and 400s (not 403).

- [ ] **Step 3: Implement**

In `receive_snapshot`, after `verify_node_id(node_id)`:
```python
    from ..auth import verify_node_binding
    verify_node_binding(request, node_id)
```
(Import at module top instead if the file's style prefers it.)

- [ ] **Step 4: Run test + snapshot regression**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py central_server/tests/test_snapshot_api.py central_server/tests/test_snapshot_hardening.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/api/snapshots.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): enforce node binding on snapshot ingest"
```

---

### Task 9: provisioning API + `has_key` on node list

**Files:**
- Modify: `central_server/api/nodes.py` (2 new routes + `NodeStatus.has_key` + set it in `list_nodes` & `get_node`), `central_server/services/audit_service.py` (2 action constants)
- Test: `central_server/tests/test_edge_node_keys.py`

**Interfaces:**
- Produces: `POST /api/nodes/{node_id}/key` → `{"api_key"}` (201, session-only, provision-or-rotate, audit-logged); `DELETE /api/nodes/{node_id}/key` → 204 (clear). `NodeStatus.has_key: Optional[bool]` (True iff `api_key_hash` present). Consumed by the SPA (Tasks 10-11).

- [ ] **Step 1: Write the failing test**

```python
def test_provision_endpoint_and_has_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server.api import nodes as nodes_api
    import asyncio
    database.upsert_node("glass_node_31", "glass", "OFFLINE", None)

    async def go():
        res = await nodes_api.provision_node_key("glass_node_31",
                                                 request=types.SimpleNamespace(),
                                                 user="op")
        assert res["api_key"].startswith("sk-edge-")
        assert database.get_edge_node_by_key(res["api_key"])["node_id"] == "glass_node_31"
        # Clearing returns the node to shared-key fallback.
        await nodes_api.clear_node_key("glass_node_31", user="op")
        assert database.get_edge_node_by_key(res["api_key"]) is None
    asyncio.run(go())

def test_has_key_derived(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    database.provision_edge_node_key("glass_node_33")
    row = database.get_node("glass_node_33")
    assert bool(row.get("api_key_hash")) is True
```

> The real `list_nodes` needs the MQTT service; test `has_key` at the DB level (as above) and/or assert `NodeStatus(has_key=...)` serialization. The endpoint functions are named `provision_node_key` / `clear_node_key` — match these names in the implementation.

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py::test_provision_endpoint_and_has_key -v`
Expected: FAIL — the endpoints don't exist.

- [ ] **Step 3: Implement**

Add action constants in `audit_service.py` (mirror `ACTION_WEBCAM_REVOKE_KEY`): `ACTION_EDGE_KEY_PROVISION = "EDGE_KEY_PROVISION"`, `ACTION_EDGE_KEY_CLEAR = "EDGE_KEY_CLEAR"`.

In `nodes.py` add the routes (mirror `revoke_node_key` at L802):
```python
@router.post("/nodes/{node_id}/key", status_code=201)
async def provision_node_key(node_id: str, request: Request,
                             user: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Provision or rotate a per-node edge API key. Raw key shown ONCE."""
    from ..auth import verify_node_id
    from ..database import provision_edge_node_key
    from ..services.audit_service import log_action, ACTION_EDGE_KEY_PROVISION
    verify_node_id(node_id)
    result = provision_edge_node_key(node_id)
    log_action(user, ACTION_EDGE_KEY_PROVISION, target_id=node_id, details={})
    return {"node_id": node_id, "api_key": result["api_key"]}


@router.delete("/nodes/{node_id}/key", status_code=204)
async def clear_node_key(node_id: str,
                         user: str = Depends(get_current_user)) -> Response:
    """Clear a node's per-node key (returns it to the shared-key fallback)."""
    from ..database import clear_edge_node_key
    from ..services.audit_service import log_action, ACTION_EDGE_KEY_CLEAR
    if not clear_edge_node_key(node_id):
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    log_action(user, ACTION_EDGE_KEY_CLEAR, target_id=node_id, details={})
    return Response(status_code=204)
```
Add `has_key: Optional[bool] = None` to `NodeStatus`. In `list_nodes` and `get_node`, set `has_key=bool(db_row.get("api_key_hash"))` (use the DB row already loaded — `db_nodes.get(node_id)` in the list, `db_node` in the detail). For webcam/clientless rows, leave `has_key=None`.

> `verify_node_id` here rejects malformed ids (400) and enforces the allowlist — desirable for provisioning too. Route ordering: `/nodes/{node_id}/key` must not shadow `/nodes/{node_id}/revoke-key` etc.; FastAPI matches exact suffixes so this is safe, but confirm no `/nodes/{node_id}/{action}` catch-all exists.

- [ ] **Step 4: Run test + nodes regression**

Run: `/c/Python314/python -m pytest central_server/tests/test_edge_node_keys.py central_server/tests/test_nodes_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/api/nodes.py central_server/services/audit_service.py central_server/tests/test_edge_node_keys.py
git commit -m "feat(edge-keys): provisioning API (POST/DELETE /nodes/{id}/key) + has_key"
```

---

### Task 10: SPA api.jsx wiring

**Files:**
- Modify: `central_server/static/spa/api.jsx`
- Test: `tools/spa/render_extra/edge-keys.js` (new)

**Interfaces:**
- Produces: `window.SDPRS_API.provisionNodeKey(nodeId)` → `{api_key}`; `window.SDPRS_API.clearNodeKey(nodeId)`; node objects surface `has_key`. Consumed by Task 11.

- [ ] **Step 1: Write the failing render test** — stub `fetch`, call `SDPRS_API.provisionNodeKey('n1')`, assert it POSTs `/api/nodes/n1/key` and returns the stubbed `{api_key}`. (Model on the existing webcam-key api.jsx test if present; otherwise on `render_extra/wxa004-lightning.js`.)

- [ ] **Step 2: Run** `node tools/spa/render_extra/edge-keys.js` → FAIL (function undefined).

- [ ] **Step 3: Implement** — add `provisionNodeKey`/`clearNodeKey` next to the existing webcam key functions (grep `revoke-key` in api.jsx for the sibling to mirror), POSTing/ DELETEing `/api/nodes/${nodeId}/key`; export both on `window.SDPRS_API`.

- [ ] **Step 4: Run** the render test + `node tools/spa/run_all.js` → PASS / "All blocking SPA checks passed."

- [ ] **Step 5: Commit** `feat(edge-keys): SPA api.jsx provision/clear node key`.

---

### Task 11: SPA node key panel UI

**Files:**
- Modify: `central_server/static/spa/pages/status.jsx` (mirror the existing webcam create/revoke/delete key UX — grep `revoke` / `api_key` in the SPA for the one-time-reveal modal to reuse)
- Test: `tools/spa/render_extra/edge-keys-panel.js` (new)

**Interfaces:**
- Consumes: `SDPRS_API.provisionNodeKey/clearNodeKey`, node `has_key`.

- [ ] **Step 1: Write the failing render test** — render the node row for a glass node; assert it shows a 「設定金鑰」/「重設金鑰」 control gated on `has_key`, and that provisioning surfaces a one-time-key reveal element with the returned key + the 「此金鑰只顯示一次」 warning.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — add the key controls to the glass/pump node card (NOT webcam rows, which keep their existing webcam-key controls): 「設定金鑰」when `!has_key`, 「重設金鑰」+「清除金鑰」when `has_key`; provision → reveal modal (copy-able, one-time warning, zh-TW); clear → confirm. Reuse the webcam reveal component/pattern rather than inventing a new one.

- [ ] **Step 4: Run** the render test + `node tools/spa/run_all.js` → PASS.

- [ ] **Step 5: Commit** `feat(edge-keys): SPA node key management panel`.

---

### Task 12: docs + .env.example annotations

**Files:**
- Modify: `.env.example`, `central_server/.env.example`, `docs/reference/configuration.md`, `docs/deployment/edge-glass.md`

- [ ] **Step 1:** Annotate `EDGE_API_KEY` in both `.env.example` files as the LEGACY shared fallback — still required today, optional once every node is provisioned; unset it to enforce per-node-only auth. (No test — doc-only.)

- [ ] **Step 2:** In `configuration.md` + `edge-glass.md`, document: provision a node (`POST /api/nodes/{id}/key` or the dashboard 「設定金鑰」button), put the returned `sk-edge-…` key in the node's `config.yaml` `server.api_key`, verify it connects, then retire the shared key by unsetting `EDGE_API_KEY`. Note keys are stored hashed and shown once.

- [ ] **Step 3: Commit**

```bash
git add .env.example central_server/.env.example docs/reference/configuration.md docs/deployment/edge-glass.md
git commit -m "docs(edge-keys): document per-node provisioning + legacy fallback"
```

---

## Self-Review notes (for the executor)

- **Dual-backend parity:** the SQLite branch is what CI exercises; the PG branch is written to match but is validated only when a PG instance exists. Every helper MUST still have the PG branch (spec constraint), even though CI can't run it.
- **Grace period is load-bearing for tests:** any test that used the shared key before this plan must still pass — the shared key path is intact. If a regression suite goes red, the fallback wiring is wrong, not the test.
- **Never log/return raw keys** beyond the single provisioning response — the reviewer should grep the diff for any `logger.*api_key` / returning the raw key from a GET.
