# SDPRS Migration Notes

Operator-facing migration guidance for breaking or fail-closed changes.
Read before deploying an update that crosses one of the dated entries below.

---

## 2026-08-27 — SECURITY: platform credential exposure, full rotation required

### What happened

The hosting provider disclosed that project **environment-variable data was
retrieved by an attacker**. This was a platform-side incident, not a
vulnerability in this codebase — there is no code fix to apply, but every
credential the deployment held must be treated as compromised.

Exposure window: **2026-08-27 → 2026-09-01** (disclosure to rotation).

The provider's notice named only the variables matching its own scanned
key-name and value-format patterns. **Treat that list as a floor, not a
ceiling** — it did not include `MQTT_PASSWORD`, whose leak mattered most
here because the broker is reachable from the public internet via TCP proxy
(anonymous access is disabled, so the password was the only control).

### What was done on 2026-09-01

Eight credentials were rotated and confirmed working: the dashboard
password, `SECRET_KEY`, the shared `EDGE_API_KEY`, `MQTT_PASSWORD`, and the
Postgres role password, plus related values the notice did not name.

Post-incident audits found **no evidence of exploitation**:

- Database: no writes, modifications, or deletions during or after the
  window; node registrations all expected; no attacker-created Postgres
  role (only `root` can log in, everything else is a built-in `pg_*`);
  Postgres uptime unbroken.
- GitHub: zero commits during the window, no webhooks, no deploy keys,
  no unexpected collaborators. The `edge-release` supply-chain path is
  intact.
- Full git-history secret scan: no live credential has ever been
  committed. Two historical values were found, both already removed the
  same day they were added (2026-03-28) and rotated twice since.

**MQTT broker activity during the window is permanently unverifiable** —
mosquitto logs to the container's stdout with no mounted volume, so they
did not survive the restart. See "Remaining hardening" below.

### Migration for existing deployments

**Edge nodes are the blocking item.** Nodes authenticating with the shared
`EDGE_API_KEY` still hold the pre-rotation value and **cannot authenticate
until re-keyed**. Nodes migrated to per-node keys are unaffected — those are
stored server-side as `api_key_hash`, never in plaintext, and were not part
of the exposure.

For each node still on the shared key, update `EDGE_API_KEY` and
`MQTT_PASSWORD` in its on-node config and restart the service.

If you are rotating these credentials yourself, three ordering traps matter:

1. **Postgres.** `POSTGRES_PASSWORD` is read by the image **only on first
   init with an empty `PGDATA`**. Changing the environment variable alone
   desyncs the config from reality: the app breaks and the leaked password
   stays valid. Correct order is `ALTER USER <role> WITH PASSWORD ...`
   inside the running container, *then* update the environment variable,
   *then* restart the server.

2. **`DATABASE_URL` is a reference chain**
   (`postgresql://${POSTGRES_USERNAME}:${POSTGRES_PASSWORD}@...`) and
   resolves automatically. **Never hand-edit it** — doing so pins a stale
   password that survives the next rotation.

3. **`MQTT_PASSWORD` lives in two services.** It must match across the
   server and the broker. Update both together and restart the broker
   first — its entrypoint regenerates the password file from the
   environment on every container start.

### Impact of rotation

- **`SECRET_KEY`** — invalidates every session cookie; all operators
  re-authenticate.
- **`EDGE_API_KEY`** — breaks every node still on the shared key until
  re-keyed. Per-node-key nodes are unaffected.
- **`MQTT_PASSWORD`** — breaks any node or client with the old value.
- **Dashboard password / Postgres role password** — no cascade beyond the
  login itself.

### Verification

Server startup should show settings validation passing, the database
initialising, and no authentication errors. Confirm each re-keyed node
reappears on the dashboard and resumes heartbeating.

### Remaining hardening

- **Mount a volume for the broker's logs.** Without one, connection logs
  die with the container, which is why this incident's MQTT activity could
  not be reconstructed. This is the single change that would most improve
  the next investigation.
- **Confirm whether the database is publicly exposed** (provider dashboard,
  networking settings). Given the broker is public, do not assume it isn't.
- **Consider retiring the shared `EDGE_API_KEY` entirely** once every node
  is on a per-node key — it is the only credential in this set whose
  rotation requires touching hardware.

### A note on where credentials are kept

Rotation records containing plaintext credentials must not be written into
a cloud-synced directory. Beyond the obvious exposure, **cloud storage
retains previous file versions and a recycle bin**, so redacting or
deleting such a file locally does not remove the credentials from the
service — they must also be purged from version history. Use a password
manager instead.

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
