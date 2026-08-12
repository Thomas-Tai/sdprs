# Weather rainfall reconciliation — SDD plan

**Spec:** `docs/superpowers/specs/2026-08-06-weather-rainfall-reconcile-design.md`
**Branch:** `feat/weather-rainfall-reconcile-2026-08-06` (worktree off `main` `5edb204`).
**Method:** Subagent-Driven Development. Opus controller orchestrates; Sonnet/Haiku subagents make edits; controller verifies each diff + RED→GREEN itself (A/B revert) before marking a task complete. One final whole-branch review → fix wave → finishing-a-development-branch.

Ordering is TDD-sound: the dataclass foundation (the new field + merge wiring) lands first so every fetcher task can reference `rainfall_rate_mmh` and go RED against a real field.

---

## Task 1 — Dataclass + merge + wire-contract foundation
**Files:** `central_server/services/weather_service.py`, tests `central_server/tests/test_hko_and_merge.py`.
**Change:**
- Add `rainfall_rate_mmh: Optional[float] = None` to `CurrentWeather` (with the Option-D fields).
- Add `"rainfall_rate_mmh"` to `_MERGEABLE_FIELDS` (adjacent to `rainfall_24h_mm`).
- In `merge_currents`, add `rainfall_rate_mmh=merged_values.get("rainfall_rate_mmh")` to the returned `CurrentWeather(...)`.
- Update the `_cur` test factory default (add `rainfall_rate_mmh=None`) so existing merge tests still construct.

**RED tests (added to `test_hko_and_merge.py`):**
- `merge` picks `rainfall_rate_mmh` per-provider (SMG supplies rate, Open-Meteo supplies daily → merged has both, each labeled from the right source).
- A candidate that supplies `rainfall_rate_mmh` but not `rainfall_24h_mm` leaves `rainfall_24h_mm` unlabeled in `merged.sources`.
- Wire-contract: `from central_server.api.weather import _serialize` → `_serialize(CurrentWeather(... rainfall_rate_mmh=1.2 ...))` yields a dict containing **both** `"rainfall_rate_mmh"` and `"rainfall_24h_mm"`. (Goes RED before the field exists on the dataclass; GREEN after — proves the endpoint payload carries both.)

**Controller RED proof:** remove the `rainfall_rate_mmh` field / the `_MERGEABLE_FIELDS` entry → tests fail; restore → pass.

## Task 2 — SMG parser: select `<Rainfall>` by explicit `<Type>`
**Files:** `central_server/services/weather_service.py` (`_fetch_smg_current`), tests `central_server/tests/test_smg_xml_parser.py`.
**Change:** stop using `_get_float('Rainfall/Value')` (first-element). Iterate `station.findall('Rainfall')`, read each `<Type>`, and map Type 3 → `rainfall_rate_mmh`, Type 5 → `rainfall_24h_mm`. Missing/empty/sentinel values fall back (rate → `None`; daily → `0.0` default, unlabeled). Populate `sources['rainfall_rate_mmh']` / `sources['rainfall_24h_mm']` only for the types actually present with a real value. Add a module constant documenting the Type→field mapping (single point of change per spec's follow-up).
**RED tests:**
- Extend `SMG_XML_FIXTURE` so 外港 has all three `<Rainfall>` types with **distinct** values (e.g. Type 3 = 2.5, Type 4 = 6.0, Type 5 = 8.0).
- `rainfall_rate_mmh == 2.5` (Type 3), `rainfall_24h_mm == 8.0` (Type 5) — the Type-4 value must NOT leak into either.
- Both source keys present + labeled `SMG 外港`.
- A station with only Type 3 (no Type 5) → `rainfall_rate_mmh` set, `rainfall_24h_mm` unlabeled (not in `sources`).
- Update the existing `test_smg_parses_station_readings...` assertion that currently checks `rainfall_24h_mm == 2.5` to the new semantics.

**Controller RED proof:** revert to `_get_float('Rainfall/Value')` → the Type-5 daily assertion fails (gets the Type-3 value); restore → pass.

## Task 3 — Open-Meteo: add `daily=precipitation_sum`
**Files:** `central_server/services/weather_service.py` (`_fetch_openmeteo_current`), **new** test `central_server/tests/test_openmeteo_current.py`.
**Change:** add `"daily": "precipitation_sum"` to the params. Store current-hour `precipitation` → `rainfall_rate_mmh` (+ `sources['rainfall_rate_mmh']`); `daily.precipitation_sum[0]` → `rainfall_24h_mm` (+ `sources['rainfall_24h_mm']`). Keep the existing nullable/absent-key discipline (missing daily key ⇒ `rainfall_24h_mm` default, unlabeled). Update the stale "approximate 24h by multiplying" comment.
**RED tests (new file, mirrors the `_FakeClient` pattern):**
- Fixture with `current.precipitation = 1.5` and `daily.precipitation_sum = [12.0]` → `rainfall_rate_mmh == 1.5`, `rainfall_24h_mm == 12.0`, both labeled `Open-Meteo (...)`.
- Missing `daily` block → `rainfall_24h_mm` unlabeled; rate still set.

**Controller RED proof:** drop the `daily` param handling → the `rainfall_24h_mm == 12.0` assertion fails; restore → pass.

## Task 4 — HKO: past-hour → rate
**Files:** `central_server/services/weather_service.py` (`_fetch_hko_current`), tests `central_server/tests/test_hko_and_merge.py`.
**Change:** the past-hour district max now populates `rainfall_rate_mmh` and is labeled under `sources['rainfall_rate_mmh']` (was `sources['rainfall_24h_mm']`). HKO does **not** claim `rainfall_24h_mm` (no native daily). Keep the "omit source when no district data" guard.
**RED tests (update in `test_hko_and_merge.py`):**
- `test_hko_parses_selected_temperature_station`: assert `rainfall_rate_mmh == 3.5` and `'rainfall_rate_mmh' in sources`, and `'rainfall_24h_mm' not in sources`.
- `test_hko_omits_rainfall_source_when_no_district_data`: assert `'rainfall_rate_mmh' not in sources`.

**Controller RED proof:** leave HKO writing `sources['rainfall_24h_mm']` → the `rainfall_rate_mmh` source assertion fails; apply → pass.

## Task 5 — SPA `api.jsx mapWeather`
**Files:** `central_server/static/spa/api.jsx`, **new** render suite `tools/spa/render_extra/weather-rainfall.js`.
**Change:** `rain.now = roundOrNull(current.rainfall_rate_mmh)`; `rain.day = roundOrNull(current.rainfall_24h_mm)`. Replace the "keep now/hour null" comment block with the honest mapping (both fields now exist on the wire). `rain.hour` stays `null`.
**RED test (new suite, `target: 'api.jsx'`, drives `SDPRS_API.loadWeather` over stubbed `fetch` — mirrors `wxa004-lightning.js`):**
- `/api/weather/current` stub returns `rainfall_rate_mmh: 2.5, rainfall_24h_mm: 40, sources: { rainfall_rate_mmh: 'SMG 外港', rainfall_24h_mm: 'SMG 外港' }`.
- Assert `w.rain.now === 2.5`, `w.rain.day === 40`, and the sources ride through.

**Controller RED proof:** the suite fails on current `api.jsx` (`rain.now` hardcoded null); passes after the edit.

## Task 6 — SPA `pages/weather.jsx` rain tile
**Files:** `central_server/static/spa/pages/weather.jsx`, `tools/spa/render_extra/weather-rainfall.js` (append page suite).
**Change:** hero shows `rain.day` "mm/24h" (unchanged markup, now truthful) with `SourceChip label={sources.rainfall_24h_mm}`. Live-rate line binds `rain.now`: `即時 {rain.now} mm/h`, colored `rainColorClass(rain.now)` when non-null, plus `SourceChip label={sources.rainfall_rate_mmh}`. The "無即時雨量資料來源" copy stays only as the `rain.now == null` fallback. Rewrite the WHA-M6 comment block to reflect that a live rate now exists.
**RED tests (append, `target: 'pages/weather.jsx'`):**
- `WEATHER.rain = { day: 40, now: 2.5, hour: null }`, `sources: { rainfall_24h_mm: 'SMG 外港', rainfall_rate_mmh: 'SMG 外港' }` → tile shows `即時 2.5 mm/h` (not "無即時雨量資料來源"), and `40` + `mm/24h`.
- `WEATHER.rain = { day: 40, now: null }` → live line shows "無即時雨量資料來源" (genuine fallback).
- Source chip for the rate line renders its label.

**Controller RED proof:** the populated-rate assertion fails on current `weather.jsx` (always "無即時..."); passes after the edit.

## Task 7 — Final review → gate → finishing-branch
- Opus whole-branch review of the full diff (correctness, discipline, no banned strings, no scope creep).
- Fix wave for any findings; scoped re-review.
- Full gates: `node tools/spa/run_all.js` (SPA) green; backend `pytest` (per-suite trap: `test_lightning_lifespan` / `test_node_allowlist` pass in isolation) green.
- `finishing-a-development-branch`: verify tests → present the 3-option menu → **await literal "approved" before any origin/main push**.

---

### Notes
- The `/api/weather/current` endpoint needs **no** production change (auto-serialized via `asdict()`); Task 1's contract test guards it.
- `test_hko_and_merge.py`'s `_cur` factory must gain a `rainfall_rate_mmh=None` default (Task 1) so it keeps constructing valid `CurrentWeather` objects.
- Watch the pytest per-suite trap: run the two flaky-in-aggregate suites in isolation to confirm they're green (shared-state artifact, not a regression).
