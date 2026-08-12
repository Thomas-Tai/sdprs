# Single-sensor (visual-only) Fallback Fusion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an audio-disabled `edge_glass` node alert on the camera alone — gated by a temporal dwell **and** a raised confidence bar — instead of being permanently silent under the visual-AND-audio gate.

**Architecture:** All logic lives in `TriggerEngine`. The engine learns at construction whether audio has a stream (`audio_available`) and the computed solo confidence bar; when audio is absent *and* the per-node flag is on, a solo path runs alongside the existing AND check (which can't fire without audio). No `VisualDetector` change — the engine enforces the raised bar from the `confidence` it already receives. `edge_glass_main.py` moves the engine construction to *after* audio init so it can pass `audio_available = (audio_stream is not None)`.

**Tech Stack:** Python 3.14, pytest. No new dependencies. Time injected via `evaluate(..., current_time=...)`; no hardware, no clocks, no sleeps.

## Global Constraints

- **Edge-only:** touch only `edge_glass/`. No `central_server/` or SPA changes.
- **No new dependencies** (production or test). Mocked/`current_time`-injected tests only; no real audio/camera hardware.
- **Strict TDD:** each behavior fails RED against the unchanged production file, then GREEN. Controller A/B-verifies RED (reverts only the production file) — never trusts a subagent self-report.
- **Zero regression:** with audio available, or the flag off, the engine is behaviorally identical to today. The existing `test_trigger_engine.py` must stay green **unchanged**.
- **Banned strings** — the credential/broker literals (`Msc@***` / `MSC-***` / the EMQX public broker) must never appear in any diff.
- **Datetime:** N/A here (engine uses injected float `current_time`); never introduce `datetime.utcnow()`.
- **zh-TW** for user-facing strings / comments consistent with the file's existing Traditional-Chinese comments.
- **Config defaults preserve behavior:** `visual_only_fallback` defaults `false`; `visual_only_confidence_multiplier` defaults `1.5`.
- **Test run context:** edge_glass tests run from the `edge_glass/` CWD (bare `from detectors...` imports). Command prefix: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest ...`.
- **Worktree:** all work in `C:/Users/sky/AppData/Local/Temp/sdprs-single-sensor-wt`, branch `feat/single-sensor-glass-fusion-2026-08-07`. Nothing reaches `origin/main` without the user's literal "approved".

**Spec:** `docs/superpowers/specs/2026-08-07-single-sensor-glass-fusion-design.md` (committed `126fe85`).

---

## File Structure

- **Modify** `edge_glass/detectors/trigger_engine.py` — `Event.trigger_source` field; `TriggerEngine.__init__` gains `audio_available` + `solo_confidence_threshold` + reads `visual_only_fallback`; dwell run tracking `_visual_run_start`; `_check_visual_only`; solo path in `evaluate`.
- **Modify** `edge_glass/tests/test_trigger_engine.py` — append new test classes; existing classes untouched (regression proof).
- **Modify** `edge_glass/edge_glass_main.py` — move `TriggerEngine(...)` construction to after audio init with the two new kwargs; add `trigger_source` to `event_metadata`.
- **Modify** `edge_glass/config.yaml` and `edge_glass/config.zeabur.yaml` — two new documented keys under `trigger:`.

Task order is a strict dependency chain: **1 → 2 → 3 → 4 → 5** (Task 4 consumes the Task 3 constructor signature and the Task 1 field). This is not parallelizable; see the plan discussion.

---

### Task 1: Event provenance field (`trigger_source`)

**Files:**
- Modify: `edge_glass/detectors/trigger_engine.py` (the `Event` dataclass, ~line 35–46)
- Test: `edge_glass/tests/test_trigger_engine.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Event.trigger_source: str = "fusion"` — later tasks set `"visual_only"` on solo events and read it in `event_metadata`.

- [ ] **Step 1: Write the failing test**

Append to `edge_glass/tests/test_trigger_engine.py`:

```python
class TestEventProvenance:
    """事件來源標記（trigger_source）。"""

    def test_fusion_event_has_trigger_source(self, engine):
        """相關配對產生的事件其 trigger_source 應為 'fusion'。"""
        event = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert event is not None
        assert event.trigger_source == "fusion"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py::TestEventProvenance -v`
Expected: FAIL — `AttributeError: 'Event' object has no attribute 'trigger_source'`.

- [ ] **Step 3: Write minimal implementation**

In the `Event` dataclass, add the field after `is_simulation`:

```python
    is_simulation: bool = False  # 是否為模擬事件
    trigger_source: str = "fusion"  # 事件來源："fusion"（視覺 AND 音訊）| "visual_only"（純視覺回退）
```

(The existing fusion `Event(...)` build and `force_trigger`'s build both omit `trigger_source`, so they default to `"fusion"` — no other change here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py -v`
Expected: PASS — the new test plus all existing tests green.

- [ ] **Step 5: Commit**

```bash
git add edge_glass/detectors/trigger_engine.py edge_glass/tests/test_trigger_engine.py
git commit -m "feat(edge_glass): add Event.trigger_source provenance field (default 'fusion')"
```

---

### Task 2: Visual dwell run tracking (`_visual_run_start`)

Tracks the start-time of the current unbroken visual-trigger run so the solo path (Task 3) can measure dwell. Pure internal bookkeeping — no firing behavior changes here; asserted via the private attribute, matching the existing suite's style (it already asserts `engine._last_visual_trigger_time`).

**Files:**
- Modify: `edge_glass/detectors/trigger_engine.py` (`__init__` state + the visual-update block in `evaluate`, ~line 104–111)
- Test: `edge_glass/tests/test_trigger_engine.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `self._visual_run_start: Optional[float]` — `None` when no run active; set to the run's start time; read by Task 3's `_check_visual_only`.

- [ ] **Step 1: Write the failing test**

Append to `edge_glass/tests/test_trigger_engine.py`:

```python
class TestDwellRunTracking:
    """視覺持續觸發區間（dwell run）追蹤。"""

    def test_triggered_starts_run_and_start_is_stable(self, engine):
        """triggered=True 開始區間；後續 triggered 幀不移動起點。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(_visual(triggered=True), None, current_time=BASE + 1.0)
        assert engine._visual_run_start == BASE  # 起點保持不變

    def test_triggered_false_clears_run(self, engine):
        """triggered=False 中斷區間 → 起點重置為 None。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(_visual(triggered=False), None, current_time=BASE + 0.5)
        assert engine._visual_run_start is None

    def test_none_visual_leaves_run_unchanged(self, engine):
        """visual_result=None（節流幀）不改變區間。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(None, None, current_time=BASE + 0.5)
        assert engine._visual_run_start == BASE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py::TestDwellRunTracking -v`
Expected: FAIL — `AttributeError: 'TriggerEngine' object has no attribute '_visual_run_start'`.

- [ ] **Step 3: Write minimal implementation**

In `__init__`, next to the trigger-time state (after `self._last_audio_trigger_time = None`), add:

```python
        # 視覺持續觸發區間起點（供純視覺回退的 dwell 判定）
        self._visual_run_start: Optional[float] = None
```

Replace the visual-update block in `evaluate` (currently the `if visual_result is not None and visual_result.triggered:` block) with:

```python
        # 更新視覺觸發時間與持續觸發區間（dwell run）
        if visual_result is not None:
            if visual_result.triggered:
                # 開始一段新的持續觸發區間（若尚未開始）；進行中則保持起點不變
                if self._visual_run_start is None:
                    self._visual_run_start = current_time
                self._last_visual_trigger_time = current_time
                self._last_visual_confidence = visual_result.confidence
                logger.debug(
                    f"Visual trigger at {current_time:.3f}, "
                    f"confidence={visual_result.confidence:.2f}"
                )
            else:
                # triggered=False：裂紋特徵消失，持續區間中斷
                self._visual_run_start = None
        # visual_result is None（偵測節流／暫停幀）：保持 dwell 區間不變
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py -v`
Expected: PASS — new `TestDwellRunTracking` plus all existing tests green (behavior unchanged: nothing yet reads `_visual_run_start`).

- [ ] **Step 5: Commit**

```bash
git add edge_glass/detectors/trigger_engine.py edge_glass/tests/test_trigger_engine.py
git commit -m "feat(edge_glass): track visual dwell run start for single-sensor fallback"
```

---

### Task 3: Solo-fire fallback logic + constructor params

The core. Adds `audio_available` + `solo_confidence_threshold` + `visual_only_fallback`, a `_check_visual_only` gate, and a solo path in `evaluate` that runs alongside the (audio-less, non-firing) AND check. Provenance set to `"visual_only"` on solo events.

**Files:**
- Modify: `edge_glass/detectors/trigger_engine.py` (`__init__` signature/body; `evaluate` correlation/cooldown/event block ~line 125–152; new `_check_visual_only`)
- Test: `edge_glass/tests/test_trigger_engine.py`

**Interfaces:**
- Consumes: `Event.trigger_source` (Task 1); `self._visual_run_start` (Task 2).
- Produces:
  - `TriggerEngine.__init__(self, config, node_id, audio_available: bool = True, solo_confidence_threshold: Optional[float] = None)` — Task 4 calls this exact signature.
  - `self._visual_only_fallback = config.get("visual_only_fallback", False)`.
  - `evaluate` fires a solo `Event(trigger_source="visual_only")` when: `audio_available` is False AND `visual_only_fallback` on AND `solo_confidence_threshold` provided AND this frame `visual_result.triggered` AND dwell `current_time - _visual_run_start >= correlation_window_seconds` AND `visual_result.confidence >= solo_confidence_threshold` AND cooldown satisfied.

- [ ] **Step 1: Write the failing test**

Append to `edge_glass/tests/test_trigger_engine.py`:

```python
# 純視覺回退測試用配置與門檻
SOLO_TRIGGER_CONFIG = {
    "correlation_window_seconds": 2,
    "cooldown_seconds": 30,
    "visual_only_fallback": True,
}
# 提高後的信心門檻 = edge_density_threshold(1.5) × multiplier(1.5) = 2.25
SOLO_THRESHOLD = 2.25


@pytest.fixture
def solo_engine():
    """音訊無串流 + 回退啟用 + 提高門檻的引擎。"""
    return TriggerEngine(
        SOLO_TRIGGER_CONFIG,
        node_id="test_node",
        audio_available=False,
        solo_confidence_threshold=SOLO_THRESHOLD,
    )


class TestVisualOnlyFallback:
    """單感測器（純視覺）回退。"""

    def test_and_unchanged_when_audio_available(self):
        """audio_available=True：即使視覺持續高信心也絕不單獨觸發。"""
        eng = TriggerEngine(
            SOLO_TRIGGER_CONFIG, node_id="test_node",
            audio_available=True, solo_confidence_threshold=SOLO_THRESHOLD,
        )
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0) is None

    def test_flag_off_no_solo(self):
        """visual_only_fallback 未設（=off）：不單獨觸發。"""
        eng = TriggerEngine(
            {"correlation_window_seconds": 2, "cooldown_seconds": 30},
            node_id="test_node",
            audio_available=False, solo_confidence_threshold=SOLO_THRESHOLD,
        )
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0) is None

    def test_dwell_gate_before_window(self, solo_engine):
        """持續時間未達 correlation_window（<2s）→ 不觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 1.0) is None

    def test_confidence_gate_below_bar(self, solo_engine):
        """持續達 dwell 但本幀信心低於提高後門檻（2.0 < 2.25）→ 不觸發。"""
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE + 2.0) is None
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE + 2.5) is None

    def test_solo_fire_after_dwell_and_bar(self, solo_engine):
        """持續達 dwell 且信心 >= 門檻 → 產生 visual_only 事件。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert event is not None
        assert event.trigger_source == "visual_only"
        assert event.is_simulation is False
        assert event.node_id == "test_node"

    def test_none_gap_does_not_break_dwell(self, solo_engine):
        """區間中的 None（節流幀）不重置 dwell → 仍於窗口末端觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(None, None, current_time=BASE + 1.0) is None
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert event is not None
        assert event.trigger_source == "visual_only"

    def test_triggered_false_breaks_dwell(self, solo_engine):
        """triggered=False 中斷 dwell，需重新持續滿窗口才觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(triggered=False), None, current_time=BASE + 1.0) is None
        # 於 BASE+1.5 重新開始；BASE+2.0 尚未滿窗口（0.5s）
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 1.5) is None
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0) is None
        # 自 BASE+1.5 起滿窗口 → 於 BASE+3.5 觸發
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 3.5)
        assert event is not None
        assert event.trigger_source == "visual_only"

    def test_solo_cooldown_suppresses_second(self, solo_engine):
        """觸發後 30 秒冷卻期內的第二次合格 solo 被抑制。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        first = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert first is not None
        # 觸發後區間已重置；於 BASE+3.0 重新開始一段 run
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 3.0) is None
        # BASE+5.0 dwell 已滿（2s）但仍在冷卻期（距上次事件 3s）→ 抑制
        second = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0)
        assert second is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py::TestVisualOnlyFallback -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'audio_available'` (and, once the ctor accepts it, the solo assertions fail because no solo path exists yet).

- [ ] **Step 3: Write minimal implementation**

**(3a)** Change the `__init__` signature and add state. New signature:

```python
    def __init__(
        self,
        config: dict,
        node_id: str,
        audio_available: bool = True,
        solo_confidence_threshold: Optional[float] = None,
    ):
```

In the body, after `self._node_id = node_id`, add:

```python
        # 單感測器（純視覺）回退設定
        self._visual_only_fallback = config.get("visual_only_fallback", False)
        self._audio_available = audio_available
        self._solo_confidence_threshold = solo_confidence_threshold
```

**(3b)** Replace the correlation/cooldown/event block in `evaluate` (from the `if not self._check_correlation(current_time): return None` through the two timestamp resets) with:

```python
        # 檢查融合（Visual AND Audio）或純視覺回退
        correlated = self._check_correlation(current_time)
        solo = (not correlated) and self._check_visual_only(visual_result, current_time)

        if not (correlated or solo):
            return None

        # 檢查冷卻期
        if not self._check_cooldown(current_time):
            logger.info("Trigger suppressed by cooldown")
            return None

        trigger_source = "fusion" if correlated else "visual_only"

        # 產生事件
        event = Event(
            timestamp=current_time,
            node_id=self._node_id,
            visual_confidence=self._last_visual_confidence,
            audio_delta_db=self._last_audio_delta_db,
            audio_flatness=self._last_audio_flatness,
            audio_db_peak=self._last_audio_db_peak,
            audio_freq_peak_hz=self._last_audio_freq_peak_hz,
            is_simulation=False,
            trigger_source=trigger_source,
        )

        # 更新上次事件時間
        self._last_event_time = current_time

        # 重置觸發時間戳與 dwell 區間：下一次事件必須由新鮮觸發重新配對／重新持續。
        self._last_visual_trigger_time = None
        self._last_audio_trigger_time = None
        self._visual_run_start = None
```

(Leave the subsequent `logger.info("EVENT TRIGGERED: ...")` and `return event` as-is.)

**(3c)** Add `_check_visual_only` (place it right after `_check_correlation`):

```python
    def _check_visual_only(
        self, visual_result: Optional["VisualResult"], current_time: float
    ) -> bool:
        """
        純視覺回退判定（不含冷卻期，冷卻期由呼叫端另行檢查）。

        僅在下列全部成立時回傳 True：
          - 音訊完全無串流（audio_available=False）且 visual_only_fallback 啟用；
          - 已提供提高後的信心門檻；
          - 本幀有新鮮視覺觸發（visual_result.triggered）；
          - dwell：持續觸發區間已達 correlation_window_seconds；
          - 本幀 confidence >= 提高後的信心門檻。
        """
        if self._audio_available or not self._visual_only_fallback:
            return False
        if self._solo_confidence_threshold is None:
            return False
        if visual_result is None or not visual_result.triggered:
            return False
        if self._visual_run_start is None:
            return False
        if current_time - self._visual_run_start < self._correlation_window:
            return False
        if visual_result.confidence < self._solo_confidence_threshold:
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/test_trigger_engine.py -v`
Expected: PASS — `TestVisualOnlyFallback` (8 cases) plus every earlier test green. (Zero regression: with `audio_available=True` or the flag off, `_check_visual_only` returns False immediately, so `evaluate` behaves exactly as before.)

- [ ] **Step 5: Commit**

```bash
git add edge_glass/detectors/trigger_engine.py edge_glass/tests/test_trigger_engine.py
git commit -m "feat(edge_glass): single-sensor visual-only fallback (dwell + raised-confidence gate)"
```

---

### Task 4: Wiring (`edge_glass_main.py`) + config keys

Structural/plumbing task — `edge_glass_main.py::main()` has no unit harness, so verification is: byte-compile + YAML-parse + the full edge_glass suite staying green + controller diff inspection. No new unit test.

**Files:**
- Modify: `edge_glass/edge_glass_main.py` (remove construction at ~289–292; re-add after audio init ~371; add `trigger_source` to `event_metadata` ~528–533)
- Modify: `edge_glass/config.yaml` (`trigger:` block ~66–68)
- Modify: `edge_glass/config.zeabur.yaml` (`trigger:` block ~43–45)

**Interfaces:**
- Consumes: `TriggerEngine(config, node_id, audio_available=..., solo_confidence_threshold=...)` (Task 3); `event.trigger_source` (Task 1).
- Produces: nothing downstream.

- [ ] **Step 1: Remove the early construction**

Delete the current block at `edge_glass_main.py` ~289–292:

```python
    trigger_engine = TriggerEngine(
        config["trigger"],
        node_id=config["node_id"],
    )
```

(`trigger_engine` is not referenced until the main loop at ~510/516, so removing it here is safe.)

- [ ] **Step 2: Re-add after audio init**

Immediately **after** the audio-init block (after the `else: ... audio_stream = None` at ~367–371), insert:

```python
    # 觸發引擎於音訊初始化之後建立，才能得知 audio_available
    # （audio_stream is None 即 compute_audio_health 判定 "disabled" 的訊號）。
    trigger_engine = TriggerEngine(
        config["trigger"],
        node_id=config["node_id"],
        audio_available=(audio_stream is not None),
        solo_confidence_threshold=(
            config["visual"]["edge_density_threshold"]
            * config["trigger"].get("visual_only_confidence_multiplier", 1.5)
        ),
    )
```

- [ ] **Step 3: Add provenance to event_metadata**

In the `event_metadata` dict (~528–533), add the `trigger_source` line:

```python
            event_metadata = {
                "visual_confidence": event.visual_confidence,
                "audio_db_peak": event.audio_db_peak,
                "audio_freq_peak_hz": event.audio_freq_peak_hz,
                "is_simulation": bool(getattr(event, "is_simulation", False)),
                "trigger_source": getattr(event, "trigger_source", "fusion"),
            }
```

- [ ] **Step 4: Add the config keys (both files)**

In `edge_glass/config.yaml`, replace the `trigger:` block with:

```yaml
# Trigger engine settings
trigger:
  correlation_window_seconds: 2       # Correlation window for visual+audio fusion; also the visual-only dwell duration
  cooldown_seconds: 30                # Cooldown period between events (shared by fusion + visual-only events)
  visual_only_fallback: false         # Opt-in per node. When audio has NO stream (audio.enabled=false / init failure /
                                      # no device), allow the camera to trigger alone. Off = today's AND-only behavior.
  visual_only_confidence_multiplier: 1.5  # Solo confidence bar = visual.edge_density_threshold × this. Tune on a real
                                          # audio-disabled node before enabling the fallback.
```

In `edge_glass/config.zeabur.yaml`, replace the `trigger:` block with:

```yaml
trigger:
  correlation_window_seconds: 2
  cooldown_seconds: 30
  visual_only_fallback: false             # 純視覺回退（每節點選用）；音訊無串流時允許相機單獨觸發
  visual_only_confidence_multiplier: 1.5  # solo 信心門檻 = visual.edge_density_threshold × 此值
```

- [ ] **Step 5: Verify — compile, YAML parse, full suite**

```bash
cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -c "import ast; ast.parse(open('edge_glass_main.py', encoding='utf-8').read()); print('main OK')"
cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -c "import yaml; yaml.safe_load(open('config.yaml', encoding='utf-8')); yaml.safe_load(open('config.zeabur.yaml', encoding='utf-8')); print('yaml OK')"
cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/ -q
```

Expected: `main OK`, `yaml OK`, and the full edge_glass suite green. Controller also diff-inspects that (a) the construction moved after audio init, (b) `audio_available` derives from `audio_stream is not None`, (c) the multiplier feeds the solo threshold.

- [ ] **Step 6: Commit**

```bash
git add edge_glass/edge_glass_main.py edge_glass/config.yaml edge_glass/config.zeabur.yaml
git commit -m "feat(edge_glass): wire visual-only fallback into main + config keys"
```

---

### Task 5: Whole-branch review + gate + finishing

Not a code task — the closeout. This is the one stage that fans out in parallel (independent read-only review lenses).

- [ ] **Step 1: Whole-branch review.** Dispatch parallel review lenses over the branch diff (`git diff main...HEAD`): (a) correctness/semantics of the solo gate vs the spec's exact fire condition; (b) zero-regression audit (AND path + `force_trigger` byte-equivalent behavior); (c) banned-string + dependency screen; (d) config/wiring consistency. Collect written findings.
- [ ] **Step 2: Fix wave.** Address any confirmed findings under the same strict-TDD discipline (new RED test for any behavior fix).
- [ ] **Step 3: Gate.** `cd edge_glass && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 /c/Python314/python -m pytest tests/ -q` → all green, including the untouched existing `test_trigger_engine.py` classes (the regression proof). Record the count.
- [ ] **Step 4: Banned-string sweep** over the full diff: confirm the credential/broker literals (`Msc@***` / `MSC-***` / the EMQX public broker) appear nowhere.
- [ ] **Step 5: Finishing.** Invoke `superpowers:finishing-a-development-branch`. Present the merge menu; **await the user's literal "approved"** before any `origin/main` push.

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Fusion rule / two regimes → Task 3 (`_check_visual_only` + restructured `evaluate`). ✓
- Dwell continuity semantics (triggered→start, False→clear, None→unchanged) → Task 2 (tracking) + Task 3 (`test_none_gap...`, `test_triggered_false_breaks_dwell`). ✓
- Exact fire condition (5 clauses) → Task 3 `_check_visual_only` + cooldown in `evaluate`. ✓
- Raised bar with no detector change → Task 3 reads `visual_result.confidence`; threshold computed in Task 4 wiring. ✓
- Config surface (2 keys) → Task 4. ✓
- Wiring (construct after audio init; `audio_available = audio_stream is not None`) → Task 4. ✓
- Event provenance (`trigger_source`, `event_metadata`) → Task 1 + Task 4. ✓
- `force_trigger` keeps `"fusion"` → Task 1 (default field; `force_trigger` build unchanged). ✓
- Safety invariants (fail-closed, disabled-only boot-fixed, regression-proof, shared cooldown) → Task 3 gates + `audio_available` boot value + shared `_check_cooldown`. ✓
- Testing (8 cases, `current_time` injection) → Tasks 1–3 cover all eight. ✓

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"; every code step shows real code. ✓

**3. Type consistency** — `trigger_source: str` (Task 1) read via `getattr(event, "trigger_source", "fusion")` (Task 4); `audio_available: bool` / `solo_confidence_threshold: Optional[float]` (Task 3 signature) called with the same kwargs (Task 4); `_visual_run_start: Optional[float]` (Task 2) read in `_check_visual_only` (Task 3). Consistent. ✓
