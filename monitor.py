"""
monitor.py — Main polling loop
"""

import asyncio
import logging
import time

from config import config
from polymarket import PolymarketClient
from detector import SuspiciousActivityDetector
from bot import TelegramBot

logger = logging.getLogger(__name__)

# Max alerts to send per scan cycle (prevents Telegram flood limits)
MAX_ALERTS_PER_CYCLE = 10


async def run_monitor(bot: TelegramBot, client: PolymarketClient, detector: SuspiciousActivityDetector):
    logger.info("Starting market scan...")

    try:
        markets = await client.get_active_markets(limit=config.MAX_MARKETS_TO_MONITOR)
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return

    markets = [
        m for m in markets
        if float(m.get("liquidity") or 0) >= config.MIN_MARKET_LIQUIDITY_USDC
    ]

    logger.info(f"Scanning {len(markets)} markets...")
    bot.update_stats("active_markets", len(markets))
    bot.update_stats("markets_scanned", bot._stats.get("markets_scanned", 0) + len(markets))

    alerts_sent_this_cycle = 0

    for market in markets:
        if alerts_sent_this_cycle >= MAX_ALERTS_PER_CYCLE:
            logger.info(f"Alert cap ({MAX_ALERTS_PER_CYCLE}) reached for this cycle, skipping remaining markets.")
            break

        market_id = market.get("conditionId") or market.get("id")
        if not market_id:
            continue

        try:
            trades = await client.get_recent_trades(market_id, limit=50)
        except Exception as e:
            logger.warning(f"Failed to fetch trades for {market_id}: {e}")
            continue

        if not trades:
            await asyncio.sleep(0.1)
            continue

        try:
            alerts = await detector.process_trades(market, trades)
        except Exception as e:
            logger.error(f"Detector error on {market_id}: {e}")
            continue

        for alert in alerts:
            if alerts_sent_this_cycle >= MAX_ALERTS_PER_CYCLE:
                break
            logger.info(f"ALERT: {alert.alert_type.name} | {alert.market_question[:60]}")
            msg = alert.format_telegram()
            await bot.broadcast(msg)
            alerts_sent_this_cycle += 1
            # Telegram allows ~1 msg/sec to the same chat
            await asyncio.sleep(1.2)

        await asyncio.sleep(0.3)

    if alerts_sent_this_cycle == 0:
        logger.info("No suspicious activity detected this cycle.")
    else:
        logger.info(f"Sent {alerts_sent_this_cycle} alert(s) this cycle.")


async def main_loop(bot: TelegramBot):
    client = PolymarketClient(config.GAMMA_API_BASE, config.CLOB_API_BASE)
    detector = SuspiciousActivityDetector(client)

    logger.info(f"Bot started. Poll interval: {config.POLL_INTERVAL_SECONDS}s")
    logger.info(f"Subscribers: {bot.subscribers}")

    try:
        while True:
            poll_task = asyncio.create_task(bot.poll_updates())
            scan_task = asyncio.create_task(run_monitor(bot, client, detector))
            await asyncio.gather(poll_task, scan_task, return_exceptions=True)
            logger.info(f"Cycle complete. Sleeping {config.POLL_INTERVAL_SECONDS}s...")
            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
    finally:
        await client.close()
