# Webcam Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable any Windows PC to stream USB webcam footage to the SDPRS Dashboard via a single exe, using 1Hz JPEG snapshots (existing pipeline) + on-demand H.264 HLS streaming.

**Architecture:** Client pushes JPEG/HLS to server via outbound HTTPS (no inbound ports). Server stores latest frame in memory (reuses existing snapshot pipeline) and HLS segments on disk. Dashboard displays webcam tiles with badge and on-demand live view via hls.js. Control channel uses HTTP long-poll for stream start/stop commands.

**Tech Stack:** Python 3.11+ / OpenCV / FFmpeg / httpx / tkinter / pystray / PyInstaller (client); FastAPI / SQLite / APScheduler (server); React 17 / hls.js / Babel-standalone (dashboard)

> **Revision 2026-07-21 (after Tasks 1-2 shipped).** A pre-flight scan of the remaining
> tasks against the actual codebase found seven defects in this plan's own text. All are
> corrected in place; each correction is annotated inline where it applies, so a reader who
> knows the original will see what changed and why. Summary:
>
> | # | Where | Defect |
> |---|-------|--------|
> | 1 | Task 3 | Broadcast two new WS types but updated neither the SPA whitelist nor the frozen contract test — `test_ws_event_contract.py` would go red and the SPA would drop both frames. New Step 6 added |
> | 2 | Tasks 5 + 12 | Task 5 keyed the tile off `node.type === 'webcam'`, which nothing populated until Task 12, seven tasks later. **Merged into Task 5**; Task 12 retired |
> | 3 | Task 12 | Told the implementer to make required `NodeStatus.node_type` optional, and called `NodeStatus(...)` with two kwargs that are not fields on the model |
> | 4 | Task 3 | `get_hls_file` gated traversal with `str.startswith()` — a sibling dir `storage/hls-evil` satisfies it. Now `Path.is_relative_to()` + an extension allowlist |
> | 5 | Task 3 | `cleanup_hls_dir` never dropped `_command_queues`, which `get_command_queue` creates for any caller-supplied `node_id` |
> | 6 | Task 3 | Re-derived the API-key hash inline via `__import__("hashlib")` instead of using the identity `verify_webcam_api_key` had already authenticated |
> | 7 | Task 5 | The `app.jsx` WS handler tested `data.type` (always `undefined` — the callback is `onEvent(type, data)`), called the un-debounced refresh, and ignored `webcam_stream_started` entirely |
>
> Tasks 1 and 2 are already committed and were not revisited. Task numbering for Tasks 6-11
> and 13 is unchanged.

## Global Constraints

- All vendor JS must be local (zero external CDN requests — disaster resilience requirement)
- Client makes ONLY outbound HTTPS connections (NAT/firewall friendly)
- One API Key per Webcam Client (covers all cameras on that PC)
- `node_id` assigned by server on camera registration (format: `webcam_XX`)
- HLS segments: 2s duration, keep latest 5, cleanup 60s after stream stop
- Viewer auto-stop: 5 minutes with no viewer → force stop
- Motion-adaptive: <1% pixel change → 1fps, <5% → 3fps, else → target_fps (default 8)
- Default capture: 640×480, JPEG quality 40
- Existing edge node behavior must not change
- Database: must support both SQLite and PostgreSQL backends
- Timestamps: use `utcnow().isoformat()` from `timeutil.py` — naive UTC. Never
  `datetime.utcnow()`, never timezone-aware datetimes; mixing them raises at comparison
- All new POST/PUT/PATCH/DELETE under `/api/*` auto-covered by CSRFOriginMiddleware

**SPA invariants** (this dashboard has NO build step — added 2026-07-21 after the UI audit):

- Every `.jsx` is compiled in-browser by vendored Babel via `<script type="text/babel">`.
  There is no bundler, no import/export, and no compile-time error surface
- **Each file runs in its OWN top-level scope.** A `const` in `components.jsx` is invisible
  to `monitor.jsx`. Cross-file symbols resolve ONLY through explicit `window.*` publication
- `cd sdprs/tools/spa && npm run check` must pass before any SPA task is committed. It is
  the only offline signal that the page still compiles and renders; a red gate is a hard stop
- Adding a WebSocket event is a **three-way change**: the server `broadcast(...)` call, the
  `_WS_EVENT_TYPES` whitelist in `api.jsx`, AND a matching branch in `app.jsx`'s `onEvent`
  allowlist (which has no default case). Miss the whitelist and the frame is dropped
  silently; miss the `onEvent` branch and it arrives but does nothing. Additionally,
  `central_server/tests/test_ws_event_contract.py` sweeps every non-test `.py` for broadcast
  `type` literals and fails on any type missing from its frozen set

**Security constraints** (non-negotiable — this system commands real water pumps):

- No hardcoded credentials of any kind. No WiFi or MQTT passwords in committed source
- The literals `Msc@2333` and `MSC-Person` must never appear in any file
- No `broker.emqx.io` (or any public broker) on a production code path
- New payload/heartbeat fields are **telemetry-only**. Do not add a downlink or command
  surface to the edge nodes beyond the existing `cmd/*` topic set. The webcam control
  channel is a separate HTTP long-poll to webcam *clients* and does not touch edge MQTT

**Test invocation:** run pytest ONE suite file per invocation
(`/c/Python314/python -m pytest <file> -q -p no:cacheprovider`). A bare `pytest` from the
repo root fails because the `[Cloud]` bracket in the absolute path is parsed as test-id
parametrization. There is no `python3` alias on this machine.

---

## File Structure

```
sdprs/
├── webcam_client/                      ← NEW directory
│   ├── __init__.py
│   ├── main.py                         ← Entry: GUI + thread orchestration
│   ├── config.py                       ← Config load/save (%APPDATA%/SDPRSWebcam/config.json)
│   ├── camera_manager.py              ← Camera scan, capture, motion detection
│   ├── push_engine.py                 ← 1Hz JPEG push + HLS mode switching
│   ├── hls_encoder.py                 ← FFmpeg subprocess management
│   ├── control_channel.py            ← HTTP long-poll command receiver
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── setup_wizard.py           ← First-run setup window
│   │   └── tray_app.py              ← System tray + menu
│   ├── requirements.txt
│   └── build.spec                    ← PyInstaller spec
├── central_server/
│   ├── api/
│   │   ├── webcam.py                 ← NEW router: HLS, stream control, commands, camera registration
│   │   └── nodes.py                  ← MODIFY: add webcam client creation + revoke-key
│   ├── services/
│   │   └── hls_service.py           ← NEW: HLS storage, viewer count, cleanup scheduler
│   ├── auth.py                       ← MODIFY: add verify_webcam_api_key (DB-backed)
│   ├── database.py                   ← MODIFY: add api_key_hash column + webcam helpers
│   ├── config.py                     ← MODIFY: add HLS_STORAGE_PATH setting
│   ├── main.py                       ← MODIFY: register webcam router, init HLS state
│   └── static/spa/
│       ├── vendor/hls.min.js         ← NEW: hls.js vendored
│       ├── components.jsx            ← MODIFY: add HlsPlayer component
│       ├── pages/monitor.jsx         ← MODIFY: webcam badge + live button on tiles
│       ├── pages/status.jsx          ← MODIFY: node management (add webcam, revoke key)
│       ├── api.jsx                   ← MODIFY: add webcam API functions
│       └── index.html                ← MODIFY: add hls.min.js script tag
└── storage/hls/                      ← Runtime (created by server)
```

---

### Task 1: Server — Database Schema + Webcam Auth

**Files:**
- Modify: `sdprs/central_server/database.py`
- Modify: `sdprs/central_server/auth.py`
- Test: `sdprs/central_server/tests/test_webcam_auth.py`

**Interfaces:**
- Produces: `create_webcam_client(name) -> dict` (returns `{node_id, api_key, api_key_hash}`)
- Produces: `get_webcam_client_by_key(api_key) -> Optional[dict]`
- Produces: `revoke_webcam_key(node_id) -> dict` (returns new `{api_key, api_key_hash}`)
- Produces: `verify_webcam_api_key(api_key) -> str` (FastAPI dependency, returns node_id)
- Produces: `register_webcam_cameras(client_node_id, cameras: list) -> list[dict]`
- Produces: `get_webcam_cameras(client_node_id) -> list[dict]`

- [ ] **Step 1: Write failing test for webcam client creation**

```python
# sdprs/central_server/tests/test_webcam_auth.py
import pytest
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from central_server.database import init_db, close_db, create_webcam_client, get_webcam_client_by_key, revoke_webcam_key


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    init_db(db_path)
    yield
    close_db()


def test_create_webcam_client():
    result = create_webcam_client("櫃台電腦")
    assert result["node_id"].startswith("webcam_")
    assert result["api_key"].startswith("sk-webcam-")
    assert len(result["api_key"]) > 30
    expected_hash = hashlib.sha256(result["api_key"].encode()).hexdigest()
    assert result["api_key_hash"] == expected_hash


def test_get_webcam_client_by_key():
    created = create_webcam_client("Test PC")
    found = get_webcam_client_by_key(created["api_key"])
    assert found is not None
    assert found["node_id"] == created["node_id"]
    assert found["name"] == "Test PC"


def test_get_webcam_client_by_key_invalid():
    assert get_webcam_client_by_key("sk-webcam-nonexistent") is None


def test_revoke_webcam_key():
    created = create_webcam_client("Revoke Test")
    new_key = revoke_webcam_key(created["node_id"])
    assert new_key["api_key"] != created["api_key"]
    assert get_webcam_client_by_key(created["api_key"]) is None
    assert get_webcam_client_by_key(new_key["api_key"]) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs && python -m pytest central_server/tests/test_webcam_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_webcam_client'`

- [ ] **Step 3: Add `api_key_hash` column and webcam tables to database.py**

In `sdprs/central_server/database.py`, add to `_create_tables_sqlite(cursor)` after the existing nodes table creation:

```python
# Webcam client registry (one key per client PC, multiple cameras)
cursor.execute("""
    CREATE TABLE IF NOT EXISTS webcam_clients (
        node_id      TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        api_key_hash TEXT NOT NULL,
        created_at   DATETIME,
        status       TEXT DEFAULT 'OFFLINE'
    );
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS webcam_cameras (
        node_id       TEXT PRIMARY KEY,
        client_id     TEXT NOT NULL REFERENCES webcam_clients(node_id),
        name          TEXT NOT NULL,
        device_index  INTEGER,
        resolution_w  INTEGER DEFAULT 640,
        resolution_h  INTEGER DEFAULT 480,
        jpeg_quality  INTEGER DEFAULT 40,
        target_fps    INTEGER DEFAULT 8,
        status        TEXT DEFAULT 'OFFLINE',
        last_upload   DATETIME,
        FOREIGN KEY (client_id) REFERENCES webcam_clients(node_id) ON DELETE CASCADE
    );
""")
```

Add the same tables in `_create_tables_postgresql(conn)` using PG syntax.

- [ ] **Step 4: Implement webcam DB helper functions in database.py**

Add at the end of `database.py` (before `__all__`):

```python
import secrets

def create_webcam_client(name: str) -> dict:
    node_id = f"webcam_{secrets.token_hex(4)}"
    api_key = f"sk-webcam-{secrets.token_urlsafe(32)}"
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    now = utcnow().isoformat()
    if get_backend() == "postgresql":
        _pg_execute_sync(
            "INSERT INTO webcam_clients (node_id, name, api_key_hash, created_at, status) "
            "VALUES (:node_id, :name, :hash, :now, 'OFFLINE')",
            {"node_id": node_id, "name": name, "hash": api_key_hash, "now": now},
        )
    else:
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO webcam_clients (node_id, name, api_key_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, 'OFFLINE')",
                (node_id, name, api_key_hash, now),
            )
    return {"node_id": node_id, "api_key": api_key, "api_key_hash": api_key_hash}


def get_webcam_client_by_key(api_key: str) -> Optional[dict]:
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT node_id, name, status FROM webcam_clients WHERE api_key_hash = :h",
            {"h": api_key_hash},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT node_id, name, status FROM webcam_clients WHERE api_key_hash = ?",
            (api_key_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def revoke_webcam_key(node_id: str) -> dict:
    new_key = f"sk-webcam-{secrets.token_urlsafe(32)}"
    new_hash = hashlib.sha256(new_key.encode()).hexdigest()
    if get_backend() == "postgresql":
        _pg_execute_sync(
            "UPDATE webcam_clients SET api_key_hash = :h WHERE node_id = :id",
            {"h": new_hash, "id": node_id},
        )
    else:
        with get_db_cursor() as cursor:
            cursor.execute(
                "UPDATE webcam_clients SET api_key_hash = ? WHERE node_id = ?",
                (new_hash, node_id),
            )
    return {"api_key": new_key, "api_key_hash": new_hash}


def register_webcam_cameras(client_node_id: str, cameras: list) -> list:
    results = []
    for cam in cameras:
        cam_node_id = f"webcam_{secrets.token_hex(4)}"
        if get_backend() == "postgresql":
            _pg_execute_sync(
                "INSERT INTO webcam_cameras (node_id, client_id, name, device_index, "
                "resolution_w, resolution_h, jpeg_quality, target_fps) "
                "VALUES (:nid, :cid, :name, :didx, :rw, :rh, :q, :fps)",
                {"nid": cam_node_id, "cid": client_node_id, "name": cam["name"],
                 "didx": cam.get("device_index", 0), "rw": cam.get("resolution", [640,480])[0],
                 "rh": cam.get("resolution", [640,480])[1], "q": cam.get("jpeg_quality", 40),
                 "fps": cam.get("target_fps", 8)},
            )
        else:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO webcam_cameras (node_id, client_id, name, device_index, "
                    "resolution_w, resolution_h, jpeg_quality, target_fps) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cam_node_id, client_node_id, cam["name"], cam.get("device_index", 0),
                     cam.get("resolution", [640,480])[0], cam.get("resolution", [640,480])[1],
                     cam.get("jpeg_quality", 40), cam.get("target_fps", 8)),
                )
        results.append({"node_id": cam_node_id, "name": cam["name"]})
    return results


def get_webcam_cameras(client_node_id: str) -> list:
    if get_backend() == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT node_id, name, device_index, status FROM webcam_cameras WHERE client_id = :cid",
            {"cid": client_node_id},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT node_id, name, device_index, status FROM webcam_cameras WHERE client_id = ?",
            (client_node_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_webcam_camera_owner(cam_node_id: str, api_key_hash: str) -> Optional[dict]:
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT wc.node_id, wc.client_id FROM webcam_cameras wc "
            "JOIN webcam_clients wcl ON wc.client_id = wcl.node_id "
            "WHERE wc.node_id = :nid AND wcl.api_key_hash = :h",
            {"nid": cam_node_id, "h": api_key_hash},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT wc.node_id, wc.client_id FROM webcam_cameras wc "
            "JOIN webcam_clients wcl ON wc.client_id = wcl.node_id "
            "WHERE wc.node_id = ? AND wcl.api_key_hash = ?",
            (cam_node_id, api_key_hash),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
```

Add `import hashlib` and `import secrets` at the top of database.py if not already present.

- [ ] **Step 5: Add `verify_webcam_api_key` dependency to auth.py**

In `sdprs/central_server/auth.py`, add:

```python
async def verify_webcam_api_key(request: Request) -> str:
    """Verify X-API-Key against webcam_clients table. Returns client node_id."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    from .database import get_webcam_client_by_key
    client = get_webcam_client_by_key(api_key)
    if client is None:
        raise HTTPException(status_code=401, detail="Invalid webcam API key")
    return client["node_id"]
```

Add `"verify_webcam_api_key"` to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd sdprs && python -m pytest central_server/tests/test_webcam_auth.py -v`
Expected: All 4 tests PASS

- [ ] **Step 7: Commit**

```bash
cd sdprs
git add central_server/database.py central_server/auth.py central_server/tests/test_webcam_auth.py
git commit -m "feat(server): add webcam client DB schema and key-based auth"
```

---

### Task 2: Server — Node Management Endpoints (Create Webcam Client + Revoke Key)

**Files:**
- Modify: `sdprs/central_server/api/nodes.py`
- Test: `sdprs/central_server/tests/test_webcam_nodes_api.py`

**Interfaces:**
- Consumes: `create_webcam_client(name)`, `revoke_webcam_key(node_id)` from Task 1
- Produces: `POST /api/nodes/webcam` → `{node_id, api_key, name}` (201)
- Produces: `POST /api/nodes/{node_id}/revoke-key` → `{api_key}` (200)

- [ ] **Step 1: Write failing test**

```python
# sdprs/central_server/tests/test_webcam_nodes_api.py
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    monkeypatch.setenv("EDGE_API_KEY", "test-edge-key-12345678901234567890")
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Login to get session
        await c.post("/login", data={"username": "admin", "password": "testpass123"})
        yield c


@pytest.mark.anyio
async def test_create_webcam_client(client):
    resp = await client.post("/api/nodes/webcam", json={"name": "櫃台電腦"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["node_id"].startswith("webcam_")
    assert data["api_key"].startswith("sk-webcam-")
    assert data["name"] == "櫃台電腦"


@pytest.mark.anyio
async def test_revoke_webcam_key(client):
    resp = await client.post("/api/nodes/webcam", json={"name": "Revoke Test"})
    node_id = resp.json()["node_id"]
    old_key = resp.json()["api_key"]

    resp2 = await client.post(f"/api/nodes/{node_id}/revoke-key")
    assert resp2.status_code == 200
    new_key = resp2.json()["api_key"]
    assert new_key != old_key
    assert new_key.startswith("sk-webcam-")


@pytest.mark.anyio
async def test_create_webcam_requires_auth():
    from central_server.main import app as fastapi_app
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/nodes/webcam", json={"name": "No Auth"})
        assert resp.status_code in (401, 302)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs && python -m pytest central_server/tests/test_webcam_nodes_api.py -v`
Expected: FAIL — 404 (route not found)

- [ ] **Step 3: Add webcam endpoints to nodes.py**

In `sdprs/central_server/api/nodes.py`, add these imports at top:

```python
from ..database import create_webcam_client, revoke_webcam_key as db_revoke_key
from ..services.audit_service import log_action
```

Add a new action constant in `services/audit_service.py`:
```python
ACTION_WEBCAM_CREATE = "webcam_create"
ACTION_WEBCAM_REVOKE_KEY = "webcam_revoke_key"
```

Add endpoints at the end of nodes.py (before `__all__`):

```python
class WebcamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


@router.post("/nodes/webcam", status_code=201)
async def create_webcam_node(
    body: WebcamCreateRequest,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    result = create_webcam_client(body.name)
    log_action(user, "webcam_create", target_id=result["node_id"],
               details={"name": body.name})
    return {"node_id": result["node_id"], "api_key": result["api_key"], "name": body.name}


@router.post("/nodes/{node_id}/revoke-key")
async def revoke_node_key(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    from ..database import get_webcam_client_by_key
    # Verify node exists as webcam client
    from ..database import get_db_cursor, get_backend
    if get_backend() == "postgresql":
        from ..database import _pg_fetch_one_sync
        row = _pg_fetch_one_sync("SELECT node_id FROM webcam_clients WHERE node_id = :id", {"id": node_id})
    else:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT node_id FROM webcam_clients WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Webcam client {node_id} not found")
    result = db_revoke_key(node_id)
    log_action(user, "webcam_revoke_key", target_id=node_id, details={})
    return {"api_key": result["api_key"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sdprs && python -m pytest central_server/tests/test_webcam_nodes_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add central_server/api/nodes.py central_server/services/audit_service.py central_server/tests/test_webcam_nodes_api.py
git commit -m "feat(server): add webcam client creation and key revocation endpoints"
```

---

### Task 3: Server — Webcam Router (Camera Registration + HLS + Stream Control + Commands)

**Files:**
- Create: `sdprs/central_server/api/webcam.py`
- Create: `sdprs/central_server/services/hls_service.py`
- Modify: `sdprs/central_server/main.py` (register router + init state)
- Modify: `sdprs/central_server/config.py` (add HLS_STORAGE_PATH)
- Modify: `sdprs/central_server/static/spa/api.jsx` (WS event whitelist — see Step 6)
- Modify: `sdprs/central_server/tests/test_ws_event_contract.py` (frozen contract — see Step 6)
- Test: `sdprs/central_server/tests/test_webcam_api.py`

**Interfaces:**
- Consumes: `verify_webcam_api_key` (Task 1), `get_webcam_camera_owner` (Task 1)
- Produces: `POST /api/webcam/cameras` → register cameras, returns `[{node_id, name}]`
- Produces: `PUT /api/webcam/{node_id}/hls/{filename}` → 204
- Produces: `GET /api/webcam/{node_id}/hls/{filename}` → file content
- Produces: `POST /api/webcam/{node_id}/stream/start` → 200
- Produces: `POST /api/webcam/{node_id}/stream/stop` → 200
- Produces: `GET /api/webcam/{node_id}/commands?timeout=5` → `{command, params}`

- [ ] **Step 1: Add HLS_STORAGE_PATH to config.py**

In `sdprs/central_server/config.py`, add to the `Settings` class:

```python
HLS_STORAGE_PATH: str = "./storage/hls"
HLS_MAX_SEGMENTS: int = 5
HLS_VIEWER_TIMEOUT_SECONDS: int = 300
```

- [ ] **Step 2: Create hls_service.py**

```python
# sdprs/central_server/services/hls_service.py
import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from ..config import get_settings

logger = logging.getLogger("hls_service")

_viewer_count: Dict[str, int] = {}
_command_queues: Dict[str, asyncio.Queue] = {}
_last_activity: Dict[str, float] = {}


def get_hls_dir(node_id: str) -> Path:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH)
    base.mkdir(parents=True, exist_ok=True)
    node_dir = base / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    return node_dir


def store_hls_segment(node_id: str, filename: str, data: bytes) -> None:
    node_dir = get_hls_dir(node_id)
    target = node_dir / filename
    target.write_bytes(data)
    _last_activity[node_id] = time.time()
    if filename.endswith(".ts"):
        _prune_old_segments(node_dir)


def _prune_old_segments(node_dir: Path) -> None:
    settings = get_settings()
    max_seg = settings.HLS_MAX_SEGMENTS
    ts_files = sorted(node_dir.glob("seg_*.ts"), key=lambda f: f.name)
    while len(ts_files) > max_seg:
        oldest = ts_files.pop(0)
        oldest.unlink(missing_ok=True)


# Only these two extensions are ever served. HLS needs nothing else, and an
# allowlist means a traversal bug alone is not enough to read arbitrary files.
_SERVABLE_SUFFIXES = (".ts", ".m3u8")


def get_hls_file(node_id: str, filename: str) -> Optional[bytes]:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH).resolve()
    if not filename.endswith(_SERVABLE_SUFFIXES):
        return None
    target = (base / node_id / filename).resolve()
    # is_relative_to(), NOT str.startswith(). A string prefix test passes for
    # a sibling directory whose name merely starts with the base name --
    # base "storage/hls" would happily match "storage/hls-evil/secret.ts".
    if not target.is_relative_to(base):
        return None
    if target.is_file():
        return target.read_bytes()
    return None


def cleanup_hls_dir(node_id: str) -> None:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH)
    node_dir = base / node_id
    if node_dir.exists():
        shutil.rmtree(node_dir, ignore_errors=True)
    _viewer_count.pop(node_id, None)
    _last_activity.pop(node_id, None)
    # _command_queues must be dropped here too. get_command_queue() creates an
    # entry for ANY node_id that reaches the long-poll endpoint, so leaving it
    # behind lets caller-supplied ids accumulate without bound.
    _command_queues.pop(node_id, None)


def get_viewer_count(node_id: str) -> int:
    return _viewer_count.get(node_id, 0)


def increment_viewer(node_id: str) -> int:
    _viewer_count[node_id] = _viewer_count.get(node_id, 0) + 1
    return _viewer_count[node_id]


def decrement_viewer(node_id: str) -> int:
    count = max(0, _viewer_count.get(node_id, 0) - 1)
    _viewer_count[node_id] = count
    return count


def get_command_queue(node_id: str) -> asyncio.Queue:
    if node_id not in _command_queues:
        _command_queues[node_id] = asyncio.Queue()
    return _command_queues[node_id]


async def enqueue_command(node_id: str, command: str, params: Optional[dict] = None) -> None:
    q = get_command_queue(node_id)
    await q.put({"command": command, "params": params})


async def dequeue_command(node_id: str, timeout: float = 5.0) -> Optional[dict]:
    q = get_command_queue(node_id)
    try:
        return await asyncio.wait_for(q.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def cleanup_stale_streams() -> None:
    settings = get_settings()
    timeout = settings.HLS_VIEWER_TIMEOUT_SECONDS
    now = time.time()
    stale = [nid for nid, ts in _last_activity.items()
             if now - ts > 60 and _viewer_count.get(nid, 0) == 0]
    for nid in stale:
        logger.info(f"Cleaning stale HLS dir for {nid}")
        cleanup_hls_dir(nid)


__all__ = [
    "get_hls_dir", "store_hls_segment", "get_hls_file", "cleanup_hls_dir",
    "get_viewer_count", "increment_viewer", "decrement_viewer",
    "get_command_queue", "enqueue_command", "dequeue_command",
    "cleanup_stale_streams",
]
```

- [ ] **Step 3: Create webcam.py router**

```python
# sdprs/central_server/api/webcam.py
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import get_current_user, verify_webcam_api_key, verify_node_id
from ..database import register_webcam_cameras, get_webcam_cameras
from ..services import hls_service
from ..services.websocket_service import ws_manager

logger = logging.getLogger("webcam_api")
router = APIRouter(prefix="/webcam", tags=["webcam"])


class CameraRegistration(BaseModel):
    cameras: List[Dict[str, Any]] = Field(..., min_length=1, max_length=10)


@router.post("/cameras", status_code=201)
async def register_cameras(
    body: CameraRegistration,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
) -> List[Dict[str, Any]]:
    results = register_webcam_cameras(client_node_id, body.cameras)
    return results


@router.put("/{node_id}/hls/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_hls_segment(
    node_id: str,
    filename: str,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
):
    verify_node_id(node_id)
    if not filename.endswith((".ts", ".m3u8")):
        raise HTTPException(status_code=400, detail="Only .ts and .m3u8 files allowed")
    # Ownership is checked against the identity the dependency already
    # authenticated. Re-hashing the raw X-API-Key header here would duplicate
    # auth logic that verify_webcam_api_key has done, and re-reading a header
    # the dependency owns is how the two drift apart later.
    if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
        raise HTTPException(status_code=403, detail="Camera not owned by this client")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty segment data")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Segment too large (max 5 MB)")
    hls_service.store_hls_segment(node_id, filename, data)
    return None


@router.get("/{node_id}/hls/{filename}")
async def serve_hls_file(
    node_id: str,
    filename: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Response:
    verify_node_id(node_id)
    data = hls_service.get_hls_file(node_id, filename)
    if data is None:
        raise HTTPException(status_code=404, detail="HLS file not found")
    media_type = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    # No Access-Control-Allow-Origin here. This endpoint is session-authenticated
    # and the SPA fetches it same-origin, so a wildcard would only widen reach
    # without enabling anything the dashboard needs.
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "no-cache, no-store"},
    )


@router.post("/{node_id}/stream/start")
async def start_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    count = hls_service.increment_viewer(node_id)
    if count == 1:
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
    await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"message": "Stream start requested", "node_id": node_id, "viewers": count}


@router.post("/{node_id}/stream/stop")
async def stop_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    count = hls_service.decrement_viewer(node_id)
    if count == 0:
        await hls_service.enqueue_command(node_id, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": node_id}})
    return {"message": "Stream stop requested", "node_id": node_id, "viewers": count}


@router.get("/{node_id}/commands")
async def poll_commands(
    node_id: str,
    request: Request,
    timeout: float = 5.0,
    client_node_id: str = Depends(verify_webcam_api_key),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    timeout = min(max(timeout, 1.0), 30.0)
    cmd = await hls_service.dequeue_command(node_id, timeout=timeout)
    if cmd is None:
        return {"command": None}
    return cmd


__all__ = ["router"]
```

- [ ] **Step 4: Register webcam router in main.py**

In `sdprs/central_server/main.py`, add import:
```python
from .api import webcam as webcam_api
```

Add router registration after the existing routers:
```python
app.include_router(webcam_api.router, prefix="/api")
```

- [ ] **Step 5: Add HLS cleanup to APScheduler in main.py lifespan**

In the lifespan startup section (where the scheduler is configured), add:

```python
from .services.hls_service import cleanup_stale_streams
scheduler.add_job(cleanup_stale_streams, "interval", minutes=5, id="hls_cleanup")
```

- [ ] **Step 6: Update the WS event contract — BOTH sides (hard gate)**

Step 3 adds two `ws_manager.broadcast(...)` call-sites emitting `webcam_stream_started`
and `webcam_stream_stopped`. `central_server/tests/test_ws_event_contract.py` sweeps every
non-test `.py` under `central_server/` for broadcast payload `type` literals and fails on
any type absent from its frozen set. It will go RED the moment Step 3 lands unless both
sides are updated in the same commit. Independently, `api.jsx` silently drops unrecognised
frames ("unknown type — ignore silently for forward-compat"), so skipping the SPA half
produces no error — just a live view that never reacts to start/stop.

**6a.** In `sdprs/central_server/static/spa/api.jsx`, extend the `_WS_EVENT_TYPES` set:

```javascript
  const _WS_EVENT_TYPES = new Set([
    'alert_updated', 'alert_acknowledged', 'alert_resolved',
    'node_status', 'pump_status', 'node_deleted',
    'auth_expired',
    'webcam_stream_started', 'webcam_stream_stopped',
  ]);
```

**6b.** In `sdprs/central_server/tests/test_ws_event_contract.py`, add both to
`EXPECTED_ALL_TYPES`, preserving the file's alphabetical ordering:

```python
EXPECTED_ALL_TYPES = frozenset({
    "alert_acknowledged",
    "alert_resolved",
    "alert_updated",
    "auth_expired",
    "new_alert",
    "node_deleted",
    "node_status",
    "ping",
    "pump_status",
    "webcam_stream_started",
    "webcam_stream_stopped",
})
```

Do NOT add them to `INTERNAL_ONLY_TYPES` — they are genuinely intended for the SPA.

**6c.** Verify the gate is green before continuing:

```bash
cd sdprs && /c/Python314/python -m pytest central_server/tests/test_ws_event_contract.py -q -p no:cacheprovider
```

- [ ] **Step 7: Write integration test**

```python
# sdprs/central_server/tests/test_webcam_api.py
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    monkeypatch.setenv("EDGE_API_KEY", "test-edge-key-12345678901234567890")
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def authed_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": "admin", "password": "testpass123"})
        yield c


@pytest.mark.anyio
async def test_full_webcam_flow(authed_client, tmp_path):
    # 1. Create webcam client
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Test PC"})
    assert resp.status_code == 201
    api_key = resp.json()["api_key"]

    # 2. Register cameras
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    assert resp.status_code == 201
    cam_node_id = resp.json()[0]["node_id"]

    # 3. Upload HLS segment
    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/seg_000001.ts",
        content=b"\x00" * 100,
        headers=headers)
    assert resp.status_code == 204

    # 4. Upload playlist
    playlist = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg_000001.ts\n"
    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/playlist.m3u8",
        content=playlist.encode(),
        headers=headers)
    assert resp.status_code == 204

    # 5. Serve HLS file (dashboard auth)
    resp = await authed_client.get(f"/api/webcam/{cam_node_id}/hls/playlist.m3u8")
    assert resp.status_code == 200
    assert b"seg_000001.ts" in resp.content

    # 6. Stream start
    resp = await authed_client.post(f"/api/webcam/{cam_node_id}/stream/start")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 1

    # 7. Poll commands (should get stream_start)
    resp = await authed_client.get(
        f"/api/webcam/{cam_node_id}/commands?timeout=1",
        headers=headers)
    assert resp.status_code == 200
    assert resp.json()["command"] == "stream_start"

    # 8. Stream stop
    resp = await authed_client.post(f"/api/webcam/{cam_node_id}/stream/stop")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 0
```

- [ ] **Step 8: Run tests**

Run each suite as its own pytest invocation — a bare `pytest` from the repo root fails
because the `[Cloud]` bracket in the absolute path is parsed as test-id parametrization:

```bash
cd sdprs
/c/Python314/python -m pytest central_server/tests/test_webcam_api.py -v -p no:cacheprovider
/c/Python314/python -m pytest central_server/tests/test_ws_event_contract.py -q -p no:cacheprovider
```

Expected: both PASS.

- [ ] **Step 9: Commit**

```bash
cd sdprs
git add central_server/api/webcam.py central_server/services/hls_service.py central_server/main.py central_server/config.py central_server/tests/test_webcam_api.py central_server/static/spa/api.jsx central_server/tests/test_ws_event_contract.py
git commit -m "feat(server): add webcam router with HLS, stream control, and command long-poll"
```

---

### Task 3b: Server — Webcam JPEG Ingest + Viewer Lease Model

> **New task (2026-07-21 audit).** Closes C1 (1Hz path 401s silently), C2
> (`last_upload` has no writer), C3 (dual `nodes`/`webcam_cameras` registry),
> H1 (viewer count never decrements), H2 (5-min auto-stop absent / never
> commands the client), and H3 (`verify_node_id` allowlist breaks every webcam
> endpoint). Derives from spec §303 (1Hz JPEG ingest) and §391 (lease model).
> All files are under `central_server/` — file-disjoint from the client track.

**Files:**
- Modify: `sdprs/central_server/database.py` (add `touch_webcam_upload`)
- Modify: `sdprs/central_server/services/hls_service.py` (lease model + async cleanup)
- Modify: `sdprs/central_server/api/webcam.py` (ingest endpoint, `stream/renew`,
  rewrite `stream/start`+`stream/stop` to lease semantics, remove `verify_node_id`)
- Modify: `sdprs/central_server/main.py` (cleanup job → async, 30s interval)
- Modify: `sdprs/central_server/tests/test_webcam_api.py` (update stream tests to lease model)
- Test: `sdprs/central_server/tests/test_webcam_ingest.py` (new)

**Interfaces:**
- Produces: `POST /api/webcam/{node_id}/snapshot` (X-API-Key) → 204
- Produces: `POST /api/webcam/{node_id}/stream/renew` (session) → 200
- Modifies: `stream/start`, `stream/stop` → lease semantics (no longer a raw counter)
- Produces (DB): `touch_webcam_upload(node_id)`
- Produces (service): `touch_lease`, `release_lease`, `has_active_lease`,
  `get_viewer_count` (now 0/1), async `cleanup_stale_streams`

**Frozen contract (shared with client Task 8 — do not diverge):** the 1Hz path is
`POST /api/webcam/{node_id}/snapshot`, `X-API-Key` header, raw JPEG body, `204` on
success. NOT the edge route. The client calls `raise_for_status()` (Task 8).

- [ ] **Step 1: `database.py` — add the `last_upload` writer (fixes C2/C3)**

The `webcam_cameras.last_upload` column exists but nothing writes it. Add a writer
that stamps ONLY `webcam_cameras` — it must NOT touch `nodes`. (Reusing the edge
`touch_node_upload` is what causes C3: that function `INSERT`s a `node_type='glass'`
row into `nodes`, giving each webcam a second identity.)

```python
def touch_webcam_upload(node_id: str) -> None:
    """Stamp webcam_cameras.last_upload = now for a camera node. Writes ONLY
    webcam_cameras (never nodes -- see audit C3). Never raises (data-quality
    column, not load-bearing for ingest)."""
    now = utcnow().isoformat()
    try:
        if get_backend() == "postgresql":
            _pg_execute_sync(
                "UPDATE webcam_cameras SET last_upload = :ts WHERE node_id = :nid",
                {"ts": now, "nid": node_id},
            )
            return
        with get_db_cursor() as cursor:
            cursor.execute(
                "UPDATE webcam_cameras SET last_upload = ? WHERE node_id = ?",
                (now, node_id),
            )
    except Exception as e:
        logger.debug(f"touch_webcam_upload failed for {node_id}: {e}")
```

Match the exact backend-dispatch idiom already used by `register_webcam_cameras`
(`get_backend()`, `_pg_execute_sync`, `get_db_cursor`). Export it if the module
uses an `__all__`.

- [ ] **Step 2: `hls_service.py` — replace the counter with a lease model (fixes H1/H2)**

Delete `_viewer_count`, `increment_viewer`, `decrement_viewer`. Add:

```python
_stream_leases: Dict[str, float] = {}       # node_id -> lease expiry (epoch seconds)
_stream_stopped_at: Dict[str, float] = {}   # node_id -> when we last forced/observed a stop

LEASE_TTL_SECONDS = 90   # spec §391: survives two missed 30s renews (one network blip)


def touch_lease(node_id: str) -> bool:
    """Arm or extend the viewer lease (start/renew). Returns True on a 0->1
    transition (no live lease before) so the caller enqueues stream_start +
    broadcasts webcam_stream_started."""
    now = time.time()
    was_live = _stream_leases.get(node_id, 0.0) > now
    _stream_leases[node_id] = now + LEASE_TTL_SECONDS
    _stream_stopped_at.pop(node_id, None)
    return not was_live


def release_lease(node_id: str) -> bool:
    """Explicit stop. Returns True if a live lease existed (caller enqueues
    stream_stop + broadcasts webcam_stream_stopped)."""
    now = time.time()
    was_live = _stream_leases.pop(node_id, 0.0) > now
    if was_live:
        _stream_stopped_at[node_id] = now
    return was_live


def has_active_lease(node_id: str) -> bool:
    return _stream_leases.get(node_id, 0.0) > time.time()


def get_viewer_count(node_id: str) -> int:
    # Single-lease-per-node model: 1 while a viewer lease is live, else 0.
    return 1 if has_active_lease(node_id) else 0
```

> **Design note — single lease per node.** The dashboard sends no per-viewer token,
> so start/renew/stop are per-node. We model ONE lease per camera: "is anyone
> watching?" (0/1). Two operators viewing the same camera share the lease; either
> one's renew keeps it alive; an explicit ✕ from one releases it and the other's
> next 30s renew re-arms it (brief re-start). This is deliberately simple and
> correct for the realistic case (usually one operator per camera); the failure
> mode it MUST kill — a forgotten tab pinning a field PC's uplink forever — is
> fixed because the lease expires without renews. Record this tradeoff; do not
> silently "improve" it into per-viewer refcounting without updating the spec.

Rewrite `cleanup_hls_dir` to drop the new dicts instead of `_viewer_count`:

```python
def cleanup_hls_dir(node_id: str) -> None:
    ...  # unchanged dir removal
    _stream_leases.pop(node_id, None)
    _stream_stopped_at.pop(node_id, None)
    _last_activity.pop(node_id, None)
    _command_queues.pop(node_id, None)
    _command_queue_activity.pop(node_id, None)
```

- [ ] **Step 3: `hls_service.py` — make `cleanup_stale_streams` async and command the client (fixes H2)**

`main.py` uses `AsyncIOScheduler`, which runs an `async def` job ON the event loop.
Making the job async is what lets it `await enqueue_command` / `ws_manager.broadcast`
and mutate the module dicts with NO lock — everything is single-threaded on the loop
(this also retires the "dict mutation from a scheduler thread" race noted in the
Task 3 roll-up). The critical fix over the shipped version: **on lease expiry it
must enqueue `stream_stop` to the client and broadcast — not merely delete dirs.**

```python
async def cleanup_stale_streams() -> None:
    """Runs every 30s ON the event loop (AsyncIOScheduler async job). Two duties
    (spec §391 + §377):
      1. Lease expiry -> a lease past expiry means every viewer left WITHOUT a
         clean stop (closed tab / crash / lid). Force the stream down for real:
         enqueue stream_stop to the CLIENT and broadcast webcam_stream_stopped,
         then mark it for directory reclaim.
      2. Directory reclaim -> 60s after a stop, or an orphan dir/queue idle past
         HLS_VIEWER_TIMEOUT_SECONDS, drop the HLS dir + in-memory queue state.
    """
    from .websocket_service import ws_manager  # local import avoids an import cycle
    settings = get_settings()
    now = time.time()

    # (1) Expire leases -> force a real stop.
    for nid in [n for n, exp in list(_stream_leases.items()) if exp <= now]:
        _stream_leases.pop(nid, None)
        _stream_stopped_at[nid] = now
        logger.info(f"Viewer lease expired for {nid}; forcing stream stop")
        await enqueue_command(nid, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": nid}})

    # (2) Reclaim directories / queue state (no active lease only).
    DIR_GRACE = 60
    orphan_grace = settings.HLS_VIEWER_TIMEOUT_SECONDS
    candidates = (set(_stream_stopped_at) | set(_last_activity)
                  | set(_command_queue_activity) | set(_command_queues))
    for nid in candidates:
        if has_active_lease(nid):
            continue
        stopped_at = _stream_stopped_at.get(nid, 0.0)
        last_act = max(_last_activity.get(nid, 0.0), _command_queue_activity.get(nid, 0.0))
        recently_stopped = bool(stopped_at) and (now - stopped_at) > DIR_GRACE
        idle_orphan = bool(last_act) and (now - last_act) > orphan_grace
        if recently_stopped or idle_orphan:
            logger.info(f"Reclaiming HLS state for {nid}")
            cleanup_hls_dir(nid)
```

Update `__all__`: drop `increment_viewer`/`decrement_viewer`, add `touch_lease`,
`release_lease`, `has_active_lease`. The reclaim predicate in duty (2) is a
reference — finalize its exact thresholds with the tests in Step 6.

- [ ] **Step 4: `webcam.py` — add the ingest endpoint, add `stream/renew`, rewrite start/stop, remove `verify_node_id` (fixes C1/H3)**

Add the ingest route (writes the SHARED frame buffer + `last_upload`):

```python
from ..timeutil import utcnow
from ..database import register_webcam_cameras, get_webcam_cameras, touch_webcam_upload

@router.post("/{node_id}/snapshot", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_webcam_snapshot(
    node_id: str,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
):
    # NO verify_node_id() here: webcam node_ids are server-assigned at
    # registration and can never be in ALLOWED_NODE_IDS (spec §303 / audit H3).
    # Ownership is enforced against the authenticated client instead.
    if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
        raise HTTPException(status_code=403, detail="Camera not owned by this client")
    jpeg_bytes = await request.body()
    if not jpeg_bytes:
        raise HTTPException(status_code=400, detail="Empty snapshot data")
    if len(jpeg_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Snapshot too large (max 5 MB)")
    # Share the SAME in-memory buffer the edge path uses, so the existing
    # GET /api/edge/{node_id}/snapshot/latest read path serves webcams unchanged
    # (spec §303 point 3). Webcam JPEGs come from cv2.imencode and carry no EXIF,
    # so the edge path's _strip_exif is unnecessary here.
    request.app.state.latest_snapshots[node_id] = {"jpeg": jpeg_bytes, "timestamp": utcnow()}
    touch_webcam_upload(node_id)  # fixes C2; writes webcam_cameras only, never nodes (C3)
    return None
```

Rewrite `stream/start` + `stream/stop` and add `stream/renew` to lease semantics,
and **remove every `verify_node_id(node_id)` call in this file** (H3 — it 403s every
webcam endpoint on allowlisted deployments; ownership is already enforced by
`get_webcam_cameras` on the client endpoints and by session auth on the dashboard
endpoints):

```python
@router.post("/{node_id}/stream/start")
async def start_webcam_stream(node_id, request, user=Depends(get_current_user)):
    fresh = hls_service.touch_lease(node_id)
    if fresh:  # 0 -> 1
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
        await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"message": "Stream start requested", "node_id": node_id,
            "viewers": hls_service.get_viewer_count(node_id)}


@router.post("/{node_id}/stream/renew")
async def renew_webcam_stream(node_id, request, user=Depends(get_current_user)):
    # Dashboard calls this every 30s while a tile is live. If the lease had
    # lapsed (network gap > 90s) the scan already stopped the client, so a
    # re-arm (fresh == True) re-issues stream_start to resume encoding.
    fresh = hls_service.touch_lease(node_id)
    if fresh:
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
        await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"node_id": node_id, "viewers": hls_service.get_viewer_count(node_id)}


@router.post("/{node_id}/stream/stop")
async def stop_webcam_stream(node_id, request, user=Depends(get_current_user)):
    was_live = hls_service.release_lease(node_id)
    if was_live:
        await hls_service.enqueue_command(node_id, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": node_id}})
    return {"message": "Stream stop requested", "node_id": node_id,
            "viewers": hls_service.get_viewer_count(node_id)}
```

Also drop `verify_node_id(node_id)` from `upload_hls_segment`, `serve_hls_file`, and
`poll_commands`, and remove the now-unused `verify_node_id` import. No new WS event
types are introduced (`webcam_stream_started/stopped` are already whitelisted +
frozen from Task 3), so this task needs no `api.jsx`/`app.jsx`/contract-test change.

- [ ] **Step 5: `main.py` — async cleanup at 30s**

Line ~106: change the job registration to 30 seconds. `cleanup_stale_streams` is
now `async def`; `AsyncIOScheduler` will await it on the loop.

```python
scheduler.add_job(cleanup_stale_streams, "interval", seconds=30, id="hls_cleanup")
```

- [ ] **Step 6: Tests**

New `central_server/tests/test_webcam_ingest.py` (async, `@pytest.mark.anyio`,
`AsyncClient` + `ASGITransport`; call `init_db()` explicitly in the fixture —
ASGITransport does NOT run lifespan). Cover:
1. Ingest with a valid client key for an OWNED camera → 204; the frame is then
   readable via `GET /api/edge/{node_id}/snapshot/latest` (proves the shared buffer);
   `webcam_cameras.last_upload` is now non-null (proves C2 writer).
2. Ingest for a node_id NOT owned by the key → 403.
3. Ingest with NO/invalid key → 401 (verify_webcam_api_key).
4. Ingest does NOT create a `nodes` row for the camera (proves C3 stays fixed).
5. Empty body → 400; >5 MB → 413.

Lease behavior (extend `test_webcam_api.py` or a new `test_webcam_lease.py`):
6. `stream/start` → viewers 1 + a `stream_start` command is enqueued; a SECOND
   start → still 1, no duplicate broadcast (0→1 only).
7. `stream/renew` extends the lease (monkeypatch `time.time`/`LEASE_TTL` so the
   scan sees it still live).
8. **Lease expiry → `cleanup_stale_streams()` enqueues `stream_stop` AND the client
   can dequeue it** (this is the H1/H2 regression guard — the single most important
   assertion in this task). Drive it by setting the lease expiry into the past, then
   `await cleanup_stale_streams()`, then `dequeue_command(nid, timeout=0.1)` returns
   `{"command": "stream_stop", ...}`.
9. `stream/stop` releases the lease immediately (viewers 0 + stop enqueued).

Also update the existing Task 3 stream tests in `test_webcam_api.py` that asserted
counter semantics.

- [ ] **Step 7: Run tests (per-file, this machine)**
```bash
cd sdprs
/c/Python314/python -m pytest central_server/tests/test_webcam_ingest.py -q -p no:cacheprovider
/c/Python314/python -m pytest central_server/tests/test_webcam_api.py -q -p no:cacheprovider
/c/Python314/python -m pytest central_server/tests/test_ws_event_contract.py -q -p no:cacheprovider
```
Expected: all PASS. Also `py_compile` every modified `.py` first.

- [ ] **Step 8: Commit** (stage explicit pathspecs only — a client-track agent may
  be committing in parallel)
```bash
cd sdprs
git add central_server/database.py central_server/services/hls_service.py central_server/api/webcam.py central_server/main.py central_server/tests/test_webcam_ingest.py central_server/tests/test_webcam_api.py
git commit -m "feat(server): webcam JPEG ingest + viewer lease model (fixes C1/C2/C3/H1/H2/H3)"
```

---

### Task 4: Dashboard — hls.js Vendor + API Functions + HlsPlayer Component

**Files:**
- Create: `sdprs/central_server/static/spa/vendor/hls.min.js` (download)
- Create: `sdprs/central_server/static/spa/vendor/VENDOR.md` (provenance + pinned SHA-256)
- Create: `sdprs/tools/spa/check_vendor.js` (integrity gate)
- Modify: `sdprs/tools/spa/run_all.js` (register the gate)
- Modify: `sdprs/central_server/static/spa/index.html`
- Modify: `sdprs/central_server/static/spa/api.jsx`
- Modify: `sdprs/central_server/static/spa/components.jsx`

**Interfaces:**
- Consumes: Server endpoints from Task 3
- Produces: `window.HlsPlayer` component
- Produces: `window.SDPRS_API.startWebcamStream(nodeId)`, `stopWebcamStream(nodeId)`, `createWebcamClient(name)`, `revokeWebcamKey(nodeId)`

- [ ] **Step 1: Vendor hls.js with a pinned SHA-256**

Global Constraint #1 is zero-CDN disaster resilience: this blob is fetched ONCE at
development time and served locally forever after. A size check ("is it >100KB") does not
establish that the bytes committed are the bytes intended, so pin the digest instead.

```bash
cd sdprs/central_server/static/spa/vendor
curl -fL -o hls.min.js "https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"
sha256sum hls.min.js          # record this value
head -c 120 hls.min.js        # sanity: minified JS, not an HTML error page
```

`curl -f` matters — without it a 404 or captive-portal page is written to the file and
"it's >100KB" still passes. If the fetch is blocked in this environment, STOP and report
NEEDS_CONTEXT rather than committing a placeholder.

Record provenance in `sdprs/central_server/static/spa/vendor/VENDOR.md`:

```markdown
# Vendored third-party assets

Runtime loads these from disk only — no CDN request is ever made in production.
Re-verify with: `cd tools/spa && npm run vendor`

| File | Version | Source | SHA-256 |
|------|---------|--------|---------|
| `hls.min.js` | 1.5.13 | https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js | `<paste digest>` |
```

- [ ] **Step 1b: Add the integrity gate**

Create `sdprs/tools/spa/check_vendor.js` — it parses the VENDOR.md table, re-hashes each
listed file, and exits non-zero on any mismatch or missing file. Keep it dependency-free
(node's built-in `crypto` and `fs` only), matching the other gates in that directory.

Register it in `sdprs/tools/spa/run_all.js` as a **blocking** check, placed first so a
corrupted vendor blob is reported before the syntax and render gates run against it:

```javascript
const CHECKS = [
  { name: 'vendor integrity', script: 'check_vendor.js', blocking: true },
  { name: 'scope invariant', script: 'scope_probe.js', blocking: true },
  { name: 'syntax',          script: 'check_spa_syntax.js', blocking: true },
  { name: 'undefined refs',  script: 'check_spa_refs.js', blocking: true },
  { name: 'render tests',    script: 'render_tests.js', blocking: true },
  { name: 'tailwind tokens', script: 'check_spa_classes.js', blocking: false },
];
```

Add a `"vendor": "node check_vendor.js"` entry to the `scripts` block of
`sdprs/tools/spa/package.json`.

- [ ] **Step 2: Add hls.min.js script tag to index.html**

In `sdprs/central_server/static/spa/index.html`, add after the babel.min.js script tag:

```html
<script src="/static/spa/vendor/hls.min.js"></script>
```

- [ ] **Step 3: Add webcam API functions to api.jsx**

In `sdprs/central_server/static/spa/api.jsx`, add before the `window.SDPRS_API = {` export:

```javascript
async function startWebcamStream(nodeId) {
  return apiFetch(`/api/webcam/${nodeId}/stream/start`, jsonBody('POST', {}));
}
async function stopWebcamStream(nodeId) {
  return apiFetch(`/api/webcam/${nodeId}/stream/stop`, jsonBody('POST', {}));
}
async function createWebcamClient(name) {
  return apiFetch('/api/nodes/webcam', jsonBody('POST', { name }));
}
async function revokeWebcamKey(nodeId) {
  return apiFetch(`/api/nodes/${nodeId}/revoke-key`, jsonBody('POST', {}));
}
```

Add to the `window.SDPRS_API` object:
```javascript
startWebcamStream, stopWebcamStream, createWebcamClient, revokeWebcamKey,
```

- [ ] **Step 4: Add HlsPlayer component to components.jsx**

In `sdprs/central_server/static/spa/components.jsx`, add before the final `window.` exports:

```javascript
const HlsPlayer = ({ nodeId, onFallback }) => {
  const videoRef = React.useRef(null);
  const hlsRef = React.useRef(null);
  const retryCount = React.useRef(0);

  React.useEffect(() => {
    const video = videoRef.current;
    if (!video || typeof Hls === 'undefined') return;

    const hls = new Hls({
      liveDurationInfinity: true,
      maxBufferLength: 5,
      maxMaxBufferLength: 10,
    });
    hlsRef.current = hls;

    hls.loadSource(`/api/webcam/${nodeId}/hls/playlist.m3u8`);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      video.play().catch(() => {});
      retryCount.current = 0;
    });

    hls.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        retryCount.current += 1;
        if (retryCount.current >= 3) {
          hls.destroy();
          hlsRef.current = null;
          if (onFallback) onFallback();
        } else {
          hls.recoverMediaError();
        }
      }
    });

    return () => {
      hls.destroy();
      hlsRef.current = null;
    };
  }, [nodeId]);

  return (
    <video
      ref={videoRef}
      autoPlay
      muted
      playsInline
      className="absolute inset-0 w-full h-full object-cover"
    />
  );
};
```

Add to the window export line:
```javascript
window.HlsPlayer = HlsPlayer;
```

- [ ] **Step 5: Run the SPA gates**

This SPA has no build step — every `.jsx` is compiled in-browser by vendored Babel, so a
syntax error or an undefined cross-file reference is invisible until a browser loads the
page. `sdprs/tools/spa/` exists to catch that offline and is the real verification here:

```bash
cd sdprs/tools/spa && npm run check
```

All blocking gates must pass. Two traps specific to this task:

- **Scope isolation.** Each `<script type="text/babel">` runs in its OWN top-level scope.
  `HlsPlayer` defined in `components.jsx` is NOT visible to `monitor.jsx` unless it is
  published as `window.HlsPlayer`. The `scope invariant` and `undefined refs` gates catch
  this; do not hand-wave them.
- **`Hls` is a global** from the vendored script tag, not an import. The `typeof Hls ===
  'undefined'` guard in the component is what keeps the page alive if the vendor file
  fails to load — keep it.

Then start the dev server, open the Dashboard, and confirm no console errors from
hls.min.js loading.

- [ ] **Step 6: Commit**

```bash
cd sdprs
git add central_server/static/spa/vendor/hls.min.js central_server/static/spa/vendor/VENDOR.md central_server/static/spa/index.html central_server/static/spa/api.jsx central_server/static/spa/components.jsx tools/spa/check_vendor.js tools/spa/run_all.js tools/spa/package.json
git commit -m "feat(dashboard): add hls.js vendor, HlsPlayer component, webcam API functions"
```

---

### Task 5: Integration — `node_type` End-to-End + Monitor Wall Webcam Tile

> **Merged task.** This absorbs what was originally Task 12. The two were separated in the
> first draft, which made Task 5 unbuildable: it keys the entire tile off
> `node.type === 'webcam'`, but nothing populated that value until Task 12 seven tasks
> later, so the badge would have been permanently-false dead code with no way to test it.
> Server field → SPA mapping → tile now land together and are verifiable in one diff.

**Files:**
- Modify: `sdprs/central_server/api/nodes.py` (include webcam cameras in `GET /api/nodes`)
- Modify: `sdprs/central_server/static/spa/api.jsx` (`mapNode` — recognise `webcam`)
- Modify: `sdprs/central_server/static/spa/pages/monitor.jsx` (badge + live button)
- Modify: `sdprs/central_server/static/spa/app.jsx` (route the two webcam WS events)
- Modify: `sdprs/tools/spa/render_tests.js` (badge render assertions)
- Test: `sdprs/central_server/tests/test_webcam_node_list.py`

**Interfaces:**
- Consumes: `window.HlsPlayer` (Task 4), `window.SDPRS_API.startWebcamStream/stopWebcamStream` (Task 4)
- Consumes: `webcam_cameras` table (Task 1)
- Produces: `GET /api/nodes` returns webcam rows with `node_type: "webcam"`
- Produces: `node.type === 'webcam'` in the SPA's mapped node shape

- [ ] **Step 0a: Return webcam cameras from `GET /api/nodes`**

In `sdprs/central_server/api/nodes.py`, in `list_nodes`, append webcam rows after the
existing node list is built:

```python
if get_backend() == "postgresql":
    webcam_rows = _pg_fetch_many_sync(
        "SELECT node_id, name, status, last_upload FROM webcam_cameras", {})
else:
    with get_db_cursor() as cursor:
        cursor.execute("SELECT node_id, name, status, last_upload FROM webcam_cameras")
        webcam_rows = [dict(r) for r in cursor.fetchall()]

for wc in webcam_rows:
    last_upload = wc.get("last_upload")
    is_stale = False
    if last_upload:
        try:
            age = (utcnow() - datetime.fromisoformat(last_upload)).total_seconds()
            is_stale = age > STALE_THRESHOLD_SECONDS
        except (TypeError, ValueError):
            pass
    nodes.append(NodeStatus(
        node_id=wc["node_id"],
        node_type="webcam",
        status=wc.get("status") or "OFFLINE",
        location=wc.get("name"),
        last_heartbeat=last_upload,
        snapshot_timestamp=last_upload,
        is_stale=is_stale,
    ))
```

Three corrections against the original Task 12 text, which would not have run:

1. **Do NOT change `NodeStatus.node_type` to `Optional[str] = None`.** The original said
   "add if missing" — it is already present and **required** (`node_type: str`). Relaxing
   it would weaken the contract for pump and glass nodes too. Leave it required.
2. The original passed `heartbeat=` and `upload=` — **neither is a field on `NodeStatus`**
   (they are `last_heartbeat` / `snapshot_timestamp`; `heartbeat` and `upload` are names in
   the SPA's *mapped* shape, not the server model). As written it raised a pydantic
   validation error.
3. Import `datetime` and `utcnow` at module top, not inside the loop. Use
   `central_server.timeutil.utcnow()` — naive-UTC — never `datetime.utcnow()`.

- [ ] **Step 0b: Recognise `webcam` in `mapNode`**

`sdprs/central_server/static/spa/api.jsx` line ~262 currently collapses every non-pump
node to `'camera'`:

```javascript
const type = n.node_type === 'pump' ? 'pump' : 'camera';
```

A webcam therefore arrives as `'camera'` today and the tile's `node.type === 'webcam'`
test can never be true. Widen it:

```javascript
const type = n.node_type === 'pump' ? 'pump'
           : n.node_type === 'webcam' ? 'webcam'
           : 'camera';
// Webcams are camera-like for freshness purposes: their "upload" age comes from
// snapshot_timestamp (not the heartbeat), and staleness downgrades them to warn.
// Introducing 'webcam' as a THIRD type silently excluded them from both rules,
// because every such check below was written as `type === 'camera'`.
const cameraLike = (type === 'camera' || type === 'webcam');
```

Then replace the `type === 'camera'` tests in this function with `cameraLike` for the
staleness downgrade (~line 282) and the `up` computation (~line 303). Leave the
visual/audio-health degradation check keyed to `type === 'camera'` only — a webcam client
reports neither, so `'unknown'` must not be read as degraded.

- [ ] **Step 1: Add webcam badge and live button to NodeCard**

In `sdprs/central_server/static/spa/pages/monitor.jsx`, modify the `NodeCard` component. After the existing status dot / type indicator area, add a source badge:

```javascript
// Inside NodeCard, after the existing type/status indicators:
const isWebcam = node.type === 'webcam';
```

Add badge element in the tile header area:
```javascript
{isWebcam && (
  <span className="absolute top-1 left-1 z-10 px-1.5 py-0.5 rounded text-[9px] font-bold bg-sev-info/90 text-sev-info-fg uppercase tracking-wide">
    Webcam
  </span>
)}
{!isWebcam && node.type === 'camera' && (
  <span className="absolute top-1 left-1 z-10 px-1.5 py-0.5 rounded text-[9px] font-bold bg-ink-muted/60 text-surface-base uppercase tracking-wide">
    Edge Cam
  </span>
)}
```

- [ ] **Step 2: Add live view state and button to NodeCard**

Add state inside NodeCard:
```javascript
const [liveMode, setLiveMode] = useState_p('off'); // 'off' | 'loading' | 'live'
```

Add the live button (only for webcam type, shown on hover or always):
```javascript
{isWebcam && liveMode === 'off' && (
  <button
    onClick={(e) => {
      e.stopPropagation();
      setLiveMode('loading');
      const api = window.SDPRS_API;
      api.startWebcamStream(node.id)
        .then(() => setTimeout(() => setLiveMode('live'), 3000))
        .catch(() => setLiveMode('off'));
    }}
    className="absolute bottom-1 right-1 z-10 px-2 py-1 rounded bg-sev-info/80 hover:bg-sev-info text-white text-[10px] font-bold transition-colors"
  >
    ▶ 即時
  </button>
)}
{isWebcam && liveMode === 'loading' && (
  <div className="absolute bottom-1 right-1 z-10 px-2 py-1 rounded bg-surface-overlay/80 text-ink-secondary text-[10px]">
    連線中...
  </div>
)}
{isWebcam && liveMode === 'live' && (
  <button
    onClick={(e) => {
      e.stopPropagation();
      window.SDPRS_API.stopWebcamStream(node.id).catch(() => {});
      setLiveMode('off');
    }}
    className="absolute top-1 right-1 z-20 px-2 py-1 rounded bg-sev-critical/80 hover:bg-sev-critical text-white text-[10px] font-bold"
  >
    ● LIVE ✕
  </button>
)}
```

- [ ] **Step 3: Replace SnapshotImage with HlsPlayer in live mode**

In the image area of NodeCard, conditionally render:
```javascript
{isWebcam && liveMode === 'live' ? (
  <HlsPlayer nodeId={node.id} onFallback={() => setLiveMode('off')} />
) : (
  <SnapshotImage node={node} />
)}
```

- [ ] **Step 4: Route the webcam WS events in `app.jsx`**

`app.jsx`'s `onEvent` handler is an explicit if/else-if allowlist of `type` values with
**no default branch** (~line 646). Adding the two types to `api.jsx`'s `_WS_EVENT_TYPES` in
Task 3 lets them reach `onEvent`, but they then match no branch and nothing refreshes.
Extend the existing node-event branch:

```javascript
} else if (type === 'node_status' || type === 'pump_status' || type === 'node_deleted' ||
           type === 'webcam_stream_started' || type === 'webcam_stream_stopped') {
  scheduleRefresh();
}
```

The original text here was wrong three ways and must not be transcribed:

- It tested `data.type`. The callback signature is `onEvent(type, data)` — `type` is the
  first parameter and `data` is already the unwrapped `msg.data` payload, so `data.type`
  is `undefined` and the branch never fires.
- It called `window.SDPRS_API.refreshLive()` directly. Use the local `scheduleRefresh()`,
  which is the 300ms-debounced coalescing helper every other event branch uses; calling
  `refreshLive` directly bypasses that and can stampede on a burst of stream events.
- It handled only `webcam_stream_stopped`, leaving `webcam_stream_started` inert.

Read the `SHL-1` comment immediately above this branch before editing. It documents a
`weather` event that was dead in both directions — missing from the whitelist AND never
emitted by any backend path — and is precisely the failure mode this step exists to avoid.
A three-way change (backend emit + `api.jsx` whitelist + this branch) is one deliberate
unit; land all three or the feature silently does nothing.

- [ ] **Step 5: Automated verification (this is the gate, not the browser check)**

**5a — backend.** Create `sdprs/central_server/tests/test_webcam_node_list.py` asserting
that a registered webcam camera appears in `GET /api/nodes` with `node_type == "webcam"`,
that `location`/`last_heartbeat`/`snapshot_timestamp` carry the expected values, and that
a camera whose `last_upload` is older than `STALE_THRESHOLD_SECONDS` comes back
`is_stale: true`. Follow the fixture style of the already-passing
`central_server/tests/test_webcam_nodes_api.py` (`@pytest.mark.anyio` + `AsyncClient` +
`ASGITransport`; anyio's pytest plugin supplies `anyio_backend`, no extra config needed).

```bash
cd sdprs && /c/Python314/python -m pytest central_server/tests/test_webcam_node_list.py -v -p no:cacheprovider
```

**5b — regression guard.** `mapNode` is shared by every node type, so re-run the suites
that assert node-shape behaviour:

```bash
cd sdprs
/c/Python314/python -m pytest central_server/tests/test_nodes_api.py -q -p no:cacheprovider
/c/Python314/python -m pytest central_server/tests/test_ws_event_contract.py -q -p no:cacheprovider
```

**5c — SPA render assertions.** Add cases to `sdprs/tools/spa/render_tests.js` covering:
a node with `node_type: 'webcam'` renders the `Webcam` badge; a node with
`node_type: 'glass'` renders `Edge Cam` and NOT the webcam badge; the live button is
absent for non-webcam nodes. Then:

```bash
cd sdprs/tools/spa && npm run check
```

All blocking gates must pass. This is the step that proves the badge is reachable — the
whole reason Tasks 5 and 12 were merged.

- [ ] **Step 6: Browser sanity check**

Start the server and confirm by eye: webcam nodes show the blue `Webcam` badge, edge cams
show grey `Edge Cam`, `▶ 即時` goes loading → video (or falls back cleanly with no client
connected), and `● LIVE ✕` returns to snapshot mode.

- [ ] **Step 7: Commit**

```bash
cd sdprs
git add central_server/api/nodes.py central_server/static/spa/api.jsx central_server/static/spa/pages/monitor.jsx central_server/static/spa/app.jsx central_server/tests/test_webcam_node_list.py tools/spa/render_tests.js
git commit -m "feat(dashboard): surface webcam nodes end-to-end with badge and live view"
```

---

### Task 6: Dashboard — Node Management UI (Add Webcam Client + Revoke Key)

**Files:**
- Modify: `sdprs/central_server/static/spa/pages/status.jsx`

**Interfaces:**
- Consumes: `window.SDPRS_API.createWebcamClient(name)`, `revokeWebcamKey(nodeId)` (Task 4)

- [ ] **Step 1: Add "新增 Webcam Client" button and modal to StatusPage**

In `sdprs/central_server/static/spa/pages/status.jsx`, add state:
```javascript
const [showAddModal, setShowAddModal] = useState_p(false);
const [newClientName, setNewClientName] = useState_p('');
const [createdKey, setCreatedKey] = useState_p(null);
const [addBusy, setAddBusy] = useState_p(false);
```

Add button in the page header area:
```javascript
<button
  onClick={() => { setShowAddModal(true); setCreatedKey(null); setNewClientName(''); }}
  className="px-3 py-1.5 rounded-lg bg-sev-info text-white text-xs font-bold hover:opacity-90 transition-opacity"
>
  + 新增 Webcam Client
</button>
```

- [ ] **Step 2: Add the modal component**

```javascript
{showAddModal && (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowAddModal(false)}>
    <div className="bg-surface-panel border border-border-subtle rounded-xl p-5 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
      <h3 className="text-sm font-bold text-ink-primary mb-3">新增 Webcam Client</h3>
      {!createdKey ? (
        <>
          <label className="block text-xs text-ink-secondary mb-1">名稱（如：櫃台電腦）</label>
          <input
            value={newClientName}
            onChange={e => setNewClientName(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-surface-base border border-border-subtle text-ink-primary text-sm mb-3"
            placeholder="輸入名稱..."
            autoFocus
          />
          <button
            disabled={addBusy || !newClientName.trim()}
            onClick={() => {
              setAddBusy(true);
              window.SDPRS_API.createWebcamClient(newClientName.trim())
                .then(data => setCreatedKey(data))
                .catch(err => setToast({ msg: err.message || '建立失敗', tone: 'critical' }))
                .finally(() => setAddBusy(false));
            }}
            className="w-full py-2 rounded-lg bg-sev-info text-white text-sm font-bold disabled:opacity-50"
          >
            {addBusy ? '建立中...' : '建立'}
          </button>
        </>
      ) : (
        <>
          <p className="text-xs text-sev-warn font-bold mb-2">⚠ API Key 僅顯示一次，請立即複製</p>
          <div className="bg-surface-base border border-border-subtle rounded-lg p-3 mb-3">
            <code className="text-xs text-ink-primary break-all select-all">{createdKey.api_key}</code>
          </div>
          <p className="text-xs text-ink-muted mb-3">Node ID: {createdKey.node_id}</p>
          <button
            onClick={() => { setShowAddModal(false); onRefresh && onRefresh(); }}
            className="w-full py-2 rounded-lg bg-sev-ok text-white text-sm font-bold"
          >
            已複製，關閉
          </button>
        </>
      )}
    </div>
  </div>
)}
```

- [ ] **Step 3: Add revoke key button to webcam rows in the table**

In the action column of the node table, for webcam-type nodes:
```javascript
{node.type === 'webcam' && (
  <button
    title="撤銷並重新產生 API Key"
    onClick={(e) => {
      e.stopPropagation();
      if (!confirm('確定要撤銷此 Key？舊 Key 將立即失效。')) return;
      window.SDPRS_API.revokeWebcamKey(node.id)
        .then(data => {
          setToast({ msg: `新 Key: ${data.api_key}`, tone: 'info' });
        })
        .catch(err => setToast({ msg: err.message || '撤銷失敗', tone: 'critical' }));
    }}
    className="w-8 h-8 rounded text-ink-muted hover:text-sev-warn hover:bg-sev-warn/10 transition-colors text-xs"
  >
    🔑
  </button>
)}
```

- [ ] **Step 4: Test in browser**

Verify:
- "新增 Webcam Client" button opens modal
- Creating a client shows the API key
- Revoke button shows confirm dialog and displays new key in toast

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add central_server/static/spa/pages/status.jsx
git commit -m "feat(dashboard): add webcam client management UI (create + revoke key)"
```

---

### Task 7: Client — Config Module + Camera Manager

**Files:**
- Create: `sdprs/webcam_client/__init__.py`
- Create: `sdprs/webcam_client/config.py`
- Create: `sdprs/webcam_client/camera_manager.py`
- Test: `sdprs/webcam_client/tests/test_config.py`
- Test: `sdprs/webcam_client/tests/test_camera_manager.py`

**Interfaces:**
- Produces: `load_config() -> dict`, `save_config(config: dict)`, `get_config_path() -> Path`
- Produces: `scan_cameras(max_index=10) -> list[dict]`
- Produces: `compute_motion(frame, prev_frame, threshold=25) -> float`
- Produces: `adaptive_fps(motion_ratio, target_fps=8) -> int`

- [ ] **Step 1: Create config.py**

```python
# sdprs/webcam_client/config.py
import base64
import ctypes
import json
import logging
import os
from ctypes import wintypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger("webcam_client.config")

_APP_NAME = "SDPRSWebcam"
_CONFIG_FILENAME = "config.json"

DEFAULT_CONFIG = {
    "server_url": "",
    "api_key": "",
    "cameras": [],
    "motion_threshold": 25,
    "heartbeat_interval": 30,
}


# --- Windows DPAPI (spec §258) ------------------------------------------------
# api_key is encrypted at rest, scoped to the current Windows user, via
# CryptProtectData / CryptUnprotectData reached through ctypes (no third-party
# package). On disk the field is "api_key_encrypted" (base64 blob); in memory
# load_config() presents a plaintext "api_key" so every downstream consumer is
# unchanged. Decrypt failure == unconfigured; NEVER fall back to a plaintext key.

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))  # keep alive across the call
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return base64.b64encode(
            ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob_b64: str) -> Optional[str]:
    try:
        raw = base64.b64decode(blob_b64, validate=True)
    except Exception:
        return None
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def get_config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / _APP_NAME


def get_config_path() -> Path:
    return get_config_dir() / _CONFIG_FILENAME


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load config: {e}")
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    # Decrypt api_key from its DPAPI blob. A plaintext "api_key" left on disk is
    # deliberately NOT honored (spec §258): decrypt failure -> unconfigured.
    enc = merged.pop("api_key_encrypted", "")
    plaintext = _dpapi_unprotect(enc) if enc else None
    if enc and plaintext is None:
        logger.error("api_key decrypt failed -- treating as unconfigured")
    merged["api_key"] = plaintext or ""
    return merged


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    to_write = dict(config)
    api_key = to_write.pop("api_key", "")
    to_write.pop("api_key_encrypted", None)
    if api_key:
        to_write["api_key_encrypted"] = _dpapi_protect(api_key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, ensure_ascii=False, indent=2)
    logger.info(f"Config saved to {path}")


def is_first_run() -> bool:
    return not get_config_path().exists()
```

- [ ] **Step 2: Create camera_manager.py**

```python
# sdprs/webcam_client/camera_manager.py
import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("webcam_client.camera")


def scan_cameras(max_index: int = 10) -> List[dict]:
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if os.name == "nt" else 0)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append({"device_index": i, "width": w, "height": h})
            cap.release()
        else:
            cap.release()
    return found


def compute_motion(frame: np.ndarray, prev_frame: Optional[np.ndarray], threshold: int = 25) -> float:
    if prev_frame is None:
        return 1.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    prev_gray = cv2.GaussianBlur(prev_gray, (21, 21), 0)
    diff = cv2.absdiff(gray, prev_gray)
    motion_ratio = float((diff > threshold).sum()) / diff.size
    return motion_ratio


def adaptive_fps(motion_ratio: float, target_fps: int = 8) -> int:
    if motion_ratio < 0.01:
        return 1
    elif motion_ratio < 0.05:
        return 3
    else:
        return target_fps


def open_camera(device_index: int, width: int = 640, height: int = 480) -> Optional[cv2.VideoCapture]:
    backend = cv2.CAP_DSHOW if os.name == "nt" else 0
    cap = cv2.VideoCapture(device_index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


import os  # noqa: E402 (needed for os.name check above)
```

- [ ] **Step 3: Write tests**

```python
# sdprs/webcam_client/tests/test_config.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.config import load_config, save_config, get_config_path, DEFAULT_CONFIG


def test_load_config_default(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = load_config()
    assert config["server_url"] == ""
    assert config["cameras"] == []
    assert config["motion_threshold"] == 25


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = {"server_url": "https://example.com", "api_key": "sk-test", "cameras": [{"name": "Cam1"}]}
    save_config(config)
    loaded = load_config()
    assert loaded["server_url"] == "https://example.com"
    assert loaded["api_key"] == "sk-test"       # round-trips through DPAPI in memory
    assert loaded["cameras"] == [{"name": "Cam1"}]
    assert loaded["motion_threshold"] == 25  # default merged


def test_api_key_encrypted_at_rest(tmp_path, monkeypatch):
    # spec §258: the key must never touch disk in plaintext.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_config({"server_url": "https://example.com", "api_key": "sk-secret-xyz"})
    raw = get_config_path().read_text(encoding="utf-8")
    assert "sk-secret-xyz" not in raw            # plaintext key must not hit disk
    assert "api_key_encrypted" in raw
    loaded = load_config()
    assert loaded["api_key"] == "sk-secret-xyz"  # decrypted in memory
    assert "api_key_encrypted" not in loaded     # blob not surfaced to callers


def test_bad_encrypted_blob_is_unconfigured(tmp_path, monkeypatch):
    # Decrypt failure must degrade to unconfigured, never crash or leak.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"server_url": "https://x", "api_key_encrypted": "!!!not-base64!!!"}',
                    encoding="utf-8")
    assert load_config()["api_key"] == ""
```

```python
# sdprs/webcam_client/tests/test_camera_manager.py
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.camera_manager import compute_motion, adaptive_fps


def test_compute_motion_no_prev():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert compute_motion(frame, None) == 1.0


def test_compute_motion_identical():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ratio = compute_motion(frame, frame.copy())
    assert ratio < 0.01


def test_compute_motion_different():
    frame1 = np.zeros((480, 640, 3), dtype=np.uint8)
    frame2 = np.ones((480, 640, 3), dtype=np.uint8) * 255
    ratio = compute_motion(frame2, frame1)
    assert ratio > 0.5


def test_adaptive_fps():
    assert adaptive_fps(0.005) == 1
    assert adaptive_fps(0.03) == 3
    assert adaptive_fps(0.1, target_fps=10) == 10
```

- [ ] **Step 4: Run tests**

Run per-file (this machine has no `python` alias and whole-dir pytest hits the
`[Cloud]` bracket-parametrization trap — see the env note in the brief):
```bash
cd sdprs
/c/Python314/python -m pytest webcam_client/tests/test_config.py -q -p no:cacheprovider
/c/Python314/python -m pytest webcam_client/tests/test_camera_manager.py -q -p no:cacheprovider
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/
git commit -m "feat(client): add config module and camera manager with motion detection"
```

---

### Task 8: Client — Push Engine (1Hz JPEG + HLS Mode)

> **Revision 2026-07-21 (audit C1).** The 1Hz path now targets the webcam ingest
> route `POST /api/webcam/{node_id}/snapshot` (NOT `/api/edge/...`, which is gated by
> the global `EDGE_API_KEY` and 401s the per-client webcam key), and every push calls
> `raise_for_status()` and logs failures at WARNING — spec §303/§322. This is the
> client half of the contract Task 3b implements on the server; the two are
> file-disjoint and build in parallel against the same frozen contract.

**Files:**
- Create: `sdprs/webcam_client/push_engine.py`
- Create: `sdprs/webcam_client/hls_encoder.py`
- Test: `sdprs/webcam_client/tests/test_push_engine.py`

**Interfaces:**
- Consumes: `camera_manager.open_camera`, `compute_motion`, `adaptive_fps` (Task 7)
- Produces: `PushEngine` class with `start()`, `stop()`, `set_streaming(bool)` methods
- Produces: `HlsEncoder` class wrapping FFmpeg subprocess

- [ ] **Step 1: Create hls_encoder.py**

```python
# sdprs/webcam_client/hls_encoder.py
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("webcam_client.hls_encoder")


class HlsEncoder:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 8, output_dir: Optional[Path] = None):
        self._width = width
        self._height = height
        self._fps = fps
        self._output_dir = output_dir or Path("./hls_out")
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._segment_count = 0

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return True
            self._output_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{self._width}x{self._height}",
                "-pix_fmt", "bgr24",
                "-r", str(self._fps),
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-g", str(self._fps * 2),
                "-hls_time", "2",
                "-hls_list_size", "5",
                "-hls_segment_filename", str(self._output_dir / "seg_%06d.ts"),
                "-f", "hls",
                str(self._output_dir / "playlist.m3u8"),
            ]
            try:
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                logger.info("FFmpeg HLS encoder started")
                return True
            except FileNotFoundError:
                logger.error("ffmpeg not found in PATH")
                return False

    def write_frame(self, frame_bytes: bytes) -> bool:
        with self._lock:
            if not self.is_running or self._process.stdin is None:
                return False
            try:
                self._process.stdin.write(frame_bytes)
                self._process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def stop(self) -> None:
        with self._lock:
            if self._process is not None:
                try:
                    if self._process.stdin:
                        self._process.stdin.close()
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
                self._process = None
                logger.info("FFmpeg HLS encoder stopped")

    def get_new_segments(self) -> list:
        ts_files = sorted(self._output_dir.glob("seg_*.ts"))
        new = ts_files[self._segment_count:]
        self._segment_count = len(ts_files)
        playlist = self._output_dir / "playlist.m3u8"
        result = [(f.name, f.read_bytes()) for f in new]
        if playlist.exists():
            result.append(("playlist.m3u8", playlist.read_bytes()))
        return result
```

- [ ] **Step 2: Create push_engine.py**

```python
# sdprs/webcam_client/push_engine.py
import logging
import threading
import time
from typing import Optional

import cv2
import httpx

from .camera_manager import open_camera, compute_motion, adaptive_fps
from .hls_encoder import HlsEncoder

logger = logging.getLogger("webcam_client.push_engine")


class PushEngine(threading.Thread):
    def __init__(self, camera_config: dict, server_url: str, api_key: str):
        super().__init__(daemon=True)
        self._cam_config = camera_config
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_id = camera_config.get("node_id", "")
        self._resolution = tuple(camera_config.get("resolution", [640, 480]))
        self._jpeg_quality = camera_config.get("jpeg_quality", 40)
        self._target_fps = camera_config.get("target_fps", 8)
        self._motion_threshold = camera_config.get("motion_threshold", 25)

        self._stop_event = threading.Event()
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._encoder: Optional[HlsEncoder] = None
        self._client: Optional[httpx.Client] = None

    def set_streaming(self, enabled: bool) -> None:
        with self._stream_lock:
            if enabled == self._streaming:
                return
            self._streaming = enabled
            if enabled:
                self._start_encoder()
            else:
                self._stop_encoder()

    def _start_encoder(self) -> None:
        self._encoder = HlsEncoder(
            width=self._resolution[0], height=self._resolution[1], fps=self._target_fps
        )
        if not self._encoder.start():
            self._encoder = None
            self._streaming = False

    def _stop_encoder(self) -> None:
        if self._encoder:
            self._encoder.stop()
            self._encoder = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(5.0, connect=3.0),
            headers={"X-API-Key": self._api_key},
        )
        cap = open_camera(self._cam_config.get("device_index", 0), *self._resolution)
        if cap is None:
            logger.error(f"Cannot open camera {self._cam_config.get('device_index')}")
            return

        prev_frame = None
        last_snapshot_time = 0.0
        last_hls_upload = 0.0

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                motion = compute_motion(frame, prev_frame, self._motion_threshold)
                prev_frame = frame
                now = time.time()

                with self._stream_lock:
                    streaming = self._streaming

                if streaming and self._encoder:
                    fps = adaptive_fps(motion, self._target_fps)
                    interval = 1.0 / fps
                    if now - last_snapshot_time >= interval:
                        self._encoder.write_frame(frame.tobytes())
                        last_snapshot_time = now
                        if now - last_hls_upload >= 2.0:
                            self._upload_segments()
                            last_hls_upload = now
                else:
                    if motion < 0.01 and now - last_snapshot_time < 2.0:
                        time.sleep(0.05)
                        continue
                    if now - last_snapshot_time >= 1.0:
                        self._push_snapshot(frame)
                        last_snapshot_time = now

                time.sleep(0.01)
        finally:
            cap.release()
            self._stop_encoder()
            if self._client:
                self._client.close()

    def _push_snapshot(self, frame) -> None:
        try:
            small = cv2.resize(frame, self._resolution)
            _, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
            # Webcam ingest route (spec §303). NOT /api/edge/... — that path is gated
            # by the global EDGE_API_KEY and would 401 the per-client webcam key.
            url = f"{self._server_url}/api/webcam/{self._node_id}/snapshot"
            resp = self._client.post(url, content=jpeg.tobytes(),
                                     headers={"Content-Type": "image/jpeg"})
            # httpx does NOT raise on 4xx without this. Spec §322: a silent 401 leaves
            # the tray green and the dashboard tile permanently blank — surface it.
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Snapshot push to {self._node_id} failed: {e}")

    def _upload_segments(self) -> None:
        if not self._encoder:
            return
        try:
            segments = self._encoder.get_new_segments()
            for filename, data in segments:
                url = f"{self._server_url}/api/webcam/{self._node_id}/hls/{filename}"
                resp = self._client.put(url, content=data)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"HLS upload for {self._node_id} failed: {e}")
```

- [ ] **Step 3: Write test for push engine logic**

```python
# sdprs/webcam_client/tests/test_push_engine.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.push_engine import PushEngine


def test_push_engine_init():
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480],
              "jpeg_quality": 40, "target_fps": 8, "motion_threshold": 25}
    engine = PushEngine(config, "https://example.com", "sk-test")
    assert engine._node_id == "webcam_01"
    assert engine._streaming is False


def test_set_streaming_flag():
    config = {"node_id": "webcam_01", "device_index": 0}
    engine = PushEngine(config, "https://example.com", "sk-test")
    with patch.object(engine, "_start_encoder"):
        engine.set_streaming(True)
        assert engine._streaming is True
    with patch.object(engine, "_stop_encoder"):
        engine.set_streaming(False)
        assert engine._streaming is False


def test_push_snapshot_uses_webcam_endpoint_and_raises(monkeypatch):
    # C1 client-side guard: normal-mode frames go to /api/webcam/.../snapshot
    # (never /api/edge), and a 4xx must surface via raise_for_status(), not be
    # swallowed. This is the regression that made the whole feature fail silently.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    engine = PushEngine(config, "https://example.com", "sk-test")
    mock_resp = MagicMock()
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client
    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))
    posted_url = mock_client.post.call_args[0][0]
    assert "/api/webcam/webcam_01/snapshot" in posted_url
    assert "/api/edge/" not in posted_url
    mock_resp.raise_for_status.assert_called_once()
```

- [ ] **Step 4: Run tests**

Run per-file (no `python` alias; whole-dir pytest hits the `[Cloud]` trap):
```bash
cd sdprs
/c/Python314/python -m pytest webcam_client/tests/test_push_engine.py -q -p no:cacheprovider
```
Expected: All PASS (including `test_push_snapshot_uses_webcam_endpoint_and_raises`).

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/push_engine.py webcam_client/hls_encoder.py webcam_client/tests/test_push_engine.py
git commit -m "feat(client): add push engine with 1Hz JPEG and HLS streaming modes"
```

---

### Task 9: Client — Control Channel (HTTP Long-Poll)

**Files:**
- Create: `sdprs/webcam_client/control_channel.py`
- Test: `sdprs/webcam_client/tests/test_control_channel.py`

**Interfaces:**
- Consumes: Server `GET /api/webcam/{node_id}/commands?timeout=5` (Task 3)
- Produces: `ControlChannel` thread that dispatches commands via callbacks

- [ ] **Step 1: Create control_channel.py**

```python
# sdprs/webcam_client/control_channel.py
import logging
import threading
import time
from typing import Callable, Dict, Optional

import httpx

logger = logging.getLogger("webcam_client.control")


class ControlChannel(threading.Thread):
    def __init__(self, server_url: str, api_key: str, node_ids: list,
                 on_command: Callable[[str, str, Optional[dict]], None]):
        super().__init__(daemon=True)
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_ids = node_ids
        self._on_command = on_command
        self._stop_event = threading.Event()
        self._client: Optional[httpx.Client] = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"X-API-Key": self._api_key},
        )
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                for node_id in self._node_ids:
                    if self._stop_event.is_set():
                        break
                    self._poll_node(node_id)
                backoff = 1.0
            except httpx.ConnectError:
                logger.warning(f"Control channel connection failed, retry in {backoff}s")
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as e:
                logger.debug(f"Control channel error: {e}")
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
        if self._client:
            self._client.close()

    def _poll_node(self, node_id: str) -> None:
        url = f"{self._server_url}/api/webcam/{node_id}/commands"
        resp = self._client.get(url, params={"timeout": 5})
        if resp.status_code == 200:
            data = resp.json()
            cmd = data.get("command")
            if cmd:
                params = data.get("params")
                logger.info(f"Received command: {cmd} for {node_id}")
                self._on_command(node_id, cmd, params)
        elif resp.status_code == 401:
            logger.error("API key rejected — stopping control channel")
            self._stop_event.set()
```

- [ ] **Step 2: Write test**

```python
# sdprs/webcam_client/tests/test_control_channel.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.control_channel import ControlChannel


def test_control_channel_init():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    assert ch._node_ids == ["webcam_01"]
    assert not ch._stop_event.is_set()


def test_stop():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    ch.stop()
    assert ch._stop_event.is_set()
```

- [ ] **Step 3: Run tests**

Run: `cd sdprs && python -m pytest webcam_client/tests/test_control_channel.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd sdprs
git add webcam_client/control_channel.py webcam_client/tests/test_control_channel.py
git commit -m "feat(client): add HTTP long-poll control channel for stream commands"
```

---

### Task 10: Client — GUI (Setup Wizard + System Tray)

> **Revision 2026-07-21 (audit M2 / design-decision #4).** The first plan silently
> dropped the spec's live camera preview (§173 item 6) and per-camera naming
> (§173 item 5). Reinstated here as `gui/preview.py` with a **pure, unit-testable**
> `resize_keep_aspect` core (headless — no Tk/camera), wired into the wizard as a
> best-effort thumbnail + a name field per camera. GUI wiring itself is verifiable
> only by `py_compile`/import (no display on the build box); the preview math and
> config-building are the parts that get real tests.

**Files:**
- Create: `sdprs/webcam_client/gui/__init__.py`
- Create: `sdprs/webcam_client/gui/preview.py`
- Create: `sdprs/webcam_client/gui/setup_wizard.py`
- Create: `sdprs/webcam_client/gui/tray_app.py`
- Test: `sdprs/webcam_client/tests/test_gui_preview.py`

**Interfaces:**
- Consumes: `config.load_config`, `save_config`, `camera_manager.scan_cameras` (Task 7)
- Produces: `run_setup_wizard() -> dict` (returns completed config, with user-entered names)
- Produces: `make_thumbnail(frame)`, `resize_keep_aspect(frame, max_size)`, `grab_preview_frame(idx)`
- Produces: `TrayApp` class with `start()`, `stop()`, `set_status(str)`

- [ ] **Step 1: Create gui/__init__.py**

```python
# sdprs/webcam_client/gui/__init__.py
```

- [ ] **Step 1b: Create gui/preview.py (audit M2 — reinstated preview)**

```python
# sdprs/webcam_client/gui/preview.py
import logging
import os
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("webcam_client.gui.preview")


def resize_keep_aspect(frame: np.ndarray, max_size=(160, 120)) -> np.ndarray:
    """Resize a frame to fit within max_size (w, h), preserving aspect ratio and
    never upscaling. Pure + headless — the unit-tested core of the preview."""
    h, w = frame.shape[:2]
    max_w, max_h = max_size
    scale = min(max_w / w, max_h / h, 1.0)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    return cv2.resize(frame, (new_w, new_h))


def make_thumbnail(frame: Optional[np.ndarray], max_size=(160, 120)):
    """Downscale a BGR frame to a Tk PhotoImage thumbnail. Returns None if the
    frame is None or Tk/Pillow is unavailable — the wizard then simply omits the
    preview and never blocks setup on a bad device."""
    if frame is None:
        return None
    resized = resize_keep_aspect(frame, max_size)
    try:
        from PIL import Image, ImageTk
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        return ImageTk.PhotoImage(Image.fromarray(rgb))
    except Exception as e:
        logger.debug(f"thumbnail render skipped: {e}")
        return None


def grab_preview_frame(device_index: int) -> Optional[np.ndarray]:
    """Open the camera, grab ONE frame, release. Returns None on any failure.
    Requires hardware — not exercised by unit tests."""
    backend = cv2.CAP_DSHOW if os.name == "nt" else 0
    cap = cv2.VideoCapture(device_index, backend)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()
```

- [ ] **Step 2: Create setup_wizard.py**

```python
# sdprs/webcam_client/gui/setup_wizard.py
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import httpx

from ..camera_manager import scan_cameras
from .preview import make_thumbnail, grab_preview_frame

logger = logging.getLogger("webcam_client.gui.wizard")


def run_setup_wizard(existing_config: Optional[dict] = None) -> Optional[dict]:
    result = {"config": None}
    root = tk.Tk()
    root.title("SDPRS Webcam 設定")
    root.geometry("500x450")
    root.resizable(False, False)

    config = existing_config or {}
    cameras_found = []

    # --- Frame: Server connection ---
    frame_conn = ttk.LabelFrame(root, text="伺服器連線", padding=10)
    frame_conn.pack(fill="x", padx=10, pady=5)

    ttk.Label(frame_conn, text="Server URL:").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=config.get("server_url", ""))
    url_entry = ttk.Entry(frame_conn, textvariable=url_var, width=40)
    url_entry.grid(row=0, column=1, padx=5)

    ttk.Label(frame_conn, text="API Key:").grid(row=1, column=0, sticky="w", pady=5)
    key_var = tk.StringVar(value=config.get("api_key", ""))
    key_entry = ttk.Entry(frame_conn, textvariable=key_var, width=40, show="*")
    key_entry.grid(row=1, column=1, padx=5, pady=5)

    status_var = tk.StringVar(value="")
    ttk.Label(frame_conn, textvariable=status_var, foreground="gray").grid(row=2, column=0, columnspan=2)

    # --- Frame: Camera selection ---
    frame_cam = ttk.LabelFrame(root, text="攝影機", padding=10)
    frame_cam.pack(fill="both", expand=True, padx=10, pady=5)

    cam_vars = []
    cam_frame_inner = ttk.Frame(frame_cam)
    cam_frame_inner.pack(fill="both", expand=True)

    def do_scan():
        status_var.set("掃描中...")
        root.update()
        cams = scan_cameras()
        cameras_found.clear()
        cameras_found.extend(cams)
        for w in cam_frame_inner.winfo_children():
            w.destroy()
        cam_vars.clear()
        if not cams:
            ttk.Label(cam_frame_inner, text="未偵測到攝影機").pack()
        for cam in cams:
            var = tk.BooleanVar(value=True)
            name_var = tk.StringVar(value=f"Webcam {cam['device_index']}")
            cam_vars.append((cam, var, name_var))
            row = ttk.Frame(cam_frame_inner)
            row.pack(fill="x", anchor="w", pady=2)
            ttk.Checkbutton(row,
                text=f"Camera {cam['device_index']} ({cam['width']}x{cam['height']})",
                variable=var).pack(side="left")
            ttk.Label(row, text="名稱:").pack(side="left", padx=(8, 2))
            ttk.Entry(row, textvariable=name_var, width=16).pack(side="left")
            # Spec §173 item 6: live preview thumbnail per camera. Best-effort — a
            # frame grab may fail (camera busy); make_thumbnail(None) yields None so
            # the wizard just omits the image and never blocks on a bad device.
            thumb = make_thumbnail(grab_preview_frame(cam["device_index"]))
            if thumb is not None:
                lbl = ttk.Label(row, image=thumb)
                lbl.image = thumb  # keep a ref so Tk doesn't GC the PhotoImage
                lbl.pack(side="right")
        status_var.set(f"找到 {len(cams)} 支攝影機")

    ttk.Button(frame_cam, text="掃描攝影機", command=do_scan).pack(anchor="e", pady=5)

    # --- Buttons ---
    frame_btn = ttk.Frame(root, padding=10)
    frame_btn.pack(fill="x")

    def on_start():
        server_url = url_var.get().strip()
        api_key = key_var.get().strip()
        if not server_url or not api_key:
            messagebox.showerror("錯誤", "請填入 Server URL 和 API Key")
            return
        selected = [{"device_index": c["device_index"],
                     "name": nv.get().strip() or f"Webcam {c['device_index']}",
                     "resolution": [640, 480], "jpeg_quality": 40, "target_fps": 8}
                    for c, v, nv in cam_vars if v.get()]
        if not selected:
            messagebox.showerror("錯誤", "請至少選擇一支攝影機")
            return
        # Validate connection
        try:
            resp = httpx.post(f"{server_url}/api/webcam/cameras",
                json={"cameras": selected},
                headers={"X-API-Key": api_key}, timeout=10.0)
            if resp.status_code == 401:
                messagebox.showerror("錯誤", "API Key 無效")
                return
            if resp.status_code != 201:
                messagebox.showerror("錯誤", f"伺服器回應: {resp.status_code}")
                return
            registered = resp.json()
            for i, cam in enumerate(selected):
                if i < len(registered):
                    cam["node_id"] = registered[i]["node_id"]
        except httpx.ConnectError:
            messagebox.showerror("錯誤", "無法連線到伺服器")
            return

        result["config"] = {
            "server_url": server_url,
            "api_key": api_key,
            "cameras": selected,
            "motion_threshold": 25,
            "heartbeat_interval": 30,
        }
        root.destroy()

    ttk.Button(frame_btn, text="開始", command=on_start).pack(side="right")
    ttk.Button(frame_btn, text="取消", command=root.destroy).pack(side="right", padx=5)

    # Auto-scan on open
    root.after(100, do_scan)
    root.mainloop()
    return result["config"]
```

- [ ] **Step 3: Create tray_app.py**

```python
# sdprs/webcam_client/gui/tray_app.py
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("webcam_client.gui.tray")

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


def _create_icon(color: str = "green") -> "Image.Image":
    img = Image.new("RGB", (64, 64), "transparent")
    draw = ImageDraw.Draw(img)
    c = (0, 200, 0) if color == "green" else (220, 50, 50)
    draw.ellipse([8, 8, 56, 56], fill=c)
    return img


class TrayApp:
    def __init__(self, on_open_settings: Callable, on_quit: Callable,
                 on_pause: Callable, on_resume: Callable):
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._icon: Optional["pystray.Icon"] = None
        self._paused = False

    def set_status(self, connected: bool) -> None:
        if self._icon and TRAY_AVAILABLE:
            color = "green" if connected else "red"
            self._icon.icon = _create_icon(color)

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("pystray not available, running without tray icon")
            return
        menu = pystray.Menu(
            pystray.MenuItem("開啟設定", lambda: self._on_open_settings()),
            pystray.MenuItem("暫停推送", lambda: self._toggle_pause()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("離開", lambda: self._quit()),
        )
        self._icon = pystray.Icon("SDPRS Webcam", _create_icon("green"), "SDPRS Webcam", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._on_pause()
        else:
            self._on_resume()

    def _quit(self) -> None:
        if self._icon:
            self._icon.stop()
        self._on_quit()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
```

- [ ] **Step 3b: Tests (the headless-testable core)**

```python
# sdprs/webcam_client/tests/test_gui_preview.py
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.preview import resize_keep_aspect


def test_resize_fits_within_bounds_and_keeps_aspect():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)  # 4:3 landscape
    out = resize_keep_aspect(frame, (160, 120))
    h, w = out.shape[:2]
    assert w <= 160 and h <= 120
    assert abs((w / h) - (640 / 480)) < 0.05  # aspect preserved


def test_resize_never_upscales():
    frame = np.zeros((60, 80, 3), dtype=np.uint8)  # already smaller
    out = resize_keep_aspect(frame, (160, 120))
    assert out.shape[:2] == (60, 80)


def test_resize_tall_frame_bounded():
    frame = np.zeros((640, 480, 3), dtype=np.uint8)  # portrait 3:4
    out = resize_keep_aspect(frame, (160, 120))
    h, w = out.shape[:2]
    assert w <= 160 and h <= 120
```

Run per-file (the GUI modules — setup_wizard/tray_app — are verified only by
`py_compile`/import; there is no display on the build box, so `run_setup_wizard()`
and `TrayApp.start()` are NOT invoked in tests):
```bash
cd sdprs
/c/Python314/python -m py_compile webcam_client/gui/preview.py webcam_client/gui/setup_wizard.py webcam_client/gui/tray_app.py
/c/Python314/python -m pytest webcam_client/tests/test_gui_preview.py -q -p no:cacheprovider
```
Expected: py_compile clean; all preview tests PASS.

- [ ] **Step 4: Commit**

```bash
cd sdprs
git add webcam_client/gui/ webcam_client/tests/test_gui_preview.py
git commit -m "feat(client): add setup wizard GUI (preview + naming) and system tray app"
```

---

### Task 11: Client — Main Entry + Wiring + Build Spec

**Files:**
- Create: `sdprs/webcam_client/main.py`
- Create: `sdprs/webcam_client/requirements.txt`
- Create: `sdprs/webcam_client/build.spec`

**Interfaces:**
- Consumes: All client modules (Tasks 7-10)
- Produces: Runnable entry point `python -m webcam_client.main`

- [ ] **Step 1: Create main.py**

```python
# sdprs/webcam_client/main.py
import logging
import signal
import sys
import threading
import time

from .config import load_config, save_config, is_first_run
from .push_engine import PushEngine
from .control_channel import ControlChannel
from .gui.setup_wizard import run_setup_wizard
from .gui.tray_app import TrayApp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("webcam_client.main")

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False


def main():
    global _running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    config = load_config()

    if is_first_run() or not config.get("server_url"):
        new_config = run_setup_wizard(config)
        if new_config is None:
            logger.info("Setup cancelled, exiting")
            return
        config = new_config
        save_config(config)

    server_url = config["server_url"]
    api_key = config["api_key"]
    cameras = [c for c in config.get("cameras", []) if c.get("enabled", True)]

    if not cameras:
        logger.error("No cameras configured")
        return

    # Start push engines
    engines = []
    for cam in cameras:
        cam["motion_threshold"] = config.get("motion_threshold", 25)
        engine = PushEngine(cam, server_url, api_key)
        engine.start()
        engines.append(engine)

    # Start control channel
    node_ids = [c["node_id"] for c in cameras if c.get("node_id")]

    def on_command(node_id: str, command: str, params: dict = None):
        for engine in engines:
            if engine._node_id == node_id:
                if command == "stream_start":
                    engine.set_streaming(True)
                elif command == "stream_stop":
                    engine.set_streaming(False)
                break

    control = ControlChannel(server_url, api_key, node_ids, on_command)
    control.start()

    # Tray app
    paused = threading.Event()

    tray = TrayApp(
        on_open_settings=lambda: _open_settings(config),
        on_quit=lambda: _shutdown(engines, control),
        on_pause=lambda: paused.set(),
        on_resume=lambda: paused.clear(),
    )
    tray.start()
    tray.set_status(True)

    logger.info(f"SDPRS Webcam Client running ({len(cameras)} cameras)")

    # Main loop — heartbeat
    heartbeat_interval = config.get("heartbeat_interval", 30)
    last_heartbeat = 0.0
    while _running:
        time.sleep(1)
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            last_heartbeat = now
            # Heartbeat is implicit via snapshot push; server detects offline at 90s

    _shutdown(engines, control)


def _open_settings(config):
    new_config = run_setup_wizard(config)
    if new_config:
        save_config(new_config)
        logger.info("Settings updated — restart required")


def _shutdown(engines, control):
    global _running
    _running = False
    control.stop()
    for engine in engines:
        engine.stop()
    for engine in engines:
        engine.join(timeout=5)
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create requirements.txt**

```
# sdprs/webcam_client/requirements.txt
opencv-python-headless>=4.8.0
numpy>=1.24.0
httpx>=0.25.0
pystray>=0.19.0
Pillow>=10.0.0
pyinstaller>=6.0.0
```

- [ ] **Step 3: Create build.spec**

```python
# sdprs/webcam_client/build.spec
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['cv2', 'numpy', 'httpx', 'pystray', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SDPRS_Webcam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
```

- [ ] **Step 4: Verify client starts (dry run without camera)**

Run: `cd sdprs && python -c "from webcam_client.config import load_config; print(load_config())"`
Expected: prints default config dict

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/main.py webcam_client/requirements.txt webcam_client/build.spec
git commit -m "feat(client): add main entry point, requirements, and PyInstaller build spec"
```

---

### Task 12: MERGED INTO TASK 5 — do not implement

**Status: retired 2026-07-21.** This task's entire content (webcam rows in
`GET /api/nodes`, plus the `node_type` -> `node.type` mapping in `api.jsx`) now lives in
**Task 5**, as Steps 0a and 0b.

**Why it moved.** Task 5 keys the Monitor Wall webcam tile off `node.type === 'webcam'`,
but nothing produced that value until this task, seven positions later. Task 5 would have
shipped as permanently-false dead code with no way to test the badge it exists to add, and
this task would have "enabled" a feature whose UI had already passed review unexercised.

Three defects in the original text of this task were corrected during the merge; they are
documented inline at Task 5 Step 0a so the reasoning survives:

1. It instructed `NodeStatus.node_type` be made `Optional[str] = None` "(add if missing)".
   The field already exists and is **required**; the change would have weakened the model
   for pump and glass nodes too.
2. Its `NodeStatus(...)` call passed `heartbeat=` and `upload=`, neither of which is a
   field on that model — those are names in the SPA's mapped node shape. It would have
   raised a pydantic validation error on the first webcam row.
3. Its `api.jsx` snippet assumed a `type` mapping it could amend. `mapNode` collapses every
   non-pump node to `'camera'`, so introducing `'webcam'` as a third type also silently
   removes webcams from the staleness and upload-age rules unless those are widened too.

**If you are executing the plan: skip this task.** Task numbering for Tasks 6-11 and 13 is
unchanged.

---

### Task 13: End-to-End Verification + Documentation

**Files:**
- Modify: `sdprs/README.md` (add webcam client section)

- [ ] **Step 1: Run full server test suite**

Run: `cd sdprs && python -m pytest central_server/tests/ -v`
Expected: All PASS (existing + new tests)

- [ ] **Step 2: Run client test suite**

Run: `cd sdprs && python -m pytest webcam_client/tests/ -v`
Expected: All PASS

- [ ] **Step 3: Manual E2E test (with real webcam)**

1. Start server: `cd sdprs/central_server && uvicorn main:app --reload`
2. Open Dashboard → Status → 新增 Webcam Client → copy API Key
3. Run client: `cd sdprs && python -m webcam_client.main`
4. Paste server URL + API Key → select camera → Start
5. Verify: Dashboard Monitor Wall shows 1Hz JPEG from webcam with "Webcam" badge
6. Click "▶ 即時" → verify HLS stream starts (may take 5-10s)
7. Click "● LIVE ✕" → verify returns to snapshot mode

- [ ] **Step 4: Add webcam client section to README**

Add to `sdprs/README.md`:

```markdown
## Webcam Client (Windows)

讓任一 Windows 電腦透過 USB Webcam 推送畫面到 Dashboard。

### 使用方式

1. Dashboard → 系統狀態 → 「新增 Webcam Client」→ 複製 API Key
2. 在目標電腦運行 `SDPRS_Webcam.exe`
3. 填入 Server URL + API Key → 選擇攝影機 → 開始
4. 程式最小化到 System Tray，自動推送 1Hz 快照
5. Dashboard 上點「即時觀看」可觸發 H.264 HLS 串流

### 開發

```bash
cd webcam_client
pip install -r requirements.txt
python -m webcam_client.main
```

### 打包

```bash
cd webcam_client
pyinstaller build.spec
# 產物: dist/SDPRS_Webcam.exe
```
```

- [ ] **Step 5: Final commit**

```bash
cd sdprs
git add README.md
git commit -m "docs: add webcam client usage and build instructions to README"
```
