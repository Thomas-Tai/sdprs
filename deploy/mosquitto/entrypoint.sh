#!/bin/sh
# Hash MQTT credentials at container start so no secret is ever stored in the
# git repo or the image layers. MQTT_USERNAME + MQTT_PASSWORD are Zeabur env
# vars set on this service.
set -eu

: "${MQTT_USERNAME:?MQTT_USERNAME env var is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD env var is required}"

PASSWD=/mosquitto/config/passwd
printf '%s:%s\n' "$MQTT_USERNAME" "$MQTT_PASSWORD" > "$PASSWD"
mosquitto_passwd -U "$PASSWD"
chmod 600 "$PASSWD"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
