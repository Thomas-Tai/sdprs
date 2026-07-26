# Design Proposal — Edge Glass Node Efficiency + Thermal Reduction

**Status:** Approved for spec review · **Author:** edge-audit workstream · **Date:** 2026-07-26
**Scope:** `edge_glass/detectors/visual_detector.py`, `edge_glass/edge_glass_main.py` (constructor wiring), `edge_glass/utils/config_loader.py` (DEFAULTS), `edge_glass/config.zeabur.yaml` + `edge_glass/config.yaml` (docs/propagation), `edge_glass/tests/test_visual_detector.py`
**Relates to:** `project_edge_glass_perf_2026_07_25` (the 2026-07-25 CV-throttle/async-encode/heartbeat pass shipped `detect_fps=5`; this is "scope B" — downscale + stabilizer optimizations, previously deferred).

> Scope confirmed with the user as **Approach B** (structural pure-wins + reversible config knobs with heat-reducing defaults). This document is the reviewable spec that precedes the implementation plan.

---

## 1. Problem

A glass node's sustained CPU heat is dominated by the CV pipeline in `visual_detector.analyze()`, run at `detect_fps=5`. Three things make each detection frame more expensive than it needs to be:

1. **Full-720p everywhere.** Grayscale, ORB, `warpAffine`, `absdiff`, ROI mask, Canny, morphology, `findContours`, and the baseline `np.mean` all run on 1280×720 = 921,600 px. Glass-*break* detection does not need 720p spatial fidelity.
2. **ORB computed twice per frame.** `_stabilize()` runs `detectAndCompute` on **both** `self._prev_gray` and the current frame every detection frame — but `prev_gray`'s descriptors were already computed one iteration earlier. ORB + brute-force matching is the single most expensive step in the pipeline, and half of it is recomputation.
3. **Expensive step precedes the cheap gate.** `analyze()` runs stabilization (ORB) **before** the cheap `np.mean` brightness/anomaly check, so anomalous frames (night transition, lightning, light switch) pay the full ORB cost and are then discarded.

There is also a latent bug: the ROI mask is built at a **hardcoded** `1280×720` (`visual_detector.py:97`) regardless of the actual frame shape.

**Root cause:** the detection path was written for maximum spatial fidelity with no separation between "what the detector needs to see" and "what we record/stream," and the stabilizer re-derives per-frame features it already had.

---

## 2. Goals / non-goals

**Goals**
- Roughly **2–3× less continuous CV work** on the detection path → lower sustained CPU temperature for 24/7 operation and hardware longevity.
- **Zero fidelity loss** to the recording buffer, the incident MP4, or the dashboard snapshot — those keep full-resolution frames.
- Every fidelity lever is a **per-node config knob, reversible without a redeploy**.
- Validated **without physical access**: the change is proven by the detector test suite (detection still fires at half-res) and confirmed on the live node's dashboard **CPU-temp tile** (temp telemetry already flows via heartbeat).

**Non-goals (explicitly out of scope)**
- **The trigger AND-logic correctness issue** (see §8) — a separate product decision, not touched here.
- ORB `nfeatures` reduction, baseline-recompute trimming, disabling stabilization by default (that was Approach C).
- Any change to the recording buffer, async-encode path, snapshot pipeline, MQTT/heartbeat, or upload queue.
- Changing detection *algorithms* — same pipeline stages, same thresholds, just cheaper inputs and no recomputation.

---

## 3. Key ideas

### 3a. The detector downscales its own working copy (the caller does not)
`analyze(frame)` receives the full-resolution frame exactly as today. Internally, when `detect_scale < 1.0`, it makes a downscaled working copy (`cv2.resize(..., INTER_AREA)`) and runs the entire pipeline on that copy. The caller (`edge_glass_main.py`) is unchanged in how it feeds the buffer and snapshot — those still get the full-res `frame`. Detection fidelity drops (coarser contours); **evidence and dashboard fidelity do not.**

Pixel-count math: at `detect_scale=0.5` the working frame is 640×360 = 230,400 px — **¼** the pixels, so every pixel-bound stage (grayscale, ORB, warp, absdiff, ROI, Canny, morphology, contours, baseline mean) drops ~4×. The baseline deque (`detect_fps × baseline_window_seconds` grayscale frames) also shrinks ~4× in RAM.

### 3b. ORB features are computed once per frame and cached
`_stabilize()` computes `detectAndCompute` **only on the current frame**, and matches it against the **previous frame's cached** `(keypoints, descriptors)`. After matching, the current frame's descriptors become the cache for next iteration. This aligns each frame to the previous **raw** frame (rather than the previous *aligned* frame), which is the standard frame-to-frame formulation and is what makes caching sound. Net: the dominant operation runs once instead of twice.

### 3c. The cheap gate runs first
`analyze()` runs the `np.mean` brightness/anomaly check **before** stabilization. Because mean brightness is invariant to affine alignment, this is behaviorally equivalent for normal frames, and anomalous frames now return `None` **before** paying any ORB cost.

---

## 4. Component changes

All detector changes preserve current behavior when constructed with a bare config (class defaults `detect_scale=1.0`, `stabilize=True`), so existing tests and existing node behavior are untouched until the *config* opts in.

### 4a. `detectors/visual_detector.py`
- **Constructor** gains `detect_scale: float = 1.0` and `stabilize: bool = True`, both read by the caller from config. `detect_scale` clamped to `(0.0, 1.0]`.
- **ROI mask built lazily** at the working resolution on the first `analyze()` (fixes the hardcoded `1280×720`): the polygon points are scaled by `detect_scale`, mask shape = working-frame shape.
- **`min_contour_length_px`** is compared at `× detect_scale` (a fixed physical length spans fewer pixels at lower resolution).
- **`_stabilize()`**: single `detectAndCompute` per call; reads `self._prev_kp/_prev_des`, matches, estimates affine, warps; then caches current `(kp, des)`. `self._prev_des is None` is the first-frame/"no previous" sentinel (replaces the `_prev_gray` read). When `stabilize=False`, `_stabilize()` is bypassed entirely.
- **`analyze()` reordered**: grayscale → **anomaly check** → (early-return `None` if anomalous) → stabilize → baseline → diff → ROI → Canny → morphology → contours. Downscale happens at the top when `detect_scale < 1.0`.
- Canny thresholds unchanged (gradient-based, resolution-tolerant). ORB `nfeatures` unchanged.

### 4b. `edge_glass_main.py`
- Read `detect_scale` and `stabilize` from `config["visual"]` and pass them into the `VisualDetector(...)` constructor. No other loop changes. Buffer, snapshot, and thermal wiring untouched.

### 4c. `utils/config_loader.py` (DEFAULTS)
- `visual.detect_scale: 0.5`, `visual.stabilize: true`.
- `camera.fps: 15 → 12`.
- These reach nodes via the existing `_deep_merge(DEFAULTS, node_yaml)` path (node YAML does not set `detect_scale`/`stabilize`, so they inherit).

### 4d. `config.zeabur.yaml` + `config.yaml` (template/docs)
- Remove the explicit `camera.fps: 15` from the templates so fresh deployments inherit the `12` default. Document the two new `visual.*` knobs inline.

---

## 5. Config surface (final)

```yaml
camera:
  fps: 12          # was 15; capture/ISP/buffer churn -20%, imperceptible for incident replay
visual:
  detect_scale: 0.5   # run detection on a half-res working copy (¼ the pixels). 1.0 = full-res.
  stabilize: true     # ORB affine stabilization. false = skip entirely (only safe on rigid mounts).
  # detect_fps: 5     # (existing) CV cadence, unchanged by this work
```

**Deployment propagation caveat (must be in rollout notes):** `detect_scale`, `stabilize`, and all pure-code wins deploy to nodes on the next rsync automatically. The **already-deployed** node's local `config.zeabur.yaml` still pins `camera.fps: 15`; the 12-fps change only reaches it if the operator trims that one line during the same console session. **The CV heat wins (the dominant effect) land regardless of the fps line.**

---

## 6. What explicitly does NOT change (fidelity guarantees)

- The circular buffer stores full-res frames; incident MP4s are encoded from full-res frames.
- The snapshot pipeline resizes the full-res `frame` to 854×480 as today — the dashboard picture is unaffected.
- Detection stages, thresholds, fusion, cooldown, thermal tiers, heartbeat, and upload are unchanged.
- With a bare/legacy config (no `detect_scale`/`stabilize` keys and `detect_scale` defaulting to 1.0), behavior is byte-for-byte the current behavior.

---

## 7. Test plan (TDD, RED → GREEN)

New/updated tests in `tests/test_visual_detector.py`. Watch each fail first.

1. **Downscale geometry** — construct `VisualDetector(config, fps=15, detect_scale=0.5)`; after first `analyze()` the ROI mask shape is `(360, 640)` and `_roi_pixel_count > 0`.
2. **Downscale detection still works** — at `detect_scale=0.5`: normal frames don't trigger; a synthetic multi-line crack frame triggers within N frames; a white/black anomaly frame returns `None`. (Mirrors the existing suites at half-res.)
3. **ORB computed once per frame** — wrap/spy `detector._orb.detectAndCompute`; after a warm-up frame, each `analyze()` invokes it exactly once (RED: currently twice).
4. **ORB skipped on anomaly frames** — after baseline, feed an anomaly frame; `detectAndCompute` is not called for that frame (RED: currently called before the anomaly check).
5. **`stabilize=False` bypass** — construct with `stabilize=False`; `analyze()` runs and `detectAndCompute` is never called.
6. **Existing tests unchanged** — the current suite (bare config → `detect_scale=1.0`, `stabilize=True`) stays green with no edits, proving backward compatibility.

Then: full `edge_glass` pytest suite green (`config_loader`, `main_helpers`, `trigger_engine`, etc.).

---

## 8. Flagged for a SEPARATE decision (not implemented here)

`TriggerEngine._check_correlation` (`trigger_engine.py:192`) requires `visual_active AND audio_active`. With `audio.enabled: false` on the cloud nodes, the audio timestamp never sets, so **a real event can never fire** — only `force_trigger` (the `simulate_trigger` command) produces an event. The nodes currently act as snapshot streamers, not alarms.

This is a product/correctness decision (should visual-only nodes alarm on vision alone — an OR mode, or an explicit `trigger.mode: visual_only`?), independent of thermal work, and is **left untouched** by this change. Recommended as the next follow-up after this ships. The thermal work stands alone: the CV must be ready for when triggering is fixed, and the snapshot/temperature pipelines run regardless.

---

## 9. Rollout & validation

1. Land on `origin/main` (fast-forward; node secrets in `config.zeabur.yaml` untouched).
2. Deploy to the one console-reachable node via the established clone + `rsync --exclude=config.zeabur.yaml` procedure; optionally trim the local `camera.fps: 15` line in the same session.
3. **Validate on the dashboard:** watch the node's CPU-temp tile settle to a lower sustained value; confirm the snapshot picture still updates at ~1 fps and detector health stays `ok`.
4. Roll to `glass_node_02` / `glass_node_03` once reachable (or fold into the pending auto-sync work).

---

## 10. Risks & rollback

- **Half-res misses hairline cracks.** Target is breakage, not inspection; `detect_scale` is per-node reversible to `1.0` with no redeploy.
- **`stabilize=false` on a non-rigid mount** would reintroduce shake false-positives — which is why the **default stays `true`**; only a knowingly-rigid node flips it.
- **`align-to-raw-prev` behavior change** in the stabilizer is covered by the crack/normal/anomaly suites at both scales.
- **Rollback:** revert the DEFAULTS values (or set per-node overrides); pure-code wins are behavior-preserving and low-risk. The deploy is a fast-forward, trivially revertible to the prior SHA.
