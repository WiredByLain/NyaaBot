"""
bot.py — Telegram bot interface

Commands:
  /start      — subscribe this chat to alerts
  /stop       — unsubscribe
  /status     — show bot stats + active markets being monitored
  /thresholds — show current detection thresholds
  /set <key> <value> — update a threshold at runtime
  /markets    — list top markets being monitored
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# ── Persistent subscriber store (simple JSON file) ──────────────────────────
SUBSCRIBERS_FILE = Path("subscribers.json")


def load_subscribers() -> set[str]:
    if SUBSCRIBERS_FILE.exists():
        try:
            return set(json.loads(SUBSCRIBERS_FILE.read_text()))
        except Exception:
            pass
    # Seed with any chat IDs from config
    return set(config.TELEGRAM_CHAT_IDS)


def save_subscribers(subs: set[str]):
    SUBSCRIBERS_FILE.write_text(json.dumps(list(subs)))


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self.subscribers: set[str] = load_subscribers()
        self._offset: int = 0
        self._stats = {
            "alerts_sent": 0,
            "markets_scanned": 0,
            "start_time": int(time.time()),
        }
        # Live-editable thresholds (mirrors config but mutable at runtime)
        self.thresholds = {
            "whale_usdc": config.WHALE_THRESHOLD_USDC,
            "fresh_wallet_max_trades": config.FRESH_WALLET_MAX_TRADES,
            "fresh_wallet_min_bet": config.FRESH_WALLET_MIN_BET_USDC,
            "concentration_pct": config.CONCENTRATION_THRESHOLD_PCT,
            "volume_spike_x": config.VOLUME_SPIKE_MULTIPLIER,
        }

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ─────────────────────────────────────────────
    # Telegram API helpers
    # ─────────────────────────────────────────────

    async def _call(self, method: str, payload: dict = None) -> dict | None:
        url = TELEGRAM_API.format(token=self.token, method=method)
        session = await self._sess()
        try:
            async with session.post(url, json=payload or {}) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"Telegram error on {method}: {data}")
                return data
        except Exception as e:
            logger.error(f"Telegram request failed ({method}): {e}")
            return None

    async def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown"):
        await self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        })

    async def broadcast(self, text: str):
        """Send a message to all subscribers."""
        dead: list[str] = []
        for chat_id in list(self.subscribers):
            result = await self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            })
            if result and not result.get("ok"):
                err = result.get("description", "")
                if "blocked" in err or "not found" in err or "deactivated" in err:
                    dead.append(chat_id)
        for d in dead:
            self.subscribers.discard(d)
        if dead:
            save_subscribers(self.subscribers)
        self._stats["alerts_sent"] += 1

    # ─────────────────────────────────────────────
    # Update polling + command dispatch
    # ─────────────────────────────────────────────

    async def poll_updates(self):
        """Long-poll Telegram for new messages and dispatch commands."""
        data = await self._call("getUpdates", {
            "offset": self._offset,
            "timeout": 20,
            "allowed_updates": ["message"],
        })
        if not data or not data.get("ok"):
            return
        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if text and chat_id:
                await self._handle_command(chat_id, text)

    async def _handle_command(self, chat_id: str, text: str):
        cmd = text.split()[0].lower().split("@")[0]  # strip bot username suffix

        if cmd == "/start":
            self.subscribers.add(chat_id)
            save_subscribers(self.subscribers)
            await self.send_message(chat_id,
                "✅ *Polymarket Alert Bot activated!*\n\n"
                "You'll receive alerts for:\n"
                "• 🐳 Whale trades\n"
                "• 🆕 Fresh wallet large bets\n"
                "• 🎯 Concentrated positions\n"
                "• 📈 Volume spikes\n\n"
                "Use /thresholds to see current settings\n"
                "Use /status to see bot stats"
            )

        elif cmd == "/stop":
            self.subscribers.discard(chat_id)
            save_subscribers(self.subscribers)
            await self.send_message(chat_id, "🔕 Unsubscribed. Use /start to re-enable.")

        elif cmd == "/status":
            uptime = int(time.time()) - self._stats["start_time"]
            h, m = divmod(uptime // 60, 60)
            await self.send_message(chat_id,
                f"📡 *Bot Status*\n\n"
                f"⏱ Uptime: {h}h {m}m\n"
                f"📣 Alerts sent: {self._stats['alerts_sent']}\n"
                f"🔍 Markets scanned: {self._stats['markets_scanned']}\n"
                f"👥 Subscribers: {len(self.subscribers)}\n"
                f"⏲ Poll interval: {config.POLL_INTERVAL_SECONDS}s"
            )

        elif cmd == "/thresholds":
            t = self.thresholds
            await self.send_message(chat_id,
                f"⚙️ *Current Thresholds*\n\n"
                f"`whale_usdc`          = ${t['whale_usdc']:,.0f}\n"
                f"`fresh_wallet_max_trades` = {t['fresh_wallet_max_trades']}\n"
                f"`fresh_wallet_min_bet`    = ${t['fresh_wallet_min_bet']:,.0f}\n"
                f"`concentration_pct`   = {t['concentration_pct']}%\n"
                f"`volume_spike_x`      = {t['volume_spike_x']}×\n\n"
                f"Edit with: `/set <key> <value>`\n"
                f"Example: `/set whale_usdc 10000`"
            )

        elif cmd == "/set":
            parts = text.split()
            if len(parts) != 3:
                await self.send_message(chat_id, "Usage: `/set <key> <value>`")
                return
            key, val_str = parts[1], parts[2]
            if key not in self.thresholds:
                await self.send_message(chat_id, f"Unknown key `{key}`. See /thresholds for valid keys.")
                return
            try:
                val = float(val_str)
                self.thresholds[key] = val
                # Push back to config for detector to pick up
                _sync_thresholds_to_config(self.thresholds)
                await self.send_message(chat_id, f"✅ `{key}` set to `{val}`")
            except ValueError:
                await self.send_message(chat_id, "Value must be a number.")

        elif cmd == "/markets":
            # This will be populated by the monitor
            count = self._stats.get("active_markets", 0)
            await self.send_message(chat_id,
                f"📊 Currently monitoring *{count}* active markets.\n"
                f"Min liquidity filter: ${config.MIN_MARKET_LIQUIDITY_USDC:,.0f}"
            )

        elif cmd == "/help":
            await self.send_message(chat_id,
                "*Commands:*\n"
                "/start — Subscribe to alerts\n"
                "/stop — Unsubscribe\n"
                "/status — Bot stats\n"
                "/thresholds — View detection thresholds\n"
                "/set <key> <value> — Update a threshold\n"
                "/markets — Show monitored market count\n"
                "/help — This message"
            )

    def update_stats(self, key: str, value):
        self._stats[key] = value


def _sync_thresholds_to_config(t: dict):
    config.WHALE_THRESHOLD_USDC = t["whale_usdc"]
    config.FRESH_WALLET_MAX_TRADES = int(t["fresh_wallet_max_trades"])
    config.FRESH_WALLET_MIN_BET_USDC = t["fresh_wallet_min_bet"]
    config.CONCENTRATION_THRESHOLD_PCT = t["concentration_pct"]
    config.VOLUME_SPIKE_MULTIPLIER = t["volume_spike_x"]
