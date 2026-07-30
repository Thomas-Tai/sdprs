# Delta Audit Handoff — Double-Check Brief for Next Session

**Created:** 2026-07-26
**Purpose:** Enable a fresh session to independently verify the delta audit results (verification verdicts + 35 new findings)
**Parent report:** `sdprs/docs/audits/full_dashboard_audit_2026-07-26.md` (184 findings)
**Prior handoff:** `sdprs/docs/audits/full_dashboard_audit_2026-07-26_HANDOFF.md` (v2, covers the original audit's verification pass)

---

## 1. What Was Done in This Session

| Phase | What | Result |
|-------|------|--------|
| Verification | 3 parallel agents re-read code for all ~157 unverified Medium/Low findings from the parent report | 142 CONFIRMED, 13 PARTIAL, 2 REFUTED |
| Fresh-eyes hunt | 3 parallel agents searched for issues the parent report missed | 35 NEW findings (1 High, 17 Medium, 17 Low) |

**Method:** Each verification agent was given the parent report section text + handoff skip-list, then independently located and re-evaluated each claim against current code. Fresh-eyes agents read the full parent report first and were instructed to report only non-duplicate issues.

---

## 2. File Locations

| File | Purpose |
|------|---------|
| `sdprs/docs/audits/full_dashboard_audit_2026-07-26.md` | Parent report — 184 findings |
| `sdprs/docs/audits/full_dashboard_audit_2026-07-26_HANDOFF.md` | Handoff for the parent report's own verification pass |
| **This file** | Handoff for the delta audit (verification + new findings) |
| `sdprs/central_server/static/spa/` | SPA source (app.jsx, api.jsx, data.jsx, components.jsx, tweaks-panel.jsx, icons.jsx, pages/*.jsx, styles.css, index.html) |
| `sdprs/central_server/` | Backend (main.py, api/*.py, services/*.py, database.py, templates/login.html) |
| `sdprs/central_server/static/css/styles.css` | Orphaned file (see NEW-UX-012) |

---

## 3. Verification Verdicts — What to Double-Check

### 3a. REFUTED findings (2) — verify these should be DELETED from the backlog

| ID | Claim in parent report | Why refuted | How to verify |
|----|----------------------|-------------|---------------|
| SHELL-008 | "markSeen POSTs on every selection change" | `api.jsx:918` markSeen → `_seenAdd(id)` is a local Set add with dedup guard (api.jsx:183-192). Zero POST calls exist. | `grep -n "markSeen\|_seenAdd" sdprs/central_server/static/spa/api.jsx` — confirm no fetch/POST in the chain |
| HDO-002 | "Handover dialogs: background keyboard-tabbable, no trap" | Both dialogs now have `role="dialog"` + `aria-modal="true"`, Escape (capture), autoFocus, focus-return, first/last Tab wrap trap (handover.jsx:28-42, 103-117, per WHA-M8 fix) | Read `sdprs/central_server/static/spa/pages/handover.jsx:28-42,103-117` |

### 3b. PARTIAL findings (13) — verify the narrowed impact notes are correct

| ID | Parent claim | Correction | Verify by |
|----|-------------|------------|-----------|
| DATA-015 | ws.onclose never inspects 1008, infinite reconnect loop | True that onclose doesn't check code, BUT server sends `auth_expired` JSON frame before every 1008 close and `app.jsx:629-635` tears down on it — loop is bounded | Read `app.jsx:629-635` + `services/websocket_service.py` auth_expired send |
| DATA-023 | OPERATORS_ONLINE "rendered as truth" | Roster is permanently empty (data.jsx:104-116), but `components.jsx:657` only renders OperatorsCluster when `operators.length > 1` — empty roster HIDES the cluster, doesn't display false data | Read `components.jsx:657` guard |
| SHELL-009 | Silent-swallow of unknown toast template keys | Path exists (app.jsx:1202-1227) but RESOLVE_TEMPLATES (data.jsx:10-17) statically has all 6 keys — currently unproducible | Check data.jsx:10-17 has keys 1-6 |
| SHELL-022 | "No nav escape hatch from error boundary crash loop" | Re-crash is click-driven (no auto loop); NavRail/StatusStrip render OUTSIDE the boundary (app.jsx:1550/1568) and boundary is `key={page}` (1447) so navigating away escapes | Read app.jsx:1447,1550,1568 structure |
| SHELL-027 | Sort comparator lacks NaN guard | True (app.jsx:811), but mapAlert coerces `secsSince(created) || 0` (api.jsx:215) — NaN unreachable via current data | Read api.jsx:215 |
| OPS-015 | "3 raw err?.message toast handlers" | Actually **4**: status.jsx lines 254, 511, 524, 558 | Count `err?.message` in status.jsx |
| OPS-016 | "Double-fire revoke → 404" | Native confirm + no busy latch + skipped onRefresh are real, but revoke rotates by clientId — second call likely succeeds (new key), not 404 | Read the revoke endpoint in api/nodes.py or webcam routes |
| OPS-021 | "Icon-only buttons lack accessible names" | Snooze/stream/key buttons now carry `title` attributes → accessible-name fallback exists | Check for `title=` on those buttons in status.jsx |
| OPS-026 | "Live-start double-fire race" | No ref latch (monitor.jsx:229-247), but React 18 discrete-event synchronous flush makes same-tick double-fire unreachable — narrow like HDO-001 | Same reasoning as verified HDO-001 |
| OPS-030 | "Dead render slots for flow/trend/cycleHistory" | flow/trend hardcoded null → permanent dash slots real (pumps.jsx:481-486); but cycleHistory IS render-guarded (status.jsx:272-281) | Read status.jsx:272-281 |
| OPS-037 | "Cross-file dependency breaks if load order changes" | Real dependency (fmtAgeOrDash defined monitor.jsx:27-29, used status.jsx:439/445), but index.html loads monitor.jsx (line 155) before status.jsx (156) and usage sites have fallbacks | Check script order in index.html |
| WXA-002 | "State-only double-submit on weather refresh" | Buttons carry `disabled={refreshing}` (weather.jsx:510/579) + React 18 flush → same-tick unreachable | Check disabled attrs |
| WXA-005 | "Lat/lon client-side bypass allows bad data" | Backend `WeatherConfigPayload` enforces `ge=-90 le=90` / `ge=-180 le=180` (api/weather.py:31-32) → 422 rejects; impact is bad UX not bad data | Read weather.py:31-32 |
| AUD-001 | "JSON error body downloaded as .csv" | exportAuditCsv now preflights `GET /api/audit?limit=1` and throws on 401/403 before anchor click (api.jsx:993-1026); only post-preflight failures (e.g. 500 on export.csv itself) still download error body | Read api.jsx:993-1026 |

### 3c. CONFIRMED findings (142) — spot-check sample

Full verdict tables were returned by the agents. If you suspect systematic error, spot-check these higher-impact ones:

- **DATA-010** — `database.py:1296-1317` creates a new SQLAlchemy engine per query (~29 call sites, 20s poll path)
- **DATA-024** — `main.py:402` throttle keys on `request.client.host`; no `--proxy-headers` in Dockerfiles → shared IP behind nginx
- **COMP-004** — HlsPlayer retryCount ref never resets on nodeId change (components.jsx:2276-2317)
- **OPS-013** — Every StreamRowButton mount fetches ALL stream health (status.jsx:~130, api.jsx:979-991, no cache/dedupe)
- **SHELL-002** — Inline `color:'#29261b'` on dark panel (app.jsx:1647-1653) → ~1.2:1 contrast
- **SHELL-017** — Wall-mode Escape handler (app.jsx:1398-1409) has no sessionExpired check; Esc exits wall mode with blocking modal up

---

## 4. NEW Findings — What to Double-Check (35 total)

### Priority verification targets (highest impact if wrong)

| ID | Sev | Claim | Key files to read | Falsify by |
|----|-----|-------|-------------------|------------|
| NEW-API-001 | Medium (arguably Critical) | PG deployments: snoozing any node → raw `datetime` from `operator_actions.timestamp` reaches `NodeStatus.snoozed_at: Optional[str]` → Pydantic ValidationError → 500 on entire node list | `database.py:363-371` (PG schema TIMESTAMP), `services/audit_service.py:232,248-260` (no str conversion), `api/nodes.py:107,288-290,356,377,643-645,679` | Find a str() coercion anywhere in the chain; or confirm Pydantic v2 lax mode DOES coerce datetime→str (it doesn't — lax coerces str→datetime, not the reverse) |
| NEW-UX-001 | High | Nested interactive controls inside `role="button"`: monitor.jsx:144-152 (NodeCard wraps buttons), status.jsx:387-394 (`<tr role="button">` wraps 4 buttons), pumps.jsx:363-367 | Those exact lines | Check if the inner elements are actually `<button>` or just styled divs |
| NEW-RT-001 | Medium | Late startWebcamStream rejection fires `.catch(() => setLiveMode('off'))` even after probe already promoted to 'live' — kills working HLS player, no stop call, no toast | `monitor.jsx:240-242` (catch), `monitor.jsx:109-131` (probe promotes to live) | Check if catch has a liveMode guard or if the probe's promotion makes the catch unreachable |
| NEW-RT-002 | Medium | Document-level Escape in alerts.jsx:428-441 clears bulk selection; fires BEFORE window-level drawer handlers (document precedes window in bubble phase); not gated by useOverlayTop | `alerts.jsx:428-441`, `components.jsx:1156-1163` (drawer Escape on window) | Verify event propagation order claim: for events dispatched on elements inside document, document listeners fire before window listeners in bubble phase — this is correct per DOM spec |
| NEW-UX-008 | Medium | Three fixed banners collide: NewAlertBanner `top-16 z-30` (components.jsx:1602-1612), dataWarnings `top-12 z-40` (app.jsx:1576), ShiftBanner `fixed top-14 right-4` (components.jsx:1634) | Those lines | Check z-index and offset values; check if any conditional prevents co-display |
| NEW-UX-003 | Medium | `n.bitrate < 0.5 ? 'text-sev-critical'` at status.jsx:450 — `null < 0.5` is `true` in JS | status.jsx:450 + api.jsx:353-358 (bitrate fallback `: 0` per DATA-020) | Note interaction: DATA-020 says bitrate falls back to 0 not null — if mapNode always yields a number, NEW-UX-003 may be unreachable. CHECK BOTH. |
| NEW-UX-014 | Medium | Audio-arming pill has `hidden md:inline-flex` (components.jsx:683-693) — mobile users never see un-armed state | components.jsx:683-693 | Verify the class and whether any other mobile indicator exists |

### Remaining new findings (lower priority to re-verify)

**Runtime (Low):**
- NEW-RT-004: HlsPlayer double-destroy — `hls.destroy()` in ERROR handler (components.jsx:~2300) AND in effect cleanup; no destroyed-flag guard
- NEW-RT-005: MuteDrawer open-effect resets inFlightRef latches (components.jsx:1122-1130) — reopen mid-flight disarms guard
- NEW-RT-006: SnoozeRowButton (status.jsx:52-72) state-only guard; sibling StreamRowButton (104-139) uses fixed ref-latch pattern
- NEW-RT-007: bootRanRef + cancelled cleanup (app.jsx:1002-1052) would blank dashboard under StrictMode (inert today — no StrictMode in index.html)

**API (Low):**
- NEW-API-002: Weather endpoints (api/weather.py:123-148, 151-164, 204-214) proxy live external fetches with no server-side throttle; only /login is rate-limited

**UX (Medium):**
- NEW-UX-002: `text-white` + `text-black` both applied on warn badge (monitor.jsx:160, 527) — winner depends on CDN Tailwind insertion order
- NEW-UX-004: Native `confirm()` for revoke (status.jsx:550) — freezes tab; same file's comment rejects confirm() for delete
- NEW-UX-005: Handover dialogs hardcode slate-950/900/700 palette (handover.jsx:43-73, 118-168) — dark in light theme
- NEW-UX-006: Floorplan SVG hardcodes dark fills (alerts.jsx:1226-1253) — near-white label invisible on light panel; 7px text
- NEW-UX-007: StatusStrip rain chip renders `{window.WEATHER.rain.now}` + "mm/h" (components.jsx:628-636) but mapWeather hardcodes rain null (api.jsx:492-493)
- NEW-UX-009: MuteDrawer w-[380px] (components.jsx:1288), NodeSidePanel w-[420px] (1983) — no max-w fallback
- NEW-UX-010: styles.css:196 focus rule omits `select`
- NEW-UX-011: login.html has no dark styling; SPA boots dark
- NEW-UX-012: `static/css/styles.css` referenced nowhere (grep proves zero imports); docs/architecture.md:167 falsely claims it styles login
- NEW-UX-013: Live controls ~22px (monitor.jsx:228-247, 254-271, `text-[10px]` + `px-2 py-1`)

**UX (Low):**
- NEW-UX-015: English on wall: "NOC WALL · v2.4" (~app.jsx:1747), "Live · Ns" (~1777), "+N more" (~1845/1876)
- NEW-UX-016: WallView counts raw `alerts` (~1855-1858) vs desktop `activeAlerts` memo filtering resolved (~1425)
- NEW-UX-017: "by {name}" English in zh copy (alerts.jsx:1104, 1175; api.jsx:232, 235)
- NEW-UX-018: Tab strips are plain buttons (alerts.jsx:516-521, 1001-1012) — no tablist semantics
- NEW-UX-019: History cards cursor-pointer + hover border (alerts.jsx:946) with no onClick
- NEW-UX-020: Audit page: `hover:text-slate-200` invisible on light (audit.jsx:236/249/262), ChevronDown on cycle-chips (230/243/256), raw JSON.stringify (350)
- NEW-UX-021: Unsnooze error in polite role=status (components.jsx:1425-1428); video unlabeled (2319-2327)
- NEW-UX-022: `cursor:default` on all tweaks controls (tweaks-panel.jsx:62, 108, 112, 130)
- NEW-UX-023: Logout avatar ChevronDown (components.jsx:710-719) opens confirm dialog, not dropdown
- NEW-UX-024: Hardcoded "build 2026.05.18-r4" + unbound green dot (components.jsx:840)
- NEW-UX-025: "Hotkey ${n.hotkey}" + raw kind enums in palette (components.jsx:1731, 1846)
- NEW-UX-026: Emoji glyphs (monitor.jsx:289), English chrome ("Webcam", "drops"), 14px checkboxes (alerts.jsx:76)

---

## 5. Known Interactions & Traps for the Verifier

1. **NEW-UX-003 vs DATA-020:** DATA-020 (confirmed) says `mapNode` falls back bitrate to `0` not null. If that's always true, `0 < 0.5` still yields critical-red — so NEW-UX-003's *mechanism* (null coercion) may be wrong but the *symptom* (red for no-data) is real via the 0 fallback. Verify which path actually fires.
2. **NEW-RT-002 event order claim:** The agent claims document listeners fire before window listeners in bubble phase. Per DOM spec this is correct (bubble goes innermost → document → window). But verify the drawer's Escape actually uses window (components.jsx:1163) and doesn't also stopPropagation at document level.
3. **NEW-API-001 Pydantic direction:** Pydantic v2 lax mode coerces str→datetime but NOT datetime→str. The finding depends on this asymmetry. If the project pins Pydantic v1, check v1 behavior (v1 also does not coerce datetime→str for `Optional[str]`).
4. **Line numbers:** The parent report's Medium/Low tables have NO per-finding line citations. All line numbers in this handoff come from the verification agents' code reads and should be accurate as of this session, but may drift after any code change.
5. **SHELL-003 correction:** The bug is real but the unreachable strip is ~(chrome−40)px device-dependent, not the fixed "50-90px" the parent report claims.
6. **DATA-026 caveat:** stream_health cache is per-process, but deploy Dockerfile pins `--workers 1` — latent, not live.

---

## 6. Suggested Verification Approach for Next Session

1. **Start with section 4's priority table** (7 findings) — highest impact if wrong
2. **Then spot-check 3a** (2 refuted) — if either is actually still valid, it goes back on the backlog
3. **Then sample 5-10 from 3c** — calibrate trust in the 142 bulk confirmations
4. **Remaining 3b/4 items** as time permits

**Estimated effort:** Priority table ~30 min; full pass ~2-3 hours.

---

## 7. Aggregate Numbers (for planning)

| Category | Count |
|----------|-------|
| Parent report findings still valid (confirmed + partial) | 155 of 157 checked |
| Parent report findings to DELETE | 2 (SHELL-008, HDO-002) |
| Parent report findings needing impact-note correction | 13 |
| New findings from this delta audit | 35 (1 High, 17 Medium, 17 Low) |
| **Total actionable backlog** | **~190** (155 corrected parent + 35 new) |
| Previously verified (parent handoff, no re-check needed) | 24 Critical/High + 8 Medium |
