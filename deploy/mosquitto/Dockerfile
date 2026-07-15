FROM eclipse-mosquitto:2

# ── Mosquitto MQTT broker for SDPRS (Zeabur cloud) ──
# Chosen over EMQX for its ~15 MB idle footprint (vs EMQX ~200 MB) so it
# survives Zeabur's shared-pool eviction pressure. Same MQTT protocol — no
# SDPRS code changes needed.
#
# Deployment:
#   - Same project as sdprs-server; internal name reachable via <svc>.zeabur.internal
#   - Expose TCP 1883 publicly for Pi/ESP32 (Zeabur → Networking → 1883 → Public)
#   - No admin dashboard (unlike EMQX) — use `mosquitto_sub` for debug
#
# Credentials:
#   - MQTT_USERNAME + MQTT_PASSWORD env vars are set in Zeabur console
#   - entrypoint.sh hashes the password at container start (never stored in git)

COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY entrypoint.sh  /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1883

ENTRYPOINT ["/entrypoint.sh"]
