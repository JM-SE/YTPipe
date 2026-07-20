# Homelab Units

These units are intentionally not installed automatically. Installing or replacing host services requires operator sudo access.

1. Keep the existing crontab entry unchanged until the monitored service has been validated.
2. Install the PostgreSQL container unit or rely on Docker's `unless-stopped` policy, then reboot-test it.
3. Install the monitored poll service only after the existing cron and `ytpipe-poll-monitor.timer` have been disabled by the operator to avoid duplicate polls.
4. The monitor reads `/home/jmse/labs/YTPipe/.env`; do not copy bearer or Telegram secrets into unit files.
5. The monitor keeps its local failure-alert state in `/home/jmse/.local/state/ytpipe-poll-monitor` so it works both under systemd and in manual validation.
6. Set `POLL_INTERVAL_MINUTES` in `.env` to a positive whole number. The default and current local value is `60`; restart the service after changing it.

## Operator Commands

After validating the current manual llama.cpp process, restore systemd ownership so it starts after reboot:

```bash
sudo systemctl restart llama-server.service
sudo systemctl status llama-server.service --no-pager
```

The selected AMD GPU PPT cap is 100 W. The kernel exposes the current cap at
`/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap` in microwatts and requires root:

```bash
printf '100000000\n' | sudo tee /sys/class/drm/card0/device/hwmon/hwmon0/power1_cap
sensors
```

The cap is runtime-only and must be reapplied after reboot unless the operator adds a privileged boot unit.

## Monitor Rollout

Validate the monitor manually without changing cron yet:

```bash
set -a
. /home/jmse/labs/YTPipe/.env
set +a
/home/jmse/labs/YTPipe/scripts/ytpipe-poll-monitor.sh --once
```

Install the monitored poll only after disabling the old cron entry and the legacy timer, otherwise more than one scheduler can trigger `/internal/run-poll`:

```bash
sudo systemctl disable --now ytpipe-poll-monitor.timer
sudo cp /home/jmse/labs/YTPipe/systemd/ytpipe-poll-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ytpipe-poll-monitor.service
sudo journalctl -u ytpipe-poll-monitor.service --no-pager
```

After changing `POLL_INTERVAL_MINUTES` in `.env`, apply it with:

```bash
sudo systemctl restart ytpipe-poll-monitor.service
```
