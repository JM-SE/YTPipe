#!/home/jmse/labs/YTPipe/.venv/bin/python
from __future__ import annotations

import argparse
import logging
import signal

from app.core.settings import Settings
from app.services.telegram_command_listener import (
    ListenerConfig,
    TelegramBotClient,
    TelegramCommandAPIClient,
    TelegramCommandListener,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="YTPipe Telegram command long-polling listener")
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Validate the bot, remove its webhook without dropping updates, and register /summary.",
    )
    parser.add_argument(
        "--drop-pending-updates",
        action="store_true",
        help="Explicitly drop Telegram pending updates during --configure; use only for initial rollout.",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args()
    if args.drop_pending_updates and not args.configure:
        parser.error("--drop-pending-updates requires --configure")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx includes request URLs in INFO logs; Bot API URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.telegram_commands_enabled and not args.configure:
        logging.getLogger(__name__).warning("TELEGRAM_COMMANDS_ENABLED=false; listener not started.")
        return 0
    settings.validate_runtime_config()
    config = ListenerConfig.from_settings(settings, require_command_config=not args.configure)
    bot = TelegramBotClient(config.bot_token)
    api = TelegramCommandAPIClient(config.api_base_url, config.internal_api_bearer_token)
    listener = TelegramCommandListener(config, bot, api)
    try:
        if args.configure:
            listener.configure_bot(drop_pending_updates=args.drop_pending_updates)
            logging.getLogger(__name__).info("Telegram bot configured without exposing credentials.")
            return 0

        signal.signal(signal.SIGINT, lambda _signum, _frame: listener.stop())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: listener.stop())
        listener.run()
        return 0
    finally:
        api.close()
        bot.close()


if __name__ == "__main__":
    raise SystemExit(main())
