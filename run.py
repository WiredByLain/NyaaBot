"""
run.py — Entry point

Usage:
    python run.py

Environment variables (or edit config.py directly):
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_IDS    — comma-separated chat IDs (your personal chat or a group)
    WHALE_THRESHOLD_USDC — default 5000
    POLL_INTERVAL_SECONDS — default 60
"""

import asyncio
import logging
import sys

from config import config
from bot import TelegramBot
from monitor import main_loop


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ],
    )
    # Quieter noise from aiohttp
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def startup_check(bot: TelegramBot) -> bool:
    """Verify bot token is valid and notify subscribers."""
    from aiohttp import ClientSession
    import aiohttp

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            data = await r.json()

    if not data.get("ok"):
        print(f"❌ Invalid bot token: {data}")
        return False

    name = data["result"].get("first_name", "Bot")
    username = data["result"].get("username", "")
    print(f"✅ Connected as {name} (@{username})")

    if not bot.subscribers:
        print("⚠️  No subscribers yet. Message your bot and send /start to subscribe.")
    else:
        await bot.broadcast(
            f"🤖 *Polymarket Alert Bot online*\n"
            f"Monitoring top markets every {config.POLL_INTERVAL_SECONDS}s\n\n"
            f"Whale threshold: ${config.WHALE_THRESHOLD_USDC:,.0f}\n"
            f"Send /help for commands."
        )
    return True


async def main():
    setup_logging()
    logger = logging.getLogger("run")

    if config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print(
            "❌  Set your bot token first!\n"
            "   Edit config.py or set TELEGRAM_BOT_TOKEN env var.\n"
            "   Get a token from @BotFather on Telegram."
        )
        sys.exit(1)

    bot = TelegramBot(config.TELEGRAM_BOT_TOKEN)

    ok = await startup_check(bot)
    if not ok:
        sys.exit(1)

    logger.info("Starting monitor loop…")
    try:
        await main_loop(bot)
    except KeyboardInterrupt:
        logger.info("Shutting down…")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
