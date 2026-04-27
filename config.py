"""
config.py — Bot configuration
Copy this to .env and fill in your values. Or edit directly for testing.
"""

import os
from dataclasses import dataclass

@dataclass
class Config:
    # ── Telegram ──────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    # Comma-separated chat IDs to receive alerts (your personal chat or group)
    TELEGRAM_CHAT_IDS: list = None

    # ── Polymarket API ────────────────────────────────────────
    # Gamma API (markets/metadata, no auth needed)
    GAMMA_API_BASE: str = "https://gamma-api.polymarket.com"
    # CLOB API (order book + trades, no auth needed for reads)
    CLOB_API_BASE: str = "https://clob.polymarket.com"

    # ── Detection thresholds ──────────────────────────────────
    # Whale alert: single trade value in USDC
    WHALE_THRESHOLD_USDC: float = float(os.getenv("WHALE_THRESHOLD_USDC", "5000"))

    # Fresh wallet: flag if wallet has fewer than this many prior trades across ALL markets
    FRESH_WALLET_MAX_TRADES: int = int(os.getenv("FRESH_WALLET_MAX_TRADES", "5"))
    # ...but only alert if their bet is at least this large
    FRESH_WALLET_MIN_BET_USDC: float = float(os.getenv("FRESH_WALLET_MIN_BET_USDC", "500"))

    # Concentration alert: single wallet holds >X% of one outcome's open interest
    CONCENTRATION_THRESHOLD_PCT: float = float(os.getenv("CONCENTRATION_THRESHOLD_PCT", "20.0"))
    # Only flag if market has at least this much liquidity (avoid tiny markets)
    CONCENTRATION_MIN_LIQUIDITY_USDC: float = float(os.getenv("CONCENTRATION_MIN_LIQUIDITY_USDC", "10000"))

    # Volume spike: flag if recent 1h volume is >X times the 24h average hourly volume
    VOLUME_SPIKE_MULTIPLIER: float = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "3.0"))

    # ── Polling ───────────────────────────────────────────────
    # How often to scan for new trades (seconds)
    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

    # ── Filters ───────────────────────────────────────────────
    # Only monitor markets with at least this much liquidity
    MIN_MARKET_LIQUIDITY_USDC: float = float(os.getenv("MIN_MARKET_LIQUIDITY_USDC", "5000"))
    # Maximum number of active markets to monitor simultaneously (performance)
    MAX_MARKETS_TO_MONITOR: int = int(os.getenv("MAX_MARKETS_TO_MONITOR", "50"))

    def __post_init__(self):
        if self.TELEGRAM_CHAT_IDS is None:
            raw = os.getenv("TELEGRAM_CHAT_IDS", "")
            self.TELEGRAM_CHAT_IDS = [c.strip() for c in raw.split(",") if c.strip()]


config = Config()
