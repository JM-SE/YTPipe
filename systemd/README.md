# Homelab Units

These units are intentionally not installed automatically. Installing or replacing host services requires operator sudo access.

1. Keep the existing crontab entry unchanged until the monitored service has been validated.
2. Install the PostgreSQL container unit or rely on Docker's `unless-stopped` policy, then reboot-test it.
3. Install the monitored poll service only after the existing cron and `ytpipe-poll-monitor.timer` have been disabled by the operator to avoid duplicate polls.
4. The monitor reads `/home/jmse/labs/YTPipe/.env`; do not copy bearer or Telegram secrets into unit files.
5. The monitor keeps its local failure-alert state in `/home/jmse/.local/state/ytpipe-poll-monitor` so it works both under systemd and in manual validation.
6. Set `POLL_INTERVAL_MINUTES` in `.env` to a positive whole number. The default and current local value is `60`; restart the service after changing it.
7. Set `LLAMA_CPP_AUTO_RESTART_ENABLED=true` only after installing and validating the narrow sudoers rule below.

The llama.cpp monitor is independent from the API and PostgreSQL path. It sends
one Telegram alert when `llama-server.service` becomes unhealthy, and sends a
recovery alert only after systemd is active and both `/health` and `/v1/models`
respond successfully. Its state is stored outside PostgreSQL so it remains
useful during an application or database incident.

## Operator Commands

After validating the current manual llama.cpp process, restore systemd ownership so it starts after reboot:

```bash
sudo systemctl restart llama-server.service
sudo systemctl status llama-server.service --no-pager
```

Install the optional automatic recovery permission. It allows only the exact llama.cpp restart command:

```bash
sudo install -o root -g root -m 0440 \
  systemd/ytpipe-llama-restart.sudoers \
  /etc/sudoers.d/ytpipe-llama-restart
sudo visudo -cf /etc/sudoers.d/ytpipe-llama-restart
sudo -u jmse sudo -n /usr/bin/systemctl restart llama-server.service
```

Then enable recovery in `.env` with `LLAMA_CPP_AUTO_RESTART_ENABLED=true`. The default cooldown is 300 seconds and can be changed with `LLAMA_CPP_RESTART_COOLDOWN_SECONDS`. Restart `ytpipe-api.service` after changing these settings.

## Llama.cpp Monitor Rollout

Install the monitor only after confirming that `TELEGRAM_NOTIFICATIONS_ENABLED`,
`TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` are configured in `.env`:

```bash
sudo cp /home/jmse/labs/YTPipe/systemd/ytpipe-llama-monitor.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ytpipe-llama-monitor.service
sudo systemctl status ytpipe-llama-monitor.service --no-pager
sudo journalctl -u ytpipe-llama-monitor.service --no-pager
```

The monitor checks every 10 seconds by default. It keeps state in
`/home/jmse/.local/state/ytpipe-llama-monitor` and does not send an alert on
its first healthy check. To validate it without installing the unit:

```bash
set -a
. /home/jmse/labs/YTPipe/.env
set +a
/home/jmse/labs/YTPipe/scripts/ytpipe-llama-monitor.sh --once
```

Restarts are detected through the systemd main PID/start timestamp even when
the downtime is shorter than one monitor interval. The monitor does not
perform an inference request; `/health` plus `/v1/models` is the configured
recovery criterion.

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

## Telegram Command Listener Rollout

The listener uses outbound Telegram long polling and calls only the protected
API on `127.0.0.1`. It does not access PostgreSQL or llama.cpp directly. Keep
`TELEGRAM_COMMANDS_ENABLED=false` until the bot and API configuration have
been validated.

Before enabling the listener, detect and stop every other `getUpdates` or
webhook consumer:

```bash
pgrep -af 'ytpipe-telegram-command-listener|getUpdates' || true
sudo systemctl list-unit-files | grep -Ei 'telegram|ytpipe' || true
sudo systemctl list-units --all | grep -Ei 'telegram|ytpipe' || true
```

Stop any known duplicate unit and terminate a manually started listener after
checking its PID. Verify that no listener remains before continuing:

```bash
sudo systemctl disable --now ytpipe-telegram-command-listener.service 2>/dev/null || true
pgrep -af 'ytpipe-telegram-command-listener|getUpdates' || true
```

Stop or interrupt each remaining process/unit reported above, then require a
clean check before enabling this unit:

```bash
if pgrep -af 'ytpipe-telegram-command-listener|getUpdates'; then
  printf 'A duplicate Telegram consumer is still running.\n' >&2
  exit 1
fi
```

Validate the bot, remove any webhook without dropping updates, and register
the command. The optional `--drop-pending-updates` flag is only for an
explicit one-time initial rollout choice:

```bash
cd /home/jmse/labs/YTPipe
/home/jmse/labs/YTPipe/.venv/bin/python \
  scripts/ytpipe-telegram-command-listener.py --configure
```

Run the listener manually as `jmse` while testing:

```bash
cd /home/jmse/labs/YTPipe
/home/jmse/labs/YTPipe/.venv/bin/python \
  scripts/ytpipe-telegram-command-listener.py
```

Then write the setting to `.env`, restart the API, and repeat the manual test:

```bash
sed -i 's/^TELEGRAM_COMMANDS_ENABLED=.*/TELEGRAM_COMMANDS_ENABLED=true/' \
  /home/jmse/labs/YTPipe/.env
grep '^TELEGRAM_COMMANDS_ENABLED=' /home/jmse/labs/YTPipe/.env
sudo systemctl restart ytpipe-api.service
```

Only after the manual command, cached-repeat, unauthorized-user, disabled-Short,
and restart-recovery checks pass, install the supervised unit:

```bash
sudo cp /home/jmse/labs/YTPipe/systemd/ytpipe-telegram-command-listener.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ytpipe-telegram-command-listener.service
sudo systemctl status ytpipe-telegram-command-listener.service --no-pager
sudo journalctl -u ytpipe-telegram-command-listener.service --no-pager
```

Routine restarts never use `drop_pending_updates=true`. To roll back, disable
commands and restart the API before stopping the listener; keep accepted rows
and the migration intact:

```bash
sed -i 's/^TELEGRAM_COMMANDS_ENABLED=.*/TELEGRAM_COMMANDS_ENABLED=false/' \
  /home/jmse/labs/YTPipe/.env
grep '^TELEGRAM_COMMANDS_ENABLED=' /home/jmse/labs/YTPipe/.env
sudo systemctl restart ytpipe-api.service
sudo systemctl disable --now ytpipe-telegram-command-listener.service
```

Updates that reach the API after commands are disabled are deliberately
rejected with no durable command row and may be acknowledged by Telegram's
offset progression. Stop the listener immediately after the API restart; any
updates still held by Telegram remain available for a later enablement.

Do not run a manual listener and the systemd unit at the same time. The bot
must have exactly one `getUpdates` consumer.
