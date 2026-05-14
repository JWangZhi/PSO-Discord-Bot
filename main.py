"""Base/NGS AI Advisor Bot entrypoint."""

import argparse
import logging

from bot.client import PSO2Bot
from bot.commands import register_commands
from settings import app as app_settings
from settings import env as config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Base/NGS AI Advisor Bot")
    parser.add_argument("--mcp", action="store_true", help="Enable MCP wiki fetching via MediaWiki API")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to terminal")
    return parser.parse_args()


def configure_debug(enabled: bool) -> None:
    if not enabled:
        return

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    for noisy_logger in ("pymongo", "httpcore", "httpx", "groq", "urllib3", "asyncio", "hpack"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    app_settings.BOT_DEBUG_ENABLED = True
    app_settings.BOT_DEBUG_INCLUDE_IN_REPLY = True


def main() -> None:
    args = parse_args()
    configure_debug(args.debug)

    if not config.DISCORD_BOT_TOKEN:
        print("[ERROR] Missing DISCORD_BOT_TOKEN in .env file")
        return

    bot = PSO2Bot(enable_mcp=args.mcp)
    register_commands(bot)
    print("[...] Starting PSO2 Bot...")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
