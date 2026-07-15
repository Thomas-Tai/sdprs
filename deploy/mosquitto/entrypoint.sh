#!/bin/sh
# Hash MQTT credentials at container start so no secret is ever stored in the
# git repo or the image layers. MQTT_USERNAME + MQTT_PASSWORD are Zeabur env
# vars set on this service.
set -eu

: "${MQTT_USERNAME:?MQTT_USERNAME env var is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD env var is required}"

# Mosquitto 2.1+ refuses to load a passwd file that is group- or world-readable
# (log: "Warning: File has world readable permissions" → fatal). Force
# owner-only perms on any file we create in this script.
umask 077

PASSWD=/mosquitto/config/passwd
printf '%s:%s\n' "$MQTT_USERNAME" "$MQTT_PASSWORD" > "$PASSWD"
mosquitto_passwd -U "$PASSWD"
chmod 0600 "$PASSWD"
# Base image drops privileges to user `mosquitto`; without this chown the file
# is root-owned and the daemon can't read it after dropping.
chown mosquitto:mosquitto "$PASSWD"

# Ensure the persistence dir the daemon writes to is owned by mosquitto too
# (base image expects this but the default entrypoint we overrode did the chown).
mkdir -p /mosquitto/data
chown -R mosquitto:mosquitto /mosquitto/data

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
