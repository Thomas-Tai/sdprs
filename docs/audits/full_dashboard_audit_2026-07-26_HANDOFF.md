# Audit Handoff — Double-Check Brief for Next Session (v2, post-verification)

**Created:** 2026-07-26 (updated after verification pass)
**Purpose:** Enable a fresh session to double-check the 184 findings in `full_dashboard_audit_2026-07-26.md`
**Status:** All Critical/High findings (16) + 8 representative Mediums already independently verified — 0 refuted. Remaining ~160 Medium/Low findings are unverified.

---

## 1. Current State of the Audit

### What has been done

| Phase | What | Result |
|-------|------|--------|
| Phase 1: Audit | 6 parallel agents read all 11,858 lines of SPA + backend | 184 findings (3 Critical, 13 High, 61 Medium, 107 Low) |
| Phase 2: Verification | 4 parallel agents re-read code for all Critical/High + 8 Medium samples | 22/24 confirmed, 1 partially correct (COMP-008 downgraded), 1 confirmed-narrow (HDO-001), 0 refuted |

### What remains for YOU to double-check

The **~53 Medium** and **~107 Low** findings that were NOT individually re-verified. The 100% confirmation rate on the verified sample suggests high accuracy, but these are unchecked claims.

---

## 2. File Locations

| File | Purpose |
|------|---------|
| `sdprs/docs/audits/full_dashboard_audit_2026-07-26.md` | THE REPORT — all 184 findings with file:line evidence |
| `sdprs/docs/audits/full_dashboard_audit_2026-07-26_HANDOFF.md` | This document |
| `sdprs/central_server/static/spa/` | SPA source (app.jsx, api.jsx, data.jsx, components.jsx, tweaks-panel.jsx, icons.jsx, pages/*.jsx, styles.css, index.html) |
| `sdprs/central_server/` | Backend (main.py, api/*.py, services/*.py, templates/login.html) |
| `sdprs/docs/audits/dashboard_ui_audit_2026-07-20.md` | Prior audit (context for fix annotations like SHL-/CMP-/MSP-) |

---

## 3. Already-Verified Findings (no need to re-check unless you suspect error)

These 24 findings were independently confirmed by fresh agents re-reading the code:

**All CONFIRMED exactly as written:**
- AUTH-001 (logout GET→POST 405) — grep proves no GET logout route exists
- AUTH-002 (WS revalidation no-op) — test file corroborates: must monkeypatch to test
- AUTH-003 (no session-restore probe in expiry modal)
- AUTH-004 (GET /login shows form to authenticated users)
- SEC-001 (login_at never enforced) — every grep hit is WRITE or RETURN, never COMPARE
- FLOW-001 (single toast slot, no dismiss)
- DATA-001 (snooze never enforced) — every `snoozed_until` hit classified; zero enforcement reads; firmware has no subscriber
- DATA-002 (ack/resolve UPDATE lacks status predicate; bulk has it)
- DATA-004 (upload size bypass) — caveat: nginx.conf caps at proxy level for docker deploys
- DATA-008 (pump command success for offline nodes; stream start does 503)
- DATA-011 (mapAlert `|| 0` coercion; mapNode was fixed but mapAlert wasn't)
- OPS-001 (backdrop click destroys one-time key; delete modal HAS the guard)
- OPS-002 (bitrate column always 0/red; firmware confirmed to never publish bitrate)
- OPS-003 (HlsPlayer no fallback when hls.js missing)
- OPS-004 (silent live-start failure + silent 30s timeout; toast exists but unreachable from NodeCard)
- OPS-005 (🔑 button missing onKeyDown; all siblings have it)
- OPS-008 (pump confirm race; no re-check after arming awaitRef)
- COMP-001 (TweakRadio pointer-only; zero onKeyDown in entire file)
- COMP-002 (focus restore to detached autoFocus input; React commit order confirmed)
- SHELL-001 (no page validation anywhere; grep for VALID_PAGES/sanitize = zero hits)
- SHELL-004 (retry never reads back __SDPRS_LOAD_FAILURES; boot effect does)
- ALR-002 (onSnooze busy guard returns silently; menu closes as if success)

**CONFIRMED WITH ADJUSTMENTS:**
- COMP-008 → downgraded to Low: unguarded `.wind` access exists, but `mapWeather` (api.jsx:431) always constructs full `wind:{}`/`rain:{}` objects — crash not producible by current code
- HDO-001 → confirmed but narrow: state-only guard is real, but React 18 synchronous flush + `disabled={saving}` + server `expected_updated_at` token make real exploitation unlikely

---

## 4. How to Verify the Remaining Findings

### Protocol per finding

1. Open the file at cited line numbers — confirm code matches the "Evidence" snippet
2. Trace the control/data flow — does the described behavior actually occur?
3. **Critical step:** grep for the function/variable name across ALL files — a fix may exist elsewhere that the original auditor missed (this is the #1 false-positive risk)
4. Verdict: CONFIRMED / REFUTED / PARTIALLY CORRECT

### Priority order for your check (highest false-positive risk first)

These categories are most likely to have hidden mitigations:

**Tier A — Race conditions & timing claims (reasoning-based, hardest to verify):**
- OPS-026 (▶ 即時 same-tick double-fire) — check if React 18 batching prevents this like HDO-001
- WXA-002 (weather refresh double-click) — same question
- SHELL-006 (browser Back pollutes Alt+← stack) — trace both skipNext refs carefully
- COMP-015 (audio timestamps advance while muted) — trace the beep() call order

**Tier B — "X is never called / never happens" claims (grep-verifiable):**
- OPS-007 (no stopWebcamStream on unmount) — grep monitor.jsx for stopWebcamStream call sites
- DATA-015 (socket reconnects after 1008) — read openSocket onclose; check if app.jsx teardown covers all paths
- FLOW-002 (no pageshow listener) — grep entire SPA for "pageshow"
- FLOW-003 (no storage event listener) — grep entire SPA for "addEventListener('storage'" or `"storage"`
- SHELL-008 (markSeen fires on already-seen) — check if markSeen has an internal guard

**Tier C — Layout/visual claims (inferred from CSS, not pixel-tested):**
- OPS-012 (badge overlap at top-1/left-1 vs top-2/left-2) — verify z-index/DOM order
- OPS-017 (toast z-40 under modal z-50) — verify both z values in status.jsx
- SHELL-003 (100vh vs 100dvh) — verify both declarations exist as claimed
- COMP-021 (twk-panel z-90 vs logout z-[80]) — verify in tweaks-panel.jsx:50 and components.jsx:722

**Tier D — Accessibility claims (mechanical checks):**
- OPS-019/020/021/022 — verify role="button" nesting, missing aria-labels
- ALR-006/007, AUD-003, COMP-006/012/020 — verify missing ARIA attributes
- SHELL-012/013/014/015 — verify missing labels/tabindex/describedby

**Tier E — UX/data claims (read and judge):**
- Everything else (dead slots, placeholder metrics, i18n gaps, filter cycling, etc.)

### Useful grep starters

```bash
cd sdprs/central_server/static/spa

# Check for cross-tab sync (FLOW-003)
grep -rn "storage" *.jsx pages/*.jsx | grep -i "addEventListener"

# Check for bfcache handling (FLOW-002)
grep -rn "pageshow\|persisted" *.jsx pages/*.jsx

# Check markSeen internal guard (SHELL-008)
grep -n "markSeen" api.jsx app.jsx

# Check stopWebcamStream call sites (OPS-007)
grep -n "stopWebcamStream" pages/monitor.jsx api.jsx

# Check toast z-index (OPS-017)
grep -n "z-40\|z-50" pages/status.jsx

# Check encodeURIComponent consistency (DATA-018)
grep -n "webcam/" api.jsx
```

---

## 5. Known Limitations (things NO static audit can claim)

1. **No runtime testing was done** — timing races, rendering glitches, and browser-specific behavior are inferred, not observed
2. **Visual claims are CSS-inferred** — badge overlaps, truncation, contrast ratios calculated from hex values, not screenshots
3. **Edge firmware partially checked** — `edge_glass/stream/rtsp_server.py` was verified for OPS-002/DATA-001 only; full firmware audit out of scope
4. **Test suite not run** — `central_server/tests/` exists but was only read for corroboration (AUTH-002), never executed
5. **Line numbers may drift** — if anyone edits code before your check, cited lines may be off by a few; search by content not number

---

## 6. Suggested Workflow

```
1. Read the report's Executive Summary + Verification Pass section (10 min)
2. Tier A races (4 findings, ~30 min) — highest refutation risk
3. Tier B "never happens" claims (5 findings, ~30 min) — grep-fast
4. Tier C layout (4 findings, ~20 min)
5. Tier D accessibility (~12 findings, ~30 min) — mechanical
6. Tier E: sample 20-30% of remaining (~45 min)
7. Update report: tag findings [REFUTED]/[PARTIAL] with one-line reason
8. Optional: start server + browser for live spot-checks of OPS-012, OPS-017, SHELL-003
```

**Expected total: ~3 hours for thorough coverage of all unverified findings.**

---

## 7. If You Find Discrepancies

- Tag the finding in the report: `**[REFUTED]**` or `**[PARTIAL]**` + one-line explanation
- If a finding's severity should change, note the new severity
- Update the Executive Summary counts at the top
- Update Section 3 of this handoff document

---

## 8. Confidence Summary

| Finding range | Verification status | Confidence |
|---------------|--------------------:|------------|
| 3 Critical | 100% verified | Very high |
| 13 High | 100% verified | Very high |
| 8 Medium samples | 100% verified (1 downgraded, 1 narrowed) | High |
| ~53 remaining Medium | Unverified | Medium-high (extrapolating from sample) |
| ~107 Low | Unverified | Medium (UX/accessibility opinions + code smells; factual errors possible in line-number citations) |

*No code was modified during the audit or verification. Both phases were read-only.*
