# Webcam Client — Full Audit of Spec + Plan

**Date:** 2026-07-21
**Scope:** `specs/2026-07-21-webcam-client-design.md` (367 lines),
`plans/2026-07-21-webcam-client.md` (2636 lines), the approved brainstorming
transcript, and the shipped code for Tasks 1–3.
**Method:** every finding below was verified against real code, not inferred
from the documents.

## Summary

The design was approved in brainstorming, then **lost a load-bearing requirement
at the spec-writing step**, and the plan inherited the loss. The result is that
the feature's *default operating mode* — the 1Hz JPEG push that runs 99% of the
time — cannot authenticate, cannot update node liveness, and fails silently with
no diagnostic surface.

Three CRITICAL, four HIGH, eight MEDIUM, three LOW.

Tasks 1–3 are committed. Tasks 4–13 have not started, so all of this is still
cheap to correct.

---

## The break in the chain

The approved design said two things about auth (brainstorming §3, "節點資料模型變更"):

> 現有 `nodes` 表新增欄位：`node_type`, `api_key_hash`
> **現有 edge node 的 X-API-Key 驗證邏輯擴展為也查 webcam 節點**

The second sentence is the one that makes the whole feature work. It never
reached the spec. Spec §資料模型 (lines 213–217) kept only the first sentence.
The plan then never implemented it, and Task 1 additionally chose **two new
tables** (`webcam_clients`, `webcam_cameras`) instead of extending `nodes`.

Every CRITICAL finding below descends from that single dropped sentence.

---

## CRITICAL

### C1 — The 1Hz JPEG path 401s on every push, silently

**Chain:** spec line 37/58/95 and plan Task 8 both route normal-mode frames to
the existing `POST /api/edge/{node_id}/snapshot`.

- Plan line 1811–1813: the client's `httpx.Client` is built with
  `headers={"X-API-Key": self._api_key}` — the **per-client webcam key**.
- `central_server/api/snapshots.py:176`: `receive_snapshot` is gated by
  `Depends(verify_api_key)`.
- `central_server/auth.py:53–68`: `verify_api_key` compares against
  `settings.EDGE_API_KEY` — the **single global edge key**.
- `snapshots.py` contains **zero** references to webcam (`grep -c webcam` → 0).

Every snapshot push therefore returns **401**.

It fails *silently*. Plan line 1867–1870:

```python
self._client.post(url, content=jpeg.tobytes(),
                headers={"Content-Type": "image/jpeg"})
except Exception as e:
    logger.debug(f"Snapshot push failed: {e}")
```

httpx does not raise on 4xx without `raise_for_status()`. The `except` never
fires, nothing is logged even at debug level, and the tray icon stays green. An
operator sees a permanently blank tile and the client reports itself healthy.

**The only two ways out are both bad if chosen accidentally:** either implement
the dropped requirement (extend key verification to webcam clients), or hand
every field PC the global `EDGE_API_KEY` — which would let one compromised
reception-desk PC authenticate as *any* glass-break or pump node, and would make
the `revoke-key` endpoint from Task 2 pure theatre.

### C2 — `webcam_cameras.last_upload` has no writer anywhere

`last_upload` is read to derive status and staleness (Task 5 Step 0a, which I
authored from the plan). Nothing writes it:

- The snapshot path stamps `nodes.last_upload_at` (`database.py:799–812`), a
  different table and column.
- The HLS upload path touches only the in-memory `_last_activity` dict.

Consequence: every webcam renders **OFFLINE forever**, with `—` for upload age,
even once C1 is fixed.

### C3 — Two node registries that will collide

`database.py:811` auto-creates a `nodes` row on snapshot ingest, hardcoded
`node_type='glass'`:

```sql
INSERT INTO nodes (node_id, node_type, last_upload_at) VALUES (:id, 'glass', :ts)
ON CONFLICT (node_id) DO UPDATE SET last_upload_at = EXCLUDED.last_upload_at
```

So the moment C1 is fixed *without* addressing this, each webcam gains a second
identity: a `glass`-typed row in `nodes` **and** its row in `webcam_cameras`.
Task 5 merges both sources into `GET /api/nodes`, so the Monitor Wall would show
the same physical camera **twice** — once badged "Edge Cam", once "Webcam".

This is the direct cost of diverging from the approved "extend the `nodes` table"
design. It is the root cause, and C1/C2 are its symptoms.

---

## HIGH

### H1 — Viewer count never decrements except on an explicit button click

`increment_viewer` runs on `stream/start`; `decrement_viewer` runs only on
`stream/stop`, which the SPA calls only from the `● LIVE ✕` button (Task 5
Step 2). Closing the tab, navigating away, a browser crash, or a laptop lid
close all leave the count permanently ≥ 1.

The approved design explicitly wanted this: *"stream/stop **或 viewer 關閉時**
viewer_count -= 1"*. Neither spec nor plan carries it.

### H2 — The 5-minute auto-stop does not exist

Spec §按需串流流程 item 10 and §Dashboard 端 both promise a 5-minute forced stop.
Actual `cleanup_stale_streams`:

```python
timeout = settings.HLS_VIEWER_TIMEOUT_SECONDS   # 300 — assigned, never used
stale = [nid for nid, ts in _last_activity.items()
         if now - ts > 60 and _viewer_count.get(nid, 0) == 0]
```

Three defects: the 300s setting is dead; the threshold is hardcoded 60s; and it
only ever **deletes server-side directories** — it never enqueues `stream_stop`,
so the client keeps encoding H.264 and uploading regardless.

Combined with H1, a forgotten browser tab pins a field PC's CPU and uplink
**indefinitely**, on a system whose §頻寬估算 treats bandwidth as a first-class
constraint. *(The Task 3 implementer independently flagged the dead variable.)*

### H3 — Dynamic `webcam_XX` ids versus a static allowlist

`auth.py:94` `verify_node_id` enforces `settings.ALLOWED_NODE_IDS`, raising 403
when the list is non-empty and the id is absent. Webcam node_ids are **assigned
by the server at registration**, so they can never be pre-listed. The feature
works only on deployments that leave the allowlist empty — i.e. it breaks
precisely on the hardened ones. Neither document mentions this interaction.

### H4 — In-memory state breaks under more than one worker — **DOWNGRADED to LOW**

**Original finding:** `_viewer_count`, `_command_queues`, `_last_activity` are
module-level dicts and `asyncio.Queue`s. Under `uvicorn --workers N`, a command
enqueued by the worker handling `stream/start` is invisible to the worker
handling the client's long-poll, so commands are silently lost.

**Correction after checking the deployment.** The mechanism is real but the
premise is not: this server runs one worker by design, and that is already a
documented, accepted constraint.

- `deploy/Dockerfile:45` — `--workers 1`, explicit.
- `Dockerfile:25` (the Zeabur path per `zbpack.json`) — no `--workers` flag,
  which is uvicorn's single-worker default.
- `main.py:332–337` already records the constraint for existing in-memory state
  (the login throttle): *"acceptable for the single-node, single-worker uvicorn
  MVP. A multi-worker / multi-node deployment would need a shared store (e.g.
  Redis) instead."*

So the webcam state is consistent with an architectural decision the codebase
already made and documented — not a new unaddressed risk. I raised this as HIGH
before checking how the server actually runs, and that was overstated.

**Residual action (LOW):** the new webcam state should carry the same comment as
`main.py:332–337`, so that whoever eventually raises the worker count finds
streaming listed among the things that break. Right now the constraint is
documented in one place and silently depended upon in another.

---

## MEDIUM

| # | Finding |
|---|---------|
| M1 | **Endpoint drift.** Approved design and spec (§200, §280) both say `POST /api/nodes`. Task 2 shipped `POST /api/nodes/webcam`. Reviewed "clean" — nobody compared against the spec. |
| M2 | **Dropped approved requirement: camera preview.** Brainstorming §2 and spec line 139/352 require live preview thumbnails in the setup wizard (`gui/preview.py`). The plan mentions "preview" **zero times** — silently dropped. |
| M3 | **Dropped approved component: `WebcamTile`.** Spec §新元件 lists it; the plan has zero mentions and inlines the logic into `NodeCard`. Defensible, but undocumented. |
| M4 | **WS payload shape mismatch.** Spec §250 emits flat `{"type": ..., "node_id": ...}`; the plan nests `{"type": ..., "data": {"node_id": ...}}`. The plan's shape is correct for the SPA's unwrapping; the spec is wrong and should be corrected. |
| M5 | **No readiness check for stream start.** Spec §304 promises a 30s "未回應" timeout. Task 5 uses a blind `setTimeout(..., 3000)` then declares the stream live, with no failure path — so a dead client shows a black `<video>` forever. |
| M6 | **No stream-state resync after WS reconnect.** Spec §312 requires it; nothing implements it. |
| M7 | **`config_update` command dropped** from the control channel (approved design §Control Channel) without a note. |
| M8 | **API key stored in plaintext** in `%APPDATA%/SDPRSWebcam/config.json`. On a shared reception PC any logged-in user can read it. Worth at least a documented accepted-risk, or Windows DPAPI. |

---

## LOW

| # | Finding |
|---|---------|
| L1 | Task 13 Step 1 runs `pytest central_server/tests/` (whole directory) — hits the `[Cloud]` bracket parametrization trap and cannot succeed on this machine. Must be per-suite. |
| L2 | Task 13 uses bare `python`; this machine has only `/c/Python314/python`. |
| L3 | Live-mode viewing timer (approved design §4, "觀看計時") dropped without note. |

---

## What is NOT wrong

Worth stating, since an audit that only lists faults distorts the picture:

- The on-demand H.264/HLS choice over MJPEG/WebRTC is well-reasoned and the
  bandwidth arithmetic in the transcript holds up.
- HTTP long-poll over WebSocket for the control channel is the right call for
  NAT-bound clients.
- One-key-per-client (not per-camera) is a sound provisioning model.
- Tasks 1–3 are individually well-built and their tests genuinely pass
  (7/7, 6/6, 9/9 + 3/3, all verified by re-running, not taken on report).
- The seven plan defects corrected in commit `30c5380` are real fixes, unaffected
  by this audit.

The problem is not workmanship. It is that no step in the chain re-read the
previous artifact — each stage was reviewed against *itself*, never against the
document upstream of it.

---

## Recommendation

**Stop feature work and settle the auth/identity model first.** Tasks 4 and 6 are
safe to build (pure dashboard). Task 5 as currently written will actively produce
the C3 double-render once C1 is fixed, so it should not land until the model is
decided.

The decision to make is C3: extend the `nodes` table as originally approved and
retire `webcam_cameras`, or keep the split tables and add the missing writer plus
an explicit exclusion so snapshot ingest does not auto-create a `glass` row. The
first restores the approved design and deletes the merge logic in Task 5; the
second preserves the two shipped commits at the cost of permanent dual-registry
complexity.
