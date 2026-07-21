# Webcam Client — Manual Bench-Test Checklist (real hardware)

**Why this exists.** Every automated test for the webcam client mocks the camera, ffmpeg, and
the network. The real 1Hz-JPEG→ingest→dashboard path and the on-demand HLS live stream have
**never been exercised on hardware**. This checklist is the manual verification that closes that
gap. Run it once on a real Windows PC with a real USB webcam before treating the feature as
field-proven. Each step names the invariant it proves.

## 0. Prerequisites
- [ ] Central server running and reachable (`uvicorn main:app` or the deployed instance); note its URL.
- [ ] A **Windows** PC with a working USB webcam (Device Manager shows it, no other app holding it).
- [ ] **ffmpeg** on that PC's PATH (`ffmpeg -version` succeeds) — required ONLY for the HLS live
      stream (step 6); 1Hz snapshots work without it.
- [ ] Client deps installed (`pip install -r webcam_client/requirements.txt`) OR the packaged
      `SDPRS_Webcam.exe` (`pyinstaller build.spec`).
- [ ] A dashboard operator login.

## 1. Create the webcam client (dashboard) — proves the admin API
- [ ] Dashboard → 系統狀態 (Status) → **新增 Webcam Client** → enter a name → 建立.
- [ ] The API key is shown **once** in a modal that stays until dismissed (not a 3s toast). Copy it.
- [ ] EXPECTED: a Node ID is returned; the key is a long random token (NOT a memorable string).

## 2. First-run setup wizard — proves config + DPAPI + preview (audit M2/M8)
- [ ] Run `python -m webcam_client.main` (or `SDPRS_Webcam.exe`). The setup wizard opens.
- [ ] 掃描攝影機 lists the webcam with resolution; a **live preview thumbnail** appears for it
      (audit M2), and there is a **per-camera name** field (audit M2).
- [ ] Paste Server URL + API Key, name the camera, 開始.
- [ ] EXPECTED: wizard closes, tray icon appears (green).
- [ ] **DPAPI-at-rest (audit M8):** open the on-disk config file (e.g.
      `%LOCALAPPDATA%\...\config.json` — check `config.py` for the path). Confirm it contains
      `api_key_encrypted` (base64) and **NO plaintext `api_key`**.

## 3. 1Hz snapshots on the Monitor Wall — proves the C1 primary path
- [ ] Monitor Wall shows a tile for the new node with a blue **Webcam** badge (not grey "Edge Cam").
- [ ] The JPEG frame updates ~once per second; waving at the camera changes the frame within ~1-2s.
- [ ] EXPECTED: this is the 99% path. If the tile stays blank while the tray is green, the C1
      auth path is broken — check the client log for a 401 WARNING (the fix makes 401s loud).

## 4. Status page correctness — proves the status.jsx webcam-awareness fix
- [ ] 系統狀態 table: the webcam row's 類型 shows **Webcam** (NOT 「抽水站」).
- [ ] 電源 shows 「—」 (NOT "PoE"); 溫度/水位 shows 「—」 with no 「水位資料未上傳」 water-level lie.
- [ ] Cycle the 類型 filter — it reaches a **Webcam** option and filters to it.

## 5. Node type end-to-end / freshness
- [ ] Stop the client (tray → 離開). Within ~90s the tile goes **OFFLINE** on the wall.
- [ ] Restart the client — it reconnects and the tile returns to fresh.

## 6. On-demand HLS live view — proves the lease + control channel + renewal
- [ ] Restart the client. On the tile click **▶ 即時**.
- [ ] EXPECTED: 連線中… then live H.264 video within a few seconds (the readiness poll waits for a
      real segment, up to 30s, instead of a blind timer).
- [ ] **Lease renewal (the final-review blocker fix):** keep watching for **at least 2.5 minutes**.
      The stream must NOT drop at ~90s. (Without renewal it would be force-stopped ~90-120s in.)
- [ ] Click **● LIVE ✕** → returns to snapshot mode; the client's ffmpeg stops (check client log).

## 7. Forgotten-tab reclaim — proves H1/H2 (lease expiry force-stops the client)
- [ ] Start a live view again, then **close the browser tab** (or navigate away) WITHOUT clicking stop.
- [ ] Watch the client log: within ~90-120s it should receive a `stream_stop` command and stop
      ffmpeg on its own. EXPECTED: the field PC's uplink is reclaimed — no stream runs forever.

## 8. Tray pause — proves the pause hook (this follow-up)
- [ ] Tray icon → **暫停推送**. On the wall, the tile's frame STOPS updating (snapshots suspended).
- [ ] Tray → **恢復推送** (resume). Frames resume within ~1-2s.
- [ ] EXPECTED: pause genuinely suspends uploads (previously it was a no-op).

## 9. Delete affordance — proves the delete endpoint (this follow-up)
- [ ] 系統狀態 → on the webcam row click **刪除** → confirm in the dialog.
- [ ] EXPECTED: the node disappears from the Status table AND the Monitor Wall (via `node_deleted`).
      The client keeps running but its pushes now 4xx (its cameras were removed) — check the log
      shows loud WARNINGs, not a crash or silent spin.

## 10. Resilience / security spot checks
- [ ] Stop the server while the client runs: the client logs WARNINGs (raise_for_status) and does
      NOT crash or busy-spin; restart the server → pushes resume.
- [ ] **Key isolation:** confirm the per-client key cannot hit an edge endpoint — e.g.
      `POST /api/edge/<id>/snapshot/...` with the webcam `X-API-Key` returns 401 (webcam keys are
      only valid on `/api/webcam/*`).
- [ ] Grep the client log for the API key / any password — none should appear in plaintext.
- [ ] **DPAPI cross-user:** copy the client config file to a different Windows user account and
      launch there → decryption fails → the client treats it as **unconfigured** (shows the setup
      wizard) and NEVER falls back to a plaintext key.

---
**Result:** ______ / all boxes. Log the date, PC, webcam model, and any deviation. File any
failure as a bug against `docs/superpowers/plans/2026-07-21-webcam-client.md`. Automated suites
being green does NOT substitute for a clean run of this checklist.
