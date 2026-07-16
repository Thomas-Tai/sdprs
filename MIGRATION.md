# SDPRS Migration Notes

Operator-facing migration guidance for breaking or fail-closed changes.
Read before deploying an update that crosses one of the dated entries below.

---

## 2026-07-16 — SECURITY: fail-closed credential validation

### What changed

`central_server/config.py:validate_settings` now runs at startup as a
**hard fail-closed check**. Prior behavior in `main.py` was to catch the
`ValueError` and only log a warning, so the app started even with
known-insecure defaults.

The app will now **refuse to start** if any of these are true:

- `DASHBOARD_PASS`, `EDGE_API_KEY`, or `SECRET_KEY` matches a known
  insecure placeholder value (in particular the `changeme-*` strings
  that older `scripts/setup_server.sh` wrote by default)
- Any of those three contains the substring `changeme`
  (case-insensitive)
- `SECRET_KEY` or `EDGE_API_KEY` is shorter than 32 characters
- `SECRET_KEY` or `EDGE_API_KEY` has fewer than 8 unique characters
  (catches values like `aaaaaaaa...`)
- `DASHBOARD_PASS` is shorter than 8 characters

`scripts/setup_server.sh` now generates cryptographically random
credentials on first run (via `openssl rand`) instead of writing
hardcoded `changeme-*` defaults.

### Why

Prior default configuration allowed anyone reaching a freshly
provisioned server to:

1. Log in as `admin` with the repo-known `changeme-strong-password`, AND
2. Forge valid Starlette session cookies (the signing key was also a
   known repo value)

That is a complete authentication bypass. Zeabur deployments were
affected only if the operator did not follow the deployment guide's
"generate a random 64-char hex key" recommendation.

### Migration for existing deployments

**Before your next redeploy or restart:**

1. Inspect your `/opt/sdprs/.env` (or Zeabur env vars):

   ```bash
   grep -E '^(DASHBOARD_PASS|EDGE_API_KEY|SECRET_KEY)=' /opt/sdprs/.env
   ```

2. If any value:
   - starts with `changeme-`
   - contains `changeme` anywhere
   - is shorter than 32 chars for `EDGE_API_KEY` / `SECRET_KEY`
   - is shorter than 8 chars for `DASHBOARD_PASS`

   then you **must** rotate before restart, or startup will fail with
   a clear error message in the logs.

3. Generate replacements:

   ```bash
   openssl rand -base64 24     # DASHBOARD_PASS (record before saving)
   openssl rand -hex 32        # EDGE_API_KEY
   openssl rand -hex 32        # SECRET_KEY
   ```

4. Update `.env`, then restart the server.

### Impact of rotation

- **`SECRET_KEY` rotation invalidates every existing session cookie.**
  All logged-in operators must re-authenticate. Not destructive to
  data, but user-visible.
- **`EDGE_API_KEY` rotation breaks every edge node until it is
  re-flashed with the new key.** If you rotate this, coordinate a
  fleet re-flash window. If the current key was a `changeme-*` default,
  this is a security emergency and worth the disruption.
- **`DASHBOARD_PASS` rotation** — only affects the login credential,
  no cascade effects.

### Verification

After redeploy, confirm startup succeeded (no `ValueError` in
`journalctl -u sdprs`) and that login works with the new
`DASHBOARD_PASS`.

Companion regression check from the earlier `security(nginx)` hotfix
(`9a35809`):

```bash
bash scripts/smoke_storage_auth.sh <host>
```

which asserts that the `/storage/` bucket is not publicly reachable.

### Related

- Commit `9a35809 security(nginx): remove /storage/ public alias — auth bypass`
- Companion tests: `central_server/tests/test_config_auth_settings.py`
- `central_server/tests/conftest.py` supplies strong default credentials
  for the whole test suite (needed because pre-existing per-file
  `os.environ.setdefault("*", "test-*")` values would fail the new
  validation when TestClient triggers the lifespan).
