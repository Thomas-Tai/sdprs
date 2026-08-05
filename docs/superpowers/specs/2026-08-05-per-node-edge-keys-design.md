# Per-Node Edge API Keys — Design Spec

**Status:** DESIGN (awaiting approval to plan)
**Date:** 2026-08-05
**Track:** 1 of 4 (software backlog sweep)
**Branch/worktree:** `feat/per-node-edge-keys-2026-08-05` @ `sdprs-track1-wt` (off `main` `b5961c5`)

## Problem

Every edge-glass node authenticates to the central server with ONE shared
secret, `EDGE_API_KEY` (`auth.verify_api_key` / `verify_api_key_or_session`,
a constant-time compare against `settings.EDGE_API_KEY`). Consequences:

1. **No node identity.** Any holder of the shared key can POST telemetry,
   alerts, or snapshots under *any* `node_id` (the `verify_node_id` allowlist
   only checks the id is *in a set*, not that the caller *is* that node). A
   single leaked key impersonates the whole fleet.
2. **No per-node revocation.** Rotating the key re-keys every node at once.

The webcam-client subsystem already solved this: `webcam_clients` stores a
per-client `api_key_hash` (SHA-256, never plaintext) bound to a `node_id`,
looked up by `get_webcam_client_by_key` in `auth.verify_webcam_api_key`,
dual-backend (SQLite + PostgreSQL), indexed. **This spec extends that proven
pattern to edge-glass nodes.**

## Approach (user-approved 2026-08-05)

Grace-period **dual-accept**: a per-node key is checked first; the shared
`EDGE_API_KEY` is still accepted as a fallback *while it remains set*. Nodes
migrate one at a time; the shared key is retired later by unsetting
`EDGE_API_KEY`. Zero-downtime, safe for the hardware-gated fleet.

```
verify_api_key(request, key):
    rec = get_edge_node_by_key(key)          # per-node path
    if rec:
        request.state.edge_auth_node = rec["node_id"]   # bound identity
        return key
    if EDGE_API_KEY and ct_equal(key, EDGE_API_KEY):     # legacy fallback
        request.state.edge_auth_node = None              # unbound / any
        return key
    raise 401
```

Binding is then enforced at the two write ingest paths (which already call
`verify_node_id`): if `request.state.edge_auth_node` is a specific node and it
does not equal the claimed `node_id`, return **403**. A `None` (legacy shared
key) skips the binding check — that is the grace period.

## Storage

Add to the existing `nodes` table (edge nodes already live there):

- `api_key_hash TEXT` — SHA-256 hex of the node's key, or NULL (unprovisioned
  → relies on the shared-key fallback). Added via the same idempotent
  `PRAGMA table_info` / `ALTER TABLE` migration block already used for
  `location`, `battery_voltage`, etc. Mirrored in `_create_tables_postgresql`.
- Index `idx_nodes_api_key_hash ON nodes(api_key_hash)` (mirrors
  `idx_webcam_clients_api_key_hash`) so key lookup is not a full scan.

No new table: the webcam subsystem needed one because a client owns many
cameras; an edge node *is* the unit, so a column on `nodes` is the minimal fit.

## DB helpers (dual-backend, mirror the webcam ones verbatim in shape)

- `provision_edge_node_key(node_id, node_type="glass") -> {api_key}` — generate
  `sk-edge-<token_urlsafe(32)>`, store its SHA-256 on the node row (UPSERT: create
  the `nodes` row if absent, else set `api_key_hash`). Returns the raw key ONCE
  (never stored, never logged, never returned again). Mirrors
  `create_webcam_client`.
- `get_edge_node_by_key(api_key) -> {node_id, status} | None` — SHA-256 lookup.
  Mirrors `get_webcam_client_by_key`. Must return None for empty/NULL hash so a
  node with no key set is never matched by an empty key.
- `rotate_edge_node_key(node_id) -> {api_key}` — new key + hash on the row.
  Mirrors `revoke_webcam_key`.
- `clear_edge_node_key(node_id) -> bool` — set `api_key_hash = NULL` (de-provision;
  the node falls back to the shared key). NOT a row delete — the node registry row
  and its telemetry stay.

## Provisioning API (session-protected; mirror webcam admin surface)

New endpoints under the existing edge/nodes admin router, all
`Depends(get_current_user)` (dashboard session only — never the edge key):

- `POST /api/nodes/{node_id}/key` → `{api_key}` (201) — provision or rotate;
  raw key shown ONCE in the response body. `verify_node_id(node_id)` first.
- `DELETE /api/nodes/{node_id}/key` → 204 — clear the key (de-provision).

Listing existing nodes already exists; the node list SHOULD expose a boolean
`has_key` (derived: `api_key_hash IS NOT NULL`) so the dashboard can show which
nodes are migrated. Never expose the hash itself.

## Auth changes (`central_server/auth.py`)

- `verify_api_key(request, api_key)` — add the `request` param, do the per-node
  lookup first, stamp `request.state.edge_auth_node`, fall back to the shared key.
  Keep returning the key string (callers unchanged). The invalid-key log keeps the
  existing SHA-256-digest form (never log key material).
- `verify_api_key_or_session` — same per-node-first, shared-fallback, then session.
  Read paths need not enforce binding; they must still accept a valid per-node key.
- New `verify_node_binding(request, claimed_node_id)` helper (or inline in the two
  write handlers): 403 when `request.state.edge_auth_node` is set and differs from
  `claimed_node_id`; no-op when it is None (grace period) — called AFTER the
  existing `verify_node_id(claimed_node_id)`.

## Write-path binding enforcement

- `POST /alerts` (`alert.node_id` in body) — after `verify_node_id(alert.node_id)`,
  call `verify_node_binding(request, alert.node_id)`.
- `POST /edge/{node_id}/snapshot` (path `node_id`) — after `verify_node_id(node_id)`,
  call `verify_node_binding(request, node_id)`. The existing snapshot 401→
  `_SNAPSHOT_401_HEADERS` wrapper must also wrap the 403 consistently (bare "—"
  tile behaviour is unaffected).

## Edge / webcam clients

**No edge code change.** `edge_glass` already sends `X-API-Key:
<config server.api_key>`; migrating a node is an ops step (put its `sk-edge-…`
key in `config.yaml`). Webcam auth (`verify_webcam_api_key`) is untouched — a
separate table, separate scheme.

## Config / docs

- `.env.example` + `central_server/.env.example`: annotate that `EDGE_API_KEY`
  is now the *legacy fallback* — optional once all nodes are provisioned; unset it
  to enforce per-node-only.
- `docs/reference/configuration.md` + `docs/deployment/edge-glass.md`: document
  provisioning (`POST /api/nodes/{id}/key`), putting the key in `config.yaml`, and
  retiring the shared key.

## Global constraints (bind every task)

- Dual-backend: EVERY new DB helper has a `get_backend() == "postgresql"` branch
  AND a SQLite branch, exactly like the webcam helpers. Migration mirrored in both
  `_create_tables_sqlite`-path and `_create_tables_postgresql`.
- Keys: raw key = `sk-edge-<secrets.token_urlsafe(32)>`; store only
  `hashlib.sha256(key.encode()).hexdigest()`. NEVER log/return/store the raw key
  beyond the one provisioning response.
- Constant-time compare for the shared-key fallback (`_ct_equal`). Per-node lookup
  is by hash equality in SQL (hash of a high-entropy 32-byte token — not
  timing-sensitive the way a low-entropy secret is).
- Datetime: naive-UTC via `central_server.timeutil.utcnow()` only.
- Banned strings must never appear in any diff: `Msc@2333`, `MSC-Person`,
  `broker.emqx.io`. No hardcoded credentials.
- Strict TDD (RED→GREEN) each task. Existing shared-key tests (`test_webcam_auth`,
  `test_node_allowlist`, `test_alerts_api`, `test_snapshot_api`, etc.) MUST stay
  green — the shared key still works during the grace period.
- zh-TW for any user-facing strings (provisioning UI copy, if added).
- No merge/push to origin/main without the user typing literal "approved".

## Dashboard key panel (IN SCOPE — user decision 2026-08-05)

A SPA panel (zh-TW) on the node/status view to manage a node's key:

- Shows each node's `has_key` state (未設定 / 已設定).
- **Provision / rotate** button → calls `POST /api/nodes/{id}/key`, then reveals
  the returned raw key ONCE in a copy-able modal with a clear "此金鑰只顯示一次"
  warning (mirrors any existing webcam-key reveal UX; the key is never re-fetchable).
- **Clear** button → `DELETE /api/nodes/{id}/key`, returns the node to shared-key
  fallback, with a confirm.
- All SPA rules hold: no build step / in-browser Babel, no new deps, no
  `import`/`require`, driven by `window.SDPRS_API`, covered by a
  `tools/spa/render_extra/*.js` render test.

## Out of scope (explicit)

- Auto-rotation / expiry schedules.
- Per-node keys for MQTT (this covers the REST/HTTP surface; MQTT auth is separate).
- Retiring `EDGE_API_KEY` in code — it stays as the fallback; retirement is an ops
  action (unset the env var).

## Acceptance

1. A provisioned node authenticates with its own key and can POST only under its
   own `node_id` (cross-node POST → 403).
2. An unprovisioned node still authenticates with the shared key (grace period)
   and can POST under any allowlisted `node_id` (unchanged behaviour).
3. Unsetting `EDGE_API_KEY` leaves only per-node auth working; the shared key is
   rejected.
4. Keys are only ever stored hashed; the raw key appears once (provisioning
   response) and nowhere in logs.
5. Both DB backends pass the full suite.
