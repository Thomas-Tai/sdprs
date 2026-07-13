# MQTT Broker Security — 設定指南

Hardening guide for the SDPRS Mosquitto broker. Covers why the default is a
trust gap, the two available modes, and the exact steps to switch to secure
mode. Related files in this directory: `mosquitto.conf`, `mosquitto_acl.conf`.

> **No secrets in this repo.** Nothing here — or in `mosquitto.conf` /
> `mosquitto_acl.conf` — contains a real password or hash. You generate
> credentials locally on the broker host; they must never be committed.

---

## Why (問題) — the current trust gap

The shipped `mosquitto.conf` runs with `allow_anonymous true` and **no
authentication and no ACL**. On that broker, *any* device that can reach TCP
1883 on the LAN can:

- **Spoof edge telemetry** — publish fake `heartbeat` / `pump_status` /
  `stream_status` on `sdprs/edge/#`, making the dashboard show bogus node state.
- **Send pump / stream commands** — publish to `sdprs/edge/<node>/cmd/*` and
  drive actuators or start/stop streams without going through the server.
- **Read everything** — subscribe to `sdprs/#` and observe all system traffic.

This is acceptable *only* for a trusted, isolated, single-node LAN MVP. It must
be closed before any multi-node or untrusted-network deployment, and the broker
must **never** be exposed to the WAN in this mode.

---

## The two modes (兩種模式)

| | Anonymous (MVP, current) | Secure (production) |
|---|---|---|
| `allow_anonymous` | `true` | `false` |
| Auth | none | username + password per client |
| ACL | none | per-node least privilege (`mosquitto_acl.conf`) |
| Use when | isolated trusted LAN, single node | multi-node / untrusted network / anything real |

---

## Enable secure mode (啟用步驟)

Run these on the **broker host** (e.g. the central Pi 5). None of it commits a
secret — passwords live only in `/etc/mosquitto/passwd`, which is not in the repo.

**1. Create the password file and one user per client.**
`-c` creates (and overwrites) the file — use it only for the first user, then
omit it. Each command prompts for the password interactively.

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd sdprs_server
sudo mosquitto_passwd    /etc/mosquitto/passwd sdprs_glass_node_01
sudo mosquitto_passwd    /etc/mosquitto/passwd sdprs_pump_node_01
```

Usernames must match the `user` blocks in `mosquitto_acl.conf` **and** the
`MQTT_USERNAME` configured on each device.

**2. Install the ACL file.**

```bash
sudo cp mosquitto_acl.conf /etc/mosquitto/acl.conf
```

Then reconcile its topic paths — see the caveat below.

**3. Flip `mosquitto.conf` to secure mode.**
In the `網路設定` section: comment out `allow_anonymous true`, and uncomment the
three secure directives:

```
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl.conf
allow_anonymous false
```

**4. Restart and check.**

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto
journalctl -u mosquitto -f
```

**5. Give the clients their credentials (without committing them).**
- **Central server:** set `MQTT_USERNAME` and `MQTT_PASSWORD` in the server's
  `.env` file. `central_server/config.py` already reads both — no code change
  needed. Keep `.env` out of git (it should already be `.gitignore`d).
- **Each edge device:** set the matching username/password in that device's own
  config (edge glass config / ESP32 pump config), never in a tracked file.

---

## Verify (驗證)

With a broker in secure mode, an **authenticated** subscribe with the server
credentials should work:

```bash
mosquitto_sub -h <broker> -u sdprs_server -P <pw> -t 'sdprs/#' -v
```

An **unauthenticated** subscribe (no `-u/-P`) should be **refused** — you should
see a connection error / not-authorized, not a live feed:

```bash
mosquitto_sub -h <broker> -t 'sdprs/#' -v   # expected: refused in secure mode
```

You can also confirm least privilege: a glass node's credentials should be able
to publish its own `heartbeat` but should be denied publishing to another
node's topics or to any `cmd/*` topic.

---

## Caveat — reconcile the ACL topics first (⚠ 重要)

The topic paths in `mosquitto_acl.conf` follow the observed scheme
`sdprs/edge/{node_id}/{category}` (heartbeat / pump_status / stream_status /
`cmd/*`), based on `shared/mqtt_topics.py`. They are provided as a **template,
not a verified match** for your deployment. Before enabling ACLs, confirm the
node ids and every published/subscribed topic against the real configuration:
`edge_glass/config.yaml`, `edge_pump/config.py`, and the server's
`mqtt_service`.

This matters because **an overly-tight ACL fails silently**: Mosquitto drops the
disallowed publish/subscribe without returning an error to the client, so a
mismatch looks like "the broker is up but messages mysteriously never arrive."
Always run the verification step above after switching, and watch
`journalctl -u mosquitto -f` for `ACL denied` lines.
