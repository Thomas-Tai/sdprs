# Design Proposal — Non-Blocking Glass Event Capture (MP4 encode + buffer)

**Status:** Draft for review · **Author:** codebase-audit workstream · **Date:** 2026-07-13
**Scope:** `edge_glass/edge_glass_main.py`, `edge_glass/buffer/circular_buffer.py` (read-only), a new capture/encode module, `edge_glass/config.yaml`
**Relates to:** Theme-3 (edge offline-autonomy) / Theme-6 (detection correctness) — the last *deferred* item in `docs/superpowers/PROGRESS.md`. Deferred deliberately because it is a single-file redesign coupled to buffer sizing and camera-thread-safety, and warrants hardware validation — **not** an unvalidatable parallel-agent edit.

> This is a proposal. **No code is written yet.** It ends with open questions (§10) that are yours to decide before implementation.

---

## 1. Problem

When a glass-break event fires, the main capture loop executes steps 7–11 **synchronously, in-line**:

```
7.  frozen = buffer.freeze()                       # cheap (ref snapshot)
8.  post   = record_post_trigger(camera, 5s, fps)  # BLOCKS ~5s, reads the camera
9.  all    = frozen + post
10. mp4     = encode_mp4(all, ...)                  # BLOCKS on ffmpeg subprocess (seconds)
11. event_queue.enqueue(mp4, metadata)             # cheap
```

For the entire step-8 + step-10 window (**~5s of forced recording plus multi-second ffmpeg encode of 150–225 × 720p frames**), the main loop does **not**:

- `buffer.append()` new frames → **the circular buffer goes stale / develops a gap**;
- run visual detection or audio/visual fusion → **a second glass-break during the encode window is silently missed** (worse than cooldown, which is an intentional suppression — this is an *unintentional blind spot*);
- push the 1 fps snapshot → **the dashboard image freezes** for the whole window;
- report detector health.

There is also a latent hazard: `record_post_trigger()` calls `camera.read()` on the **same** `cv2.VideoCapture` the main loop reads. Today they never overlap (step 8 blocks the loop), but that coupling is why the fix can't be a naïve "move step 8 to a thread" — two threads reading one `VideoCapture` is a data race.

**Root cause:** post-trigger frames are obtained by a *second, blocking* camera read, and encoding runs on the capture thread.

---

## 2. Goals / non-goals

**Goals**
- The main capture loop **never blocks** on post-trigger recording or MP4 encoding.
- A second event occurring seconds after the first is **still detected** (subject only to the intentional `cooldown`).
- **No concurrent `VideoCapture` access** — the camera is read from exactly one thread (the main loop), forever.
- The "buffer arithmetic" is made **explicit and asserted**, not implicit.
- Fully **desktop-testable** (fake camera + fake clock + fake encoder), with a defined hardware-validation gate before production enable.

**Non-goals**
- Changing the detection algorithms, fusion, or cooldown semantics.
- Changing the upload pipeline (`event_queue` / `UploadWorker`) — the encode worker still calls `event_queue.enqueue()` exactly as today.
- Hardware-accelerated encode tuning (separate concern; `detect_encoder()` already prefers `h264_v4l2m2m`).

---

## 3. Key idea — post-frames come from the buffer, not a second camera read

The main loop **already** appends every captured frame to the circular buffer. So instead of blocking to record post-trigger frames, we simply **let the loop keep running** and **freeze the buffer once `post_roll` seconds have elapsed** after the trigger. Encoding then runs on a **worker thread** over the frozen frames (pure CPU/pipe work — no camera).

```
Trigger at T:
  - record a PendingEvent(trigger_ts=T, metadata), set cooldown. Return to the loop. (non-blocking)
Loop continues normally, appending frames to the buffer.
When now >= T + post_roll:
  - frames = slice(buffer.freeze(), from=T - pre_roll, to=T + post_roll)
  - encode_queue.put((frames, node_id, T, metadata))     # hand off, non-blocking
Encode worker thread:
  - mp4 = encode_mp4(frames, ...)   # blocks THIS thread, not the loop
  - event_queue.enqueue(mp4, metadata)
```

This **eliminates** `record_post_trigger` and its second camera read entirely — resolving the thread-safety hazard as a side effect.

---

## 4. The buffer arithmetic (making the implicit explicit)

`CircularBuffer` holds `maxlen = fps × duration_seconds` frames (default `15 × 10 = 150`, ≈ 396 MB at 720p BGR). `freeze()` returns whatever is currently in the deque.

For the window `[T − pre_roll, T + post_roll]` to be **fully present** at the freeze moment (`≈ T + post_roll`), the buffer must span at least `pre_roll + post_roll` seconds, **plus margin** for loop jitter / thermal fps changes:

> **Invariant:** `duration_seconds ≥ pre_roll + post_roll + margin`  (recommend `margin ≥ 1s`)

This is the crux of the previously-"unconfirmed" arithmetic. Today's code sidesteps it by using the *whole* buffer as pre-roll (≤10s) and *separately recording* post-roll — so pre-roll is effectively "up to 10s" and the clip is up to 15s. The buffer-only approach makes the clip **exactly** `pre_roll + post_roll`, so those become explicit config values bounded by the invariant.

**Trade-off to decide (see §10):**

| Option | buffer | pre | post | clip | frames@15fps | ~RAM | Note |
|---|---|---|---|---|---|---|---|
| **A (recommended)** | 10s | 4s | 5s | 9s | 135 | ~356 MB | Fits today's budget; 1s margin. |
| B | 12s | 6s | 5s | 11s | 180 | ~475 MB | Longer pre-roll; +80 MB. |
| C | 15s | 9s | 5s | 14s | 225 | ~594 MB | Preserves ~today's clip length; +200 MB. |

The edge node is a **Pi 5 (8 GB)** (per deployment notes), so even Option C fits comfortably; the historical "~415 MB / 10s" budget was set against a 4 GB target. Recommendation: **Option A** — it keeps the documented memory budget, and 4s pre-roll is ample context for a glass strike. Longer pre-roll is a one-line config bump if you disagree.

Startup should **validate the invariant** and fail-fast (or clamp + WARN) if violated, so a mis-config can't silently truncate clips.

---

## 5. Proposed structure (new module + main-loop edits)

New file `edge_glass/capture/event_capture.py` (or `utils/`), with two small, independently-testable pieces:

**5a. `PendingEventTracker`** — a pure, clock-injected state machine (no I/O):
- `add(trigger_ts, metadata)` — register a pending event.
- `due(now) -> list[PendingEvent]` — return + remove events whose `trigger_ts + post_roll <= now`.
- Trivial to unit-test with a fake `now`.

**5b. `slice_window(frozen, t_start, t_end) -> list[frame]`** — pure function selecting frames whose timestamp ∈ `[t_start, t_end]`. Unit-testable with synthetic `(ts, frame)` tuples. (Handles the low-fps case correctly because it slices by **timestamp**, not frame count — and under thermal throttling the fixed-`maxlen` buffer spans *more* wall-time, never less.)

**5c. `EncodeWorker(threading.Thread)`** — drains a **bounded** `queue.Queue(maxsize=N)`:
- On item: `mp4 = encode_fn(frames, ...)` then `event_queue.enqueue(...)`. `encode_fn` is injected (real `encode_mp4` in prod, fake in tests).
- Wraps encode in try/except → logs + drops on failure; **never lets the thread die**.
- `stop(drain=True, timeout=...)` for graceful shutdown so in-flight events aren't lost.
- **Backpressure:** if the queue is full (encode falling behind), **drop the new event with a loud `WARNING`** rather than block the producer or grow memory unbounded. With `cooldown=30s` and encodes of a few seconds, the queue effectively never exceeds depth 1 — but the bound is a correctness backstop and its drops must be logged (no silent loss).

**Main-loop edits (`edge_glass_main.py`):**
- Delete `record_post_trigger()` and steps 8–10's inline body.
- On trigger: `tracker.add(T, metadata)`; set cooldown; **continue the loop**.
- Each iteration, after `buffer.append(...)`: for each `ev in tracker.due(now)`, `frames = slice_window(buffer.freeze(), ev.T - pre, ev.T + post)`; `encode_worker.submit(frames, node_id, ev.T, ev.metadata)`.
- Start `EncodeWorker` alongside the other threads (it becomes "Thread 7"); drain it in the shutdown sequence.
- Preserve the `--simulate` / MQTT `simulate_trigger` path (a simulated trigger just calls `tracker.add(now, ...)`; the cooldown-bypass flag rides in the metadata as today).

**Memory note:** each *pending/queued* event pins `pre+post` seconds of frames (frozen refs survive deque rollover). At `cooldown=30s` and queue bound `N=2`, worst-case pinned ≈ `N × clip` ≈ 2 × ~356 MB. Fine on 8 GB; the bound guarantees it can't grow.

---

## 6. Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| Encode falls behind under a burst | Bounded queue drops new events with WARNING (no memory blow-up, no silent loss). Cooldown makes this near-impossible in practice. |
| ffmpeg fails / not installed | Worker try/except → log + drop that event; thread survives; loop unaffected. |
| Loop jitter/thermal throttle delays the freeze | `margin` in the invariant absorbs it; slicing is by timestamp; low fps = *more* wall-time coverage. |
| Process killed mid-encode | Same exposure as today (in-flight clip lost). `stop(drain=True)` covers graceful shutdown (SIGTERM/systemd). |
| Two events within `post_roll` | Each is an independent PendingEvent; both encode when due. (Cooldown usually suppresses the second — unchanged semantics.) |

---

## 7. Test plan (desktop — no hardware)

- `PendingEventTracker`: add + `due(now)` boundaries (not-yet-due, exactly-due, past-due, multiple).
- `slice_window`: exact window, empty window, low-fps (sparse timestamps), out-of-range trims.
- Invariant check: raises/clamps+WARNs when `pre+post+margin > duration`.
- `EncodeWorker`: injected fake `encode_fn` → asserts `event_queue.enqueue` called with the right args; encode-raises → event dropped + logged + worker still alive; `stop(drain=True)` finishes queued items.
- Integration (fake camera + fake clock): trigger → loop keeps appending → worker receives a correctly-sliced clip; **assert the loop was never blocked** (advance the fake clock and confirm N appends happened across the post_roll window).

These all fit the existing `edge_glass/tests` bare-import convention and run under `cd edge_glass && python -m pytest tests -q`.

## 8. Hardware-validation gate (before production enable)

On a real Pi 5 + camera: (1) confirm clip window/length is correct and playable; (2) measure loop cadence during an event — appends must continue at ~fps throughout; (3) fire two events ~2–3s apart and confirm the second is detected; (4) confirm dashboard snapshot does **not** freeze during encode; (5) soak for memory stability across many events.

---

## 9. Rollout

Gate behind `capture.async_encode: true` (default **false** initially) so the redesign ships dormant, is enabled on the bench node first, validated per §8, then flipped on for the field node. Once proven, remove the flag and the legacy path.

---

## 10. Open questions for you

1. **Clip window** — Option **A** (buffer 10s / pre 4 / post 5), B, or C from §4? (Recommend A.)
2. **Backpressure policy** — drop-newest-with-WARNING (recommended) vs. drop-oldest vs. block-briefly?
3. **Invariant violation at startup** — fail-fast (refuse to start) vs. clamp-and-WARN? (Recommend clamp-and-WARN so a bad config degrades rather than bricks the node.)
4. **Rollout** — ship behind the `async_encode` flag (recommended) or replace outright?
5. **Encode-failure retention** — on ffmpeg failure, drop silently-after-log (today's effective behavior) or persist the raw frames for a retry? (Recommend drop-after-log; retry adds disk + complexity for a rare case.)

Once you answer §10, this becomes a concrete, file-disjoint implementation task (new module + main-loop edits + config + tests) that I can execute and desktop-verify, leaving only the §8 hardware gate before field enable.
