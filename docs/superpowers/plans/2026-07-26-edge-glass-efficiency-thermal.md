# Edge Glass Efficiency + Thermal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the glass node's continuous CV work ~2–3× (lower sustained CPU temperature) with zero loss to recording/snapshot fidelity, all fidelity levers reversible per node.

**Architecture:** Optimize `VisualDetector` in place — compute ORB features once per frame (cache), run the cheap brightness gate before the expensive stabilizer, and add two config-driven knobs (`detect_scale` to run detection on a downscaled working copy, `stabilize` to skip stabilization). The detector keeps reading every parameter from its `visual` config dict, so production defaults flow through `config_loader.DEFAULTS` and **no `edge_glass_main.py` change is required**.

**Tech Stack:** Python 3.14, OpenCV (`cv2`), NumPy, pytest.

## Global Constraints

- **TDD, always:** every code change is test-first — write the test, run it, watch it fail for the right reason, then implement. Never write production code before a failing test.
- **Test runner:** `cd edge_glass && /c/Python314/python -m pytest tests/<file>.py -q -p no:cacheprovider`. Run **per test file** (this project has a running-all-suites-at-once trap) — after each task run at least the file(s) it touches.
- **"Config in" pattern:** `detect_scale` and `stabilize` are read inside `VisualDetector.__init__` from the `visual` config dict (`config.get("detect_scale", 1.0)`, `config.get("stabilize", True)`), exactly like the existing thresholds. Do **not** add constructor kwargs and do **not** modify `edge_glass_main.py` — production values arrive via `config_loader.DEFAULTS`.
- **Backward compatibility (hard requirement):** with a bare/legacy config (no `detect_scale`/`stabilize` keys → defaults `1.0` / `True`), detector behavior is byte-identical to today. Every existing test in `tests/test_visual_detector.py` MUST stay green **without edits**.
- **Fidelity guarantee:** downscaling happens only on the detector's own local copy of the frame (rebind the local `frame` variable). The caller's full-resolution frame — used by the circular buffer, incident MP4, and snapshot — must remain untouched.
- **Detection base canvas:** the detector assumes a 1280×720 input (as the current hardcoded ROI mask already does). The working canvas is `(1280×detect_scale, 720×detect_scale)`; the working frame is resized to exactly those dims so mask and frame always match.
- **Security / secrets:** `config.zeabur.yaml` carries each node's secrets. Only *remove* the `camera.fps` line and *add comments* — never add, alter, or place real secret values. This change deploys as a fast-forward; node secrets are untouched.
- **Behavior change to acknowledge:** the stabilizer now aligns each frame to the previous **raw** frame (not the previous *aligned* frame) — this is what makes descriptor caching sound, and is covered by the crack/normal/anomaly suites at both scales.

**Spec:** `docs/superpowers/specs/2026-07-26-edge-glass-efficiency-thermal-design.md`

---

### Task 1: ORB descriptor cache (compute features once per frame)

Today `_stabilize()` runs `cv2.ORB.detectAndCompute` on **both** the previous and the current frame every call — but the previous frame's descriptors were already computed one iteration earlier. Cache them so ORB (the single most expensive step) runs once per frame.

**Files:**
- Modify: `edge_glass/detectors/visual_detector.py` (`__init__` ~line 71-74; `_stabilize` ~line 147-203; `analyze` ~line 411-457)
- Test: `edge_glass/tests/test_visual_detector.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_CountingORB` test helper (module-level in the test file) reused by Tasks 2 and 4. Detector state changes from `self._prev_gray` to `self._prev_kp` / `self._prev_des` (the "have-previous" sentinel is `self._prev_des is None`).

- [ ] **Step 1: Write the failing test**

Add to `edge_glass/tests/test_visual_detector.py` (module level, near the top after imports):

```python
class _CountingORB:
    """Wraps a real cv2.ORB, counting detectAndCompute calls, delegating the rest."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def detectAndCompute(self, *args, **kwargs):
        self.calls += 1
        return self._real.detectAndCompute(*args, **kwargs)


def test_orb_features_computed_once_per_frame():
    """Stabilization must reuse the previous frame's cached descriptors, so ORB
    detectAndCompute runs exactly once per analyze() (was twice: prev + current)."""
    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    spy = _CountingORB(detector._orb)
    detector._orb = spy

    frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(5):
        detector.analyze(frame)

    # 5 analyze() calls -> exactly 5 detectAndCompute calls (one per frame).
    assert spy.calls == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py::test_orb_features_computed_once_per_frame -q -p no:cacheprovider`
Expected: FAIL — `assert 8 == 5` (current code: 0 calls on frame 1, then 2 calls × 4 frames).

- [ ] **Step 3: Implement the descriptor cache**

In `__init__`, replace the stabilization-state lines:

```python
        # [2] 防震對齊
        self._orb = cv2.ORB_create(nfeatures=500)
        self._bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        # 前一幀的 ORB 特徵快取：每幀只算一次特徵，重用上一幀已算好的描述子（原本
        # 每幀對 prev 與 current 各算一次，等於重算了上一幀）。以 _prev_des 是否為
        # None 作為「是否有前一幀」的哨兵，取代原本的 _prev_gray。
        self._prev_kp = None
        self._prev_des: Optional[np.ndarray] = None
```

(Delete the old `self._prev_gray: Optional[np.ndarray] = None` line.)

Replace the whole `_stabilize` method body with:

```python
    def _stabilize(self, gray: np.ndarray) -> np.ndarray:
        """
        步驟 [2]：防震對齊。

        只對「當前幀」計算一次 ORB 特徵，與上一幀「已快取」的特徵配對；對齊基準是
        上一幀的原始灰度（raw），非上一幀對齊後的輸出（這才使快取成立）。
        """
        try:
            kp2, des2 = self._orb.detectAndCompute(gray, None)
            prev_kp, prev_des = self._prev_kp, self._prev_des
            # 先讀舊值、後存新值：快取當前幀特徵供下一幀使用。
            self._prev_kp, self._prev_des = kp2, des2

            if (
                prev_des is None
                or des2 is None
                or len(prev_des) < 10
                or len(des2) < 10
            ):
                self._stabilize_warn_count += 1
                if (
                    self._stabilize_warn_count == 1
                    or self._stabilize_warn_count % self._stabilize_warn_interval == 0
                ):
                    logger.info(
                        "Stabilization skipped: not enough feature points (count=%d)",
                        self._stabilize_warn_count,
                    )
                return gray

            matches = self._bf_matcher.match(prev_des, des2)
            if len(matches) < 10:
                self._stabilize_warn_count += 1
                if (
                    self._stabilize_warn_count == 1
                    or self._stabilize_warn_count % self._stabilize_warn_interval == 0
                ):
                    logger.info(
                        "Stabilization skipped: not enough matches (count=%d)",
                        self._stabilize_warn_count,
                    )
                return gray

            src_pts = np.float32([prev_kp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

            M, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts)
            if M is None:
                return gray

            h, w = gray.shape
            aligned = cv2.warpAffine(gray, M, (w, h))
            return aligned

        except Exception as e:
            logger.warning(f"Stabilization failed: {e}")
            return gray
```

In `analyze`, delete **both** `self._prev_gray = aligned` lines (the one in the `if diff is None:` branch and the one just before the final `return`). The "previous frame" state is now maintained inside `_stabilize` via the cache, so those assignments are gone entirely. The two spots become:

```python
        diff = self._compute_diff(aligned)
        if diff is None:
            # 基線尚未建立
            return VisualResult(triggered=False)
```

and

```python
        triggered, confidence = self._analyze_contours(closed)

        return VisualResult(triggered=triggered, confidence=confidence)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py -q -p no:cacheprovider`
Expected: PASS — the new test plus **all existing** `test_visual_detector.py` tests green (flat/crack frames have no ORB features in these fixtures, so alignment is a no-op and behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/detectors/visual_detector.py edge_glass/tests/test_visual_detector.py
git commit -m "perf(edge): compute ORB features once per frame (descriptor cache)"
```

---

### Task 2: Cheap anomaly gate runs before the expensive stabilizer

Reorder `analyze()` so the `np.mean` brightness/anomaly check runs before ORB. Anomalous frames (night transition, lightning, light switch) return `None` without paying the stabilization cost. Mean brightness is ~invariant to affine alignment, so this is behaviorally equivalent for normal frames.

**Files:**
- Modify: `edge_glass/detectors/visual_detector.py` (`analyze` ~line 423-434)
- Test: `edge_glass/tests/test_visual_detector.py`

**Interfaces:**
- Consumes: `_CountingORB` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Add to `edge_glass/tests/test_visual_detector.py`:

```python
def test_orb_skipped_on_anomaly_frames():
    """The cheap brightness/anomaly gate runs before stabilization, so an anomaly
    frame returns None without paying the ORB cost."""
    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(10):
        detector.analyze(normal)  # establish the brightness baseline

    spy = _CountingORB(detector._orb)
    detector._orb = spy
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    result = detector.analyze(white)

    assert result is None
    assert spy.calls == 0  # anomaly frame skipped stabilization entirely
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py::test_orb_skipped_on_anomaly_frames -q -p no:cacheprovider`
Expected: FAIL — `assert 1 == 0` (stabilization currently runs before the anomaly check).

- [ ] **Step 3: Reorder the pipeline**

In `analyze`, change the top of the pipeline from:

```python
        # [1] 灰度轉換
        gray = self._to_gray(frame)

        # [2] 防震對齊
        aligned = self._stabilize(gray)

        # [3] 異常幀排除
        if not self._check_anomaly(aligned):
            return None
```

to:

```python
        # [1] 灰度轉換
        gray = self._to_gray(frame)

        # [3] 異常幀排除（廉價閘門先跑）：異常幀（夜間/閃電/切燈）直接返回，不用付出
        #     ORB 成本。平均亮度對仿射對齊近似不變，故在對齊前檢查與原行為等價。
        if not self._check_anomaly(gray):
            return None

        # [2] 防震對齊
        aligned = self._stabilize(gray)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py -q -p no:cacheprovider`
Expected: PASS — new test green; existing anomaly tests (`test_anomaly_frame_returns_none`, `test_single_anomaly_sets_blinded`, `test_sustained_anomaly_recovers`, etc.) still green.

- [ ] **Step 5: Commit**

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/detectors/visual_detector.py edge_glass/tests/test_visual_detector.py
git commit -m "perf(edge): run cheap brightness gate before ORB stabilization"
```

---

### Task 3: `detect_scale` — run detection on a downscaled working copy

Add a `detect_scale` knob (default `1.0`). When `< 1.0`, `analyze()` downscales its own copy of the frame; the ROI mask is built at the scaled canvas and `min_contour_length_px` is compared at `× detect_scale`. The caller's full-res frame (buffer/snapshot) is untouched.

**Files:**
- Modify: `edge_glass/detectors/visual_detector.py` (`__init__` ~line 60-98; `analyze` top; `_analyze_contours` ~line 374)
- Test: `edge_glass/tests/test_visual_detector.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `self._detect_scale` (float, clamped `(0, 1]`), `self._work_w`, `self._work_h` (ints), `self._min_contour_length_effective` (float).

- [ ] **Step 1: Write the failing tests**

Add to `edge_glass/tests/test_visual_detector.py`:

```python
def test_detect_scale_builds_scaled_roi_mask():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    # Half-res working canvas: 1280x720 -> 640x360. numpy shape is (h, w).
    assert detector._roi_mask.shape == (360, 640)
    assert detector._roi_pixel_count > 0
    # A fixed physical length spans half the pixels at half resolution.
    assert detector._min_contour_length_effective == VISUAL_CONFIG["min_contour_length_px"] * 0.5


def test_detect_scale_rejects_normal_and_flags_anomaly():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    result = None
    for _ in range(30):
        result = detector.analyze(normal)
    if result:
        assert result.triggered is False
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    assert detector.analyze(white) is None


def test_detect_scale_triggers_on_crack():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(30):
        detector.analyze(normal)
    crack = np.full((720, 1280, 3), 128, dtype=np.uint8)
    # Bolder strokes than the full-res crack test: at half resolution thin lines
    # blur below the Canny/contour floor.
    cv2.line(crack, (200, 200), (1000, 600), (255, 255, 255), 6)
    cv2.line(crack, (300, 100), (900, 500), (255, 255, 255), 5)
    cv2.line(crack, (400, 300), (800, 650), (255, 255, 255), 6)
    triggered = False
    for _ in range(10):
        r = detector.analyze(crack)
        if r and r.triggered:
            triggered = True
            break
    assert triggered, "half-res detector should still trigger on a bold crack"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py -k detect_scale -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'VisualDetector' object has no attribute '_min_contour_length_effective'` / `_work_w`, and the ROI mask is still `(720, 1280)`.

- [ ] **Step 3: Implement `detect_scale`**

In `__init__`, right after `self._config = config` / `self._fps = fps` and the parameter reads, add the scale (place it before the ROI-mask block):

```python
        # 偵測降採樣比例（visual.detect_scale）：偵測管線在原尺寸的 detect_scale 倍
        # 工作影像上執行，畫素量約 detect_scale² 倍。錄影緩衝／快照仍用原尺寸幀，
        # 證據與儀表板畫面不受影響。夾在 (0, 1]。
        self._detect_scale = min(1.0, max(0.01, float(config.get("detect_scale", 1.0))))
```

Just after `self._min_contour_length_px = config.get("min_contour_length_px", 100)`, add:

```python
        # 固定物理長度在低解析度下佔用較少畫素，故有效輪廓門檻按 detect_scale 縮放。
        self._min_contour_length_effective = self._min_contour_length_px * self._detect_scale
```

Replace the ROI-mask block:

```python
        # [6] ROI 遮罩（預生成）
        roi_polygon = config.get(
            "roi_polygon",
            [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        )
        self._roi_mask = self._create_roi_mask(roi_polygon, 1280, 720)
        self._roi_pixel_count = np.count_nonzero(self._roi_mask)
```

with:

```python
        # [6] ROI 遮罩（預生成）
        # 偵測基準畫布固定為 1280x720（既有假設）。降採樣時，遮罩與工作影像一律縮到
        # 此畫布的 detect_scale 倍，兩者尺寸永遠一致；ROI 多邊形亦以 detect_scale 縮放。
        roi_polygon = config.get(
            "roi_polygon",
            [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        )
        _BASE_W, _BASE_H = 1280, 720
        self._work_w = max(1, int(round(_BASE_W * self._detect_scale)))
        self._work_h = max(1, int(round(_BASE_H * self._detect_scale)))
        scaled_polygon = [
            [int(round(x * self._detect_scale)), int(round(y * self._detect_scale))]
            for x, y in roi_polygon
        ]
        self._roi_mask = self._create_roi_mask(scaled_polygon, self._work_w, self._work_h)
        self._roi_pixel_count = np.count_nonzero(self._roi_mask)
```

At the very top of `analyze`, before `gray = self._to_gray(frame)`, add the downscale:

```python
        # 偵測降採樣：只縮偵測用的工作副本（重新綁定區域變數 frame）；呼叫端傳入的
        # 原始 frame（供緩衝／快照）不受影響。縮到工作畫布尺寸，與 ROI 遮罩一致。
        if self._detect_scale != 1.0:
            frame = cv2.resize(
                frame, (self._work_w, self._work_h), interpolation=cv2.INTER_AREA
            )

        # [1] 灰度轉換
        gray = self._to_gray(frame)
```

In `_analyze_contours`, change the contour-length comparison from `self._min_contour_length_px` to the effective value:

```python
            length = cv2.arcLength(contour, closed=False)
            if length > self._min_contour_length_effective:
                significant_contours.append(contour)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py -q -p no:cacheprovider`
Expected: PASS — new `detect_scale` tests green; all existing tests still green (at the default `detect_scale=1.0`: `_work_w/_work_h` = 1280/720, no resize, mask `(720, 1280)`, effective == raw min-contour — byte-identical).
Note: if `test_detect_scale_triggers_on_crack` does not trigger, increase the line thickness/count — half-res needs bolder synthetic cracks; this is test-fixture tuning, not a code bug.

- [ ] **Step 5: Commit**

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/detectors/visual_detector.py edge_glass/tests/test_visual_detector.py
git commit -m "perf(edge): detect_scale knob — run detection on a downscaled working copy"
```

---

### Task 4: `stabilize` flag — skip ORB stabilization entirely

Add a `stabilize` knob (default `True`). When `False`, `analyze()` skips ORB/match/warp — the biggest single win on a rigidly-mounted node. Default stays `True` so nothing changes until a node opts in.

**Files:**
- Modify: `edge_glass/detectors/visual_detector.py` (`__init__`; `analyze` stabilize call)
- Test: `edge_glass/tests/test_visual_detector.py`

**Interfaces:**
- Consumes: `_CountingORB` (Task 1), `detect_scale` behavior (Task 3).
- Produces: `self._stabilize_enabled` (bool).

- [ ] **Step 1: Write the failing test**

Add to `edge_glass/tests/test_visual_detector.py`:

```python
def test_stabilize_flag_false_skips_orb_entirely():
    cfg = {**VISUAL_CONFIG, "stabilize": False}
    detector = VisualDetector(cfg, fps=15)
    spy = _CountingORB(detector._orb)
    detector._orb = spy
    frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    result = None
    for _ in range(5):
        result = detector.analyze(frame)
    assert spy.calls == 0
    # Detection still runs; a flat frame simply does not trigger.
    if result:
        assert result.triggered is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py::test_stabilize_flag_false_skips_orb_entirely -q -p no:cacheprovider`
Expected: FAIL — `assert 5 == 0` (stabilization still runs; ORB called once per frame after Task 1).

- [ ] **Step 3: Implement the flag**

In `__init__`, alongside the other stabilization state (just after the `self._prev_kp` / `self._prev_des` cache lines), add:

```python
        # 防震對齊開關（visual.stabilize）：預設開啟（維持現行行為）。剛性固定的攝像頭
        # 可關閉，省下整段 ORB＋配對＋warpAffine（管線最貴的部分）。
        self._stabilize_enabled = bool(config.get("stabilize", True))
```

In `analyze`, change the stabilization call:

```python
        # [2] 防震對齊（可由 visual.stabilize 關閉）
        aligned = self._stabilize(gray) if self._stabilize_enabled else gray
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_visual_detector.py -q -p no:cacheprovider`
Expected: PASS — new test green; every other `test_visual_detector.py` test green (default `stabilize=True`).

- [ ] **Step 5: Commit**

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/detectors/visual_detector.py edge_glass/tests/test_visual_detector.py
git commit -m "perf(edge): stabilize flag — allow skipping ORB stabilization per node"
```

---

### Task 5: Ship the defaults + document the knobs

Set the production defaults in `config_loader.DEFAULTS` so they reach nodes via the deep-merge, strip the explicit `camera.fps` from the lean node template so it inherits `12`, and document the knobs in the reference config.

**Files:**
- Modify: `edge_glass/utils/config_loader.py` (`DEFAULTS`: `camera.fps`, `visual` block)
- Modify: `edge_glass/config.zeabur.yaml` (remove `camera.fps`; add doc comments)
- Modify: `edge_glass/config.yaml` (reference values for the new knobs; `fps: 12`)
- Test: `edge_glass/tests/test_config_loader.py`

**Interfaces:**
- Consumes: the config keys `visual.detect_scale`, `visual.stabilize` read by `VisualDetector` (Tasks 3–4).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Add to `edge_glass/tests/test_config_loader.py`:

```python
def test_defaults_deliver_detect_scale_to_lean_config():
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["visual"]["detect_scale"] == 0.5


def test_defaults_deliver_stabilize_to_lean_config():
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["visual"]["stabilize"] is True


def test_defaults_deliver_camera_fps_12_to_lean_config():
    # config.zeabur.yaml no longer pins camera.fps, so it inherits the 12 default.
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["camera"]["fps"] == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_config_loader.py -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'detect_scale'` and `assert 15 == 12` (config.zeabur.yaml still pins `fps: 15`).

- [ ] **Step 3: Update DEFAULTS**

In `edge_glass/utils/config_loader.py`, change `camera.fps`:

```python
    "camera": {
        "source": 0,
        "resolution": [1280, 720],
        "fps": 12,
    },
```

and add the two knobs inside the `visual` block (next to `detect_fps`):

```python
    "visual": {
        "detect_fps": 5,
        # 偵測降採樣：偵測管線在半解析度工作副本上執行（約 ¼ 畫素）。錄影/快照仍原尺寸。
        "detect_scale": 0.5,
        # 防震對齊開關：預設開啟。剛性固定攝像頭可關閉以省下最貴的 ORB 階段。
        "stabilize": True,
        "edge_density_threshold": 1.5,
        "baseline_window_seconds": 60,
        "brightness_anomaly_percent": 50,
        "min_contour_length_px": 100,
        "roi_polygon": [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        "canny_threshold1": 50,
        "canny_threshold2": 150,
        "anomaly_recovery_seconds": 3,
    },
```

- [ ] **Step 4: Strip `camera.fps` from the lean node template**

In `edge_glass/config.zeabur.yaml`, change the camera block from:

```yaml
camera:
  source: 0
  resolution: [1280, 720]
  fps: 15
```

to:

```yaml
camera:
  source: 0
  resolution: [1280, 720]
  # fps 由 config_loader.DEFAULTS 提供（12）；不在此固定，讓調校值隨更新下發。

# 偵測調校值（detect_fps / detect_scale / stabilize）皆來自 config_loader.DEFAULTS，
# 節點不需列出。如需覆寫可自行加入 visual: 區塊。
```

- [ ] **Step 5: Document the knobs in the reference config**

In `edge_glass/config.yaml`, set `fps: 12` and add the two knobs under `visual:` (this file is the explicit LAN reference; it is not loaded by any test):

```yaml
camera:
  source: 0
  resolution: [1280, 720]
  fps: 12                             # capture rate (buffer + snapshots); detection runs at detect_fps
```

```yaml
visual:
  detect_fps: 5                       # CV cadence (already present)
  detect_scale: 0.5                   # run detection on a half-res working copy (¼ pixels); 1.0 = full-res
  stabilize: true                     # ORB affine stabilization; false skips it (only safe on rigid mounts)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd edge_glass && /c/Python314/python -m pytest tests/test_config_loader.py tests/test_main_helpers.py -q -p no:cacheprovider`
Expected: PASS — new delivery tests green; existing `test_config_loader.py` (detect_fps/async_encode/heartbeat) and `test_main_helpers.py` (`resolve_detect_fps`, which passes `camera.fps` explicitly) still green.

- [ ] **Step 7: Commit**

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/utils/config_loader.py edge_glass/config.zeabur.yaml edge_glass/config.yaml edge_glass/tests/test_config_loader.py
git commit -m "feat(edge): ship detect_scale=0.5 + stabilize + camera.fps=12 defaults"
```

---

### Task 6: Full edge-suite regression sweep

Confirm nothing else regressed and record the result.

**Files:** none (verification only).

- [ ] **Step 1: Run the full edge test suite, file by file**

```bash
cd edge_glass
for f in tests/test_visual_detector.py tests/test_config_loader.py tests/test_main_helpers.py tests/test_trigger_engine.py tests/test_circular_buffer.py tests/test_event_capture.py tests/test_mqtt_heartbeat.py; do
  echo "=== $f ==="; /c/Python314/python -m pytest "$f" -q -p no:cacheprovider || break
done
```
Expected: every file green. (Run remaining `tests/test_*.py` the same way if time permits.)

- [ ] **Step 2: Sanity-check the fidelity guarantee by inspection**

Confirm in `edge_glass_main.py` that the snapshot path (`cv2.resize(frame, snapshot_size)`, ~line 605) and `buffer.append(timestamp, frame)` (~line 493) still receive the **caller's** `frame` — the detector's downscale is internal to `analyze()` and must not have leaked out. No code change expected; this is a read-only confirmation.

- [ ] **Step 3: Commit (only if any test fixture needed tuning)**

If `test_detect_scale_triggers_on_crack` required bolder lines, commit that fixture tweak:

```bash
cd "C:/D/WorkSpace/[Cloud]_Company_Sync/1Project(Single)/TyphoneCrackDetect_waterRemove/sdprs"
git add edge_glass/tests/test_visual_detector.py
git commit -m "test(edge): tune half-res crack fixture"
```

---

## Post-implementation (human-gated, NOT part of the TDD tasks)

- **Rebase before merge:** this branch (`fix/edge-glass-efficiency-thermal`) was cut from the webcam branch tip. Before merging to `main`, run `git rebase --onto main feat/webcam-startup-and-guard-ux fix/edge-glass-efficiency-thermal` so only these edge commits land. Do **not** merge/push without explicit user approval.
- **Deploy + validate:** deploy to the console-reachable node via the established clone + `rsync --exclude=config.zeabur.yaml` procedure (optionally trim the local `camera.fps: 15` line so it inherits 12). Then watch the node's **CPU-temp tile** on the dashboard settle lower, confirm the snapshot still updates ~1 fps, and detector health stays `ok`.
- **Out of scope (flagged separately):** the trigger AND-logic issue (`trigger_engine.py:192`) that prevents visual-only nodes from firing real events — a separate product decision, not touched here.
