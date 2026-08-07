# Single-sensor (visual-only) fallback fusion — design

**Date:** 2026-08-07
**Track:** Track 4 of the 4-track backlog sweep ("single-sensor glass fusion — safety decision").
**Branch:** `feat/single-sensor-glass-fusion-2026-08-07` (worktree off `main` `2a97c3c`).
**Scope:** edge-only (`edge_glass`). No server/SPA code. No new dependencies.

## Problem

`edge_glass/detectors/trigger_engine.py::TriggerEngine.evaluate` fires an event **only when visual AND audio both trigger** within `correlation_window_seconds` (2s), followed by a 30s cooldown. The AND gate is deliberately conservative — a lone shadow or a lone loud noise will not fire; both together is a high-confidence glass break. That conservatism is a feature.

But it has a fatal corollary for a whole class of nodes: **when audio has no stream, the node can never alert.** `audio.enabled: false` is the *recommended* setting for Raspberry Pi nodes without a USB mic (PortAudio SEGVs — not a catchable Python exception — when `pa.open(input=True)` runs on hardware with no capture device), and the deployed Pi 5 fleet runs this way. In that configuration `audio_stream is None`, audio never triggers, so the AND check never passes. `edge_glass_main.py:120` states it outright: *"Running in visual-only mode (AND logic will not trigger)."* The main loop already **detects** this dead-end and logs *"node may be unable to alert"* every 30s (`edge_glass_main.py:629`) — but does nothing about it. Such a node is a camera that watches and never speaks.

## Decision (locked via brainstorming)

Five decisions frame the whole design:

1. **Intent — fallback, not always-on.** Fusion stays **AND** whenever both sensors are healthy. Single-sensor triggering is a *fallback*, active only when the other sensor is unavailable. (Rejected: always-on OR logic, which would raise fleet-wide false positives.)
2. **Direction — visual-only survivor (asymmetric).** Only the camera may trigger alone (when audio is unavailable). There is **no audio-only path**. Rationale: the camera is the system's evidence source (it records the MP4 clip); an audio-only alert while the camera is blinded/paused produces a near-worthless clip and fires on any loud noise.
3. **Activation — audio `disabled` only.** The fallback opens **only** when audio has no stream at all (`audio_stream is None` — config-off, PortAudio init failure, or absent device), determined **once at boot**. A merely `stale` (hung) mic does **not** open the window. This is persistent and intentional, exactly matching the Pi-without-mic driver, and it removes any per-frame flapping.
4. **False-positive control — dwell AND raised confidence (both).** When audio corroboration is gone, two independent gates replace it: temporal persistence (dwell) **and** an elevated confidence bar.
5. **Rollout — default OFF, opt-in per node.** Ships behind a config flag defaulting to `false`. Deployed audio-disabled nodes keep their current behavior until an operator consciously enables it (ideally after bench-tuning the multiplier). Matches the project's "ship dormant behind a flag" pattern; no surprise behavior change on upgrade.

## Key finding — the raised bar is expressible with no detector change

`VisualDetector._analyze_contours` computes `confidence = (current_edge_density / baseline_edge_density) - 1.0` — a *relative excess over baseline* — and sets `triggered = confidence > edge_density_threshold` (default 1.5), gated by a minimum contour length. `confidence` and `edge_density_threshold` are the **same unit**. `VisualResult` already carries the raw `confidence` value.

Therefore the elevated solo bar is simply:

```
solo_triggered  ⟺  confidence > edge_density_threshold × visual_only_confidence_multiplier
```

The `TriggerEngine` already receives `visual_result.confidence` every frame, so it can enforce the raised bar **itself** — the `VisualDetector` needs no change — and the bar stays **anchored to the already-tuned threshold** (a multiplier), not a fresh un-calibrated absolute number.

## Approach

### The fusion rule (`TriggerEngine`)

The engine gains a boot-time `audio_available: bool` (default `True`) plus the enable flag and the computed solo confidence threshold. Two regimes:

- **`audio_available` is True → today's code path, unchanged.** AND-only; the solo path does not exist. (This is the zero-regression guarantee.)
- **`audio_available` is False AND `visual_only_fallback` enabled →** a solo path runs *alongside* the AND check (which cannot fire without audio). A solo event fires when **all** hold:
  1. **Dwell:** the visual detector has been continuously triggered for ≥ `correlation_window_seconds` (the existing 2s constant is reused as the dwell — no new timing knob).
  2. **Elevated confidence:** the latest visual `confidence` ≥ `edge_density_threshold × visual_only_confidence_multiplier`.
  3. **Cooldown:** the same 30s gate that fusion events use.

When the flag is off, or audio is available, the solo path is inert.

### Dwell continuity semantics

The engine tracks the start-time of the current unbroken visual-trigger run and its latest confidence:

- **`visual_result.triggered is True`** → start a run if none is active (`_visual_run_start = current_time`); update the latest confidence.
- **`visual_result.triggered is False`** → **clear the run** (the crack signature disappeared; continuity is broken).
- **`visual_result is None`** (a detect-throttle gap — CV runs at `detect_fps` 5fps, below camera fps; also thermal-pause / brightness-anomaly frames) → **leave the run unchanged.** Throttle gaps between detection frames are expected and must not reset dwell.

On a solo fire: reset the run start and the visual/audio trigger timestamps (mirroring the existing post-event reset at `trigger_engine.py:151`) and set the cooldown.

**Exact fire condition (unambiguous).** A solo event fires on the current `evaluate` frame iff **all** of:
1. `audio_available` is False and `visual_only_fallback` is enabled;
2. `visual_result.triggered is True` on this frame (a fresh visual confirmation — the engine never fires solo on a `None`/throttle frame);
3. a run is active and `current_time - _visual_run_start >= correlation_window_seconds` (dwell elapsed);
4. `visual_result.confidence >= solo_confidence_threshold` on this frame; and
5. cooldown satisfied.

Note the split of roles: **dwell continuity requires only the normal trigger** (`triggered=True`, i.e. `confidence > edge_density_threshold`) sustained across the window; the **raised bar is checked at fire time against the current frame's confidence**, not required across the whole dwell. This is deliberately the A+B combination — a run must be sustained *and* the firing frame must be strong — and it is not equivalent to requiring the raised bar for the full window.

### Config surface

All new keys live under `trigger:` (documented in `config.yaml` and `config.zeabur.yaml`):

```yaml
trigger:
  correlation_window_seconds: 2          # (existing) also serves as the solo dwell duration
  cooldown_seconds: 30                   # (existing) shared by solo events
  visual_only_fallback: false            # NEW — opt-in per node; off by default
  visual_only_confidence_multiplier: 1.5 # NEW — solo bar = visual.edge_density_threshold × this
```

Two new keys; one anchored to the existing tuned threshold, none changing default behavior.

### Wiring (`edge_glass_main.py`)

Construct the `TriggerEngine` **after** the audio-init block (currently `audio_stream = start_audio_stream(...)` or `None`, `edge_glass_main.py:367-371`) so it can be told `audio_available = (audio_stream is not None)` — the exact signal `compute_audio_health` uses for `"disabled"`. The engine also receives `visual_only_fallback` (from `config["trigger"]`) and the computed solo threshold `config["visual"]["edge_density_threshold"] × config["trigger"]["visual_only_confidence_multiplier"]`. The main-loop `evaluate(...)` call site is otherwise unchanged.

### Event provenance

`Event` gains `trigger_source: str = "fusion"`; solo events set it to `"visual_only"`. The main-loop `event_metadata` dict carries `"trigger_source": event.trigger_source` (telemetry-only, backward-compatible JSON — the server already stores the metadata blob as-is). A solo event still records a real MP4 (the camera works), so evidence integrity is preserved. `force_trigger` (simulation) keeps `trigger_source="fusion"` (or an explicit sim marker via the existing `is_simulation`), unchanged.

## Safety invariants

- **Fail-closed:** any uncertainty yields no trigger — consistent with all existing `None` handling.
- **Disabled-only, boot-fixed:** a mic that goes `stale` later never opens the window; only a genuinely audio-less node qualifies, decided once at boot. No per-frame flapping.
- **Regression-proof:** with audio present, or the flag off, the engine is behaviorally identical to today. The existing edge_glass trigger suite must stay green **unchanged** — that is the proof.
- **Shared cooldown:** solo events obey the same 30s cooldown, so a sustained visual anomaly cannot flood alerts.

## Testing (no hardware; strict TDD)

New cases in `edge_glass/tests/test_trigger_engine.py`, each proven RED→GREEN (controller reverts only the production file, confirms the new test fails, restores, confirms green):

- **AND unchanged:** `audio_available=True` → a sustained, high-confidence visual stream never produces a solo event.
- **Flag gate:** `audio_available=False` + `visual_only_fallback` off → no solo event.
- **Dwell gate:** flag on + audio absent + visual triggered but for < dwell → no event.
- **Confidence gate:** flag on + audio absent + visual sustained ≥ dwell but confidence below the raised bar → no event.
- **Solo fire:** flag on + audio absent + sustained ≥ dwell + confidence ≥ raised bar → event fires with `trigger_source == "visual_only"`.
- **Continuity:** a `triggered=False` result mid-run resets the dwell; interleaved `None` (throttle) frames do **not** reset it.
- **Cooldown:** a second qualifying solo event within 30s is suppressed.
- **Regression:** the full existing `test_trigger_engine.py` (and the edge_glass suite) stays green unchanged.

Time is injected via the existing `current_time` parameter of `evaluate` (already used by the suite) — no real clocks, no sleeps.

## Non-goals / deferred

- **No audio-only path** (asymmetric by decision 2).
- **No stale-audio activation** — `disabled` only (decision 3).
- **No server/SPA display** of the single-sensor flag — the `trigger_source` is captured in telemetry metadata only; surfacing it in the dashboard is a possible follow-up, out of scope here.
- **Bench calibration of `visual_only_confidence_multiplier`** — the flag stays off until an operator tunes the multiplier against a real visual-only node. The 1.5 default is a conservative starting point, not a validated value.
- **No change** to AND-mode thresholds, the correlation window's fusion role, or the cooldown.

## Invariants / discipline

- Strict TDD: each new behavior fails RED before its code lands, passes GREEN after; controller A/B-verifies (never trusts subagent self-reports).
- No new production or test dependencies; no hardware required (mocked/`current_time`-injected tests only).
- Banned strings (`Msc@***` / `MSC-***` / the EMQX public broker) must never appear in any diff.
- Work stays in the worktree; nothing reaches `origin/main` without the user's literal "approved".

## Follow-up (post-merge, out of scope)

- Bench-tune `visual_only_confidence_multiplier` on a real audio-disabled node during a controlled break test; enable the flag per node once tuned.
- Optionally surface `trigger_source == "visual_only"` in the server `/api/nodes` / SPA so operators can visually distinguish degraded-mode alerts from full-confidence ones.
