# Weather rainfall reconciliation — design

**Date:** 2026-08-06
**Track:** Track 2 of the 4-track backlog sweep ("Weather UI"). This is the *non-key-blocked* slice — it needs no `CWA_API_KEY` (that gates the separate Taiwan-CWA typhoon path, deferred).
**Branch:** `feat/weather-rainfall-reconcile-2026-08-06` (worktree off `main` `5edb204`).

## Problem

The backend `CurrentWeather.rainfall_24h_mm` field is **mislabeled**. Every fetcher feeds it an *hourly* value, not a 24-hour cumulative:

- `_fetch_smg_current` calls `_get_float('Rainfall/Value')` — `findtext` returns the **first** `<Rainfall>` element, which is SMG's **Type 3** (current-hour rate). The parser's own comment already says so ("Type 3 (hourly) is the instantaneous rate").
- `_fetch_openmeteo_current` stores the **current-hour** `precipitation`.
- `_fetch_hko_current` stores the **past-hour** max across districts.

The SPA then treats this hourly number as a daily total: `api.jsx mapWeather` sets `rain.day = rainfall_24h_mm` and hard-codes `rain.now = null`. The weather tile renders it as a big **"mm/24h"** hero, while the live-rate line permanently reads **"無即時雨量資料來源"** ("no live rainfall source"). During a rainstorm this is doubly wrong: the "24h total" is actually the current hour, and the tile claims no live-rate source exists — even though that hourly value *is* exactly a live rate.

## Live-data verification (2026-08-06)

Fetched `https://xml.smg.gov.mo/c_actualweather.xml` (17 stations). Each rainfall-instrumented station (**10 of 17**; the 7 bridge/wind-only stations omit rainfall) emits **exactly three `<Rainfall>` elements**, distinguished by `<Type>`:

```xml
<Rainfall><MeasureUnit>mm</MeasureUnit><Type>3</Type><Value>0.0</Value><dValue>0.0</dValue></Rainfall>
<Rainfall><MeasureUnit>mm</MeasureUnit><Type>4</Type><Value>0.0</Value><dValue>0.0</dValue></Rainfall>
<Rainfall><MeasureUnit>mm</MeasureUnit><Type>5</Type><Value>0.0</Value><dValue>0.0</dValue></Rainfall>
```

Distinct `<Type>` values across the whole feed: `{3, 4, 5}`.

**Type→semantic mapping (SMG schema convention):**
- **Type 3** = current-hour rainfall → the honest **live rate (mm/h)**.
- **Type 4** = intermediate accumulation (unused here).
- **Type 5** = **daily total** (since local midnight) → the honest **24h total (mm)**.

> **Documented assumption.** The verification day was dry (all values 0.0), so Types 3/4/5 could not be distinguished *empirically* by magnitude on 2026-08-06 — the mapping rests on SMG's published schema convention, not on observing `daily > hourly`. This is a strict improvement regardless: the parser stops mislabeling the 1-hour element as 24h, and the Type→field mapping is a single documented constant that can be re-validated during the next rain event. **Re-validate during a rain event** (see "Follow-up validation" below).

A genuine daily total is therefore **natively available** — SMG Type 5 and Open-Meteo `daily=precipitation_sum` — with **no rolling-accumulation store and no new DB tables**. This delivers a true 24h total with less risk than server-side accumulation.

## Approach

Replace the one mislabeled field with **two honest fields**:

| Field | Meaning | SMG | Open-Meteo | HKO |
|---|---|---|---|---|
| `rainfall_rate_mmh` *(new, `Optional[float]`)* | live intensity, mm/h | Type 3 | current `precipitation` | past-hour district max |
| `rainfall_24h_mm` *(kept, now genuine)* | daily total, mm | Type 5 | `daily=precipitation_sum` | *(no native daily → not supplied)* |

`rainfall_rate_mmh` is `Optional[float] = None` on the dataclass, matching the Option-D idiom for `pressure_hpa`/`visibility_km`: absent-from-`sources` means "no provider supplied it", so the SPA renders "—"/the no-source line rather than a fake 0. Both fields join `_MERGEABLE_FIELDS` and are picked per-provider by `merge_currents` exactly like every other field. Each fetcher labels the fields it supplies in its `.sources` dict under the field-name keys `rainfall_rate_mmh` / `rainfall_24h_mm`.

HKO has no native daily accumulation, so it supplies only `rainfall_rate_mmh` (its past-hour max is effectively mm/h) and does **not** claim `rainfall_24h_mm`. When SMG/Open-Meteo are both unavailable and only HKO has rain data, the 24h hero honestly shows "—".

The `/api/weather/current` endpoint serializes the dataclass via `asdict()`, so the new field flows to the wire automatically — the API task is a **contract guard test**, not a code change.

### SPA
- `api.jsx mapWeather`: `rain.now ← current.rainfall_rate_mmh`, `rain.day ← current.rainfall_24h_mm` (both `roundOrNull`). Retire the "keep now/hour null" comment/behavior.
- `pages/weather.jsx` rain tile: hero keeps the 24h total (**now truthful**) with `SourceChip label={sources.rainfall_24h_mm}`; the live-rate line binds `rain.now` — colored via `rainColorClass(rain.now)` (mm/h thresholds 30/10), shows `即時 {rain.now} mm/h`, and a `SourceChip label={sources.rainfall_rate_mmh}`. The permanent "無即時雨量資料來源" becomes the genuine no-source fallback (only when `rain.now == null`).

## Non-goals / deferred
- **CWA typhoon path** (`_parse_typhoon_warning`, the `w.typhoon` hero) — external-dependency-blocked on a real `CWA_API_KEY`. Untouched.
- **HKO wind** ("Phase 2") — Open-Meteo already covers wind. Untouched.
- **Server-side rolling accumulation** — explicitly avoided; native daily totals make it unnecessary.
- **`rainfall_hour_mm`** (a distinct 1h-vs-instantaneous split) — `rain.hour` stays `null`; SMG Type 3 already *is* the ~1h rate, so `rain.now` covers the live signal. No separate hour bucket is invented.

## Invariants / discipline
- Naive-UTC only via `central_server.timeutil.utcnow()`; no tz-aware datetimes.
- zh-TW for all user-facing strings.
- No new npm deps; SPA source stays no-import / no-build (in-browser Babel).
- Banned strings (`Msc@2333`, `MSC-Person`, `broker.emqx.io`) must never appear.
- Strict TDD: every task's test fails RED before the change, passes GREEN after; controller A/B-verifies.
- Nothing reaches `origin/main` without the literal "approved".

## Follow-up validation (post-merge, no code)
Re-sample the SMG feed **during a rain event** and confirm `Type 5 ≥ Type 3` (daily ≥ current-hour) at a wet station, closing the dry-day assumption above. If SMG's Type semantics differ from the convention, adjust the two `<Type>` constants in `_fetch_smg_current` (single-line change, pinned by `test_smg_xml_parser.py`).
