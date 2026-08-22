# Edge glass auto-update — operator runbook (Phase 1)

The glass fleet self-updates nightly from the **`edge-release`** branch:
each node, in a staggered 03:00–03:30 window, brings itself to the
`edge-release` tip with snapshot → rsync → health-check → rollback. No
workstation, LAN, or VPN involved — each Pi fetches public code itself.

## Cut a release (dev workstation)
Publishing = fast-forwarding `edge-release` to `main`'s tip:

    scripts/publish_edge_release.sh          # publishes origin/main
    scripts/publish_edge_release.sh <ref>    # publishes another ref

Nodes apply it during their next quiet window. To roll the fleet back to an
earlier good commit, `publish_edge_release.sh` cannot (it is FF-only) — reset
`edge-release` deliberately: `git push origin <good-sha>:refs/heads/edge-release --force-with-lease`
(a considered, manual action).

## Bootstrap a node (once, on the Pi)

    curl -fsSL https://raw.githubusercontent.com/Thomas-Tai/sdprs/edge-release/scripts/edge_autoupdate_install.sh | sudo bash

Idempotent — re-run to update the updater itself.

## Pause / resume a node (maintenance)

    sudo touch /opt/sdprs/.edge_update_paused     # skip all auto-updates
    sudo rm -f /opt/sdprs/.edge_update_paused     # resume

## Observe

    systemctl list-timers sdprs-edge-update.timer      # next run
    journalctl -u sdprs-edge-update -n 50 --no-pager   # last run's log
    cat /opt/sdprs/.edge_deployed_sha                  # what's deployed

## Force an update now (bypasses window + hold)

    sudo /opt/sdprs/edge_autoupdate.sh --manual
    sudo /opt/sdprs/edge_autoupdate.sh --dry-run       # show intent only

## Manual rollback
Automatic rollback runs on a failed health-check. To roll back by hand to a
snapshot (kept at `/opt/sdprs/edge_glass.backup.<stamp>.tgz`, newest
`KEEP_SNAPSHOTS`=3):

    sudo systemctl stop sdprs-edge-cloud
    sudo tar xzf /opt/sdprs/edge_glass.backup.<stamp>.tgz -C /opt/sdprs
    sudo chown -R sdprs:sdprs /opt/sdprs/edge_glass
    sudo systemctl start sdprs-edge-cloud

## Config
Per-node knobs in `/etc/sdprs-edge-update.conf` (quiet window, health timeout,
snapshot retention). `deploy_console.sh` remains the ad-hoc "pull main now"
tool and coexists with this timer.
