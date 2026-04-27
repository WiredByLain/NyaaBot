"""
detector.py — Suspicious activity detection logic
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import config
from polymarket import PolymarketClient

logger = logging.getLogger(__name__)


class AlertType(Enum):
    WHALE = "Whale Alert"
    FRESH_WALLET = "Fresh Wallet"
    CONCENTRATION = "Concentration Alert"
    VOLUME_SPIKE = "Volume Spike"


@dataclass
class Alert:
    alert_type: AlertType
    market_question: str
    market_id: str
    wallet: Optional[str]
    details: str
    value_usdc: Optional[float] = None
    outcome: Optional[str] = None
    severity: str = "medium"
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def format_telegram(self) -> str:
        icons = {
            AlertType.WHALE: "🐳",
            AlertType.FRESH_WALLET: "🆕",
            AlertType.CONCENTRATION: "🎯",
            AlertType.VOLUME_SPIKE: "📈",
        }
        severity_bar = {"low": "🟡", "medium": "🟠", "high": "🔴", "critical": "💀"}
        icon = icons.get(self.alert_type, "⚠️")
        sev = severity_bar.get(self.severity, "🟠")

        lines = [
            f"{icon} {sev} *{self.alert_type.value}*",
            f"",
            f"📊 *Market:* {self.market_question}",
        ]
        if self.outcome:
            lines.append(f"🎲 *Outcome:* {self.outcome}")
        if self.value_usdc:
            lines.append(f"💵 *Amount:* ${self.value_usdc:,.0f} USDC")
        if self.wallet:
            short = f"{self.wallet[:6]}...{self.wallet[-4:]}"
            lines.append(f"👛 *Wallet:* `{short}`")
            lines.append(f"🔗 [View on Polymarket](https://polymarket.com/profile/{self.wallet})")
        lines += ["", f"ℹ️ {self.details}"]
        lines.append(f"\n🕐 `{_ts_to_utc(self.timestamp)}`")
        return "\n".join(lines)


def _ts_to_utc(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_trade(trade) -> Optional[dict]:
    if isinstance(trade, dict):
        return trade
    if isinstance(trade, str):
        try:
            import json
            return json.loads(trade)
        except Exception:
            pass
    return None


def _value_from_trade(trade: dict) -> float:
    try:
        if "usdcSize" in trade and trade["usdcSize"]:
            return float(trade["usdcSize"])
        price = float(trade.get("price") or trade.get("outcomePrice") or 0)
        size = float(trade.get("size") or trade.get("amount") or 0)
        return price * size
    except (TypeError, ValueError):
        return 0.0


def _severity_from_value(value: float) -> str:
    if value >= 100_000:
        return "critical"
    if value >= 25_000:
        return "high"
    if value >= 5_000:
        return "medium"
    return "low"


def _get_wallet(trade: dict) -> Optional[str]:
    return (
        trade.get("maker") or
        trade.get("taker") or
        trade.get("transactorAddress") or
        trade.get("maker_address") or
        trade.get("taker_address") or
        trade.get("user")
    )


class SuspiciousActivityDetector:
    def __init__(self, client: PolymarketClient):
        self.client = client
        self._seen_trades: dict[str, bool] = {}
        self._wallet_trade_counts: dict[str, int] = {}
        self._volume_state: dict[str, dict] = {}

    async def process_trades(self, market: dict, trades: list) -> list[Alert]:
        alerts: list[Alert] = []
        question = market.get("question", "Unknown market")
        market_id = market.get("conditionId") or market.get("id", "")
        liquidity = float(market.get("liquidity") or 0)

        new_trades = []
        for raw in trades:
            trade = _parse_trade(raw)
            if not trade:
                continue
            tid = trade.get("id") or trade.get("transactionHash") or trade.get("hash") or str(trade)
            if tid in self._seen_trades:
                continue
            self._seen_trades[tid] = True
            new_trades.append(trade)

        if len(self._seen_trades) > 50_000:
            for k in list(self._seen_trades.keys())[:25_000]:
                del self._seen_trades[k]

        for trade in new_trades:
            value = _value_from_trade(trade)
            wallet = _get_wallet(trade)
            outcome = trade.get("outcome") or trade.get("side", "")

            # 1. WHALE
            if value >= config.WHALE_THRESHOLD_USDC:
                alerts.append(Alert(
                    alert_type=AlertType.WHALE,
                    market_question=question,
                    market_id=market_id,
                    wallet=wallet,
                    value_usdc=value,
                    outcome=outcome,
                    details=f"Single trade of ${value:,.0f} USDC detected. Threshold: ${config.WHALE_THRESHOLD_USDC:,.0f}.",
                    severity=_severity_from_value(value),
                ))

            # 2. FRESH WALLET
            if wallet and value >= config.FRESH_WALLET_MIN_BET_USDC:
                prior = await self._get_wallet_trade_count(wallet)
                if prior < config.FRESH_WALLET_MAX_TRADES:
                    alerts.append(Alert(
                        alert_type=AlertType.FRESH_WALLET,
                        market_question=question,
                        market_id=market_id,
                        wallet=wallet,
                        value_usdc=value,
                        outcome=outcome,
                        details=(
                            f"Wallet has only {prior} prior trades but placed "
                            f"a ${value:,.0f} USDC bet. Possible fresh/burner wallet."
                        ),
                        severity="high" if value >= 10_000 else "medium",
                    ))

        # 3. CONCENTRATION — requires multiple unique wallets in batch AND minimum trade count
        if liquidity >= config.CONCENTRATION_MIN_LIQUIDITY_USDC and len(new_trades) >= 5:
            conc_alerts = self._check_concentration_from_trades(market, new_trades)
            alerts.extend(conc_alerts)

        # 4. VOLUME SPIKE
        if new_trades:
            spike = self._check_volume_spike(market_id, question, new_trades)
            if spike:
                alerts.append(spike)

        return alerts

    async def _get_wallet_trade_count(self, wallet: str) -> int:
        if wallet in self._wallet_trade_counts:
            return self._wallet_trade_counts[wallet]
        try:
            trades = await self.client.get_wallet_trades(wallet, limit=20)
            count = len(trades) if isinstance(trades, list) else 99
        except Exception:
            count = 99
        self._wallet_trade_counts[wallet] = count
        return count

    def _check_concentration_from_trades(self, market: dict, trades: list[dict]) -> list[Alert]:
        """
        Only flag concentration if:
        - At least 3 unique wallets traded in this batch (avoid single-trade false positives)
        - One wallet accounts for >= CONCENTRATION_THRESHOLD_PCT of a side
        - That wallet's total USDC value is >= CONCENTRATION_MIN_LIQUIDITY_USDC * 0.1
        """
        alerts = []
        question = market.get("question", "Unknown market")
        market_id = market.get("conditionId") or market.get("id", "")
        liquidity = float(market.get("liquidity") or 0)

        wallet_totals: dict[tuple, float] = {}
        outcome_totals: dict[str, float] = {}
        unique_wallets: set = set()

        for trade in trades:
            w = _get_wallet(trade) or "unknown"
            out = trade.get("outcome") or trade.get("side", "YES")
            value = _value_from_trade(trade)
            if value <= 0:
                continue
            wallet_totals[(w, out)] = wallet_totals.get((w, out), 0) + value
            outcome_totals[out] = outcome_totals.get(out, 0) + value
            if w != "unknown":
                unique_wallets.add(w)

        # Need at least 3 unique wallets to make concentration meaningful
        if len(unique_wallets) < 3:
            return alerts

        min_value = config.CONCENTRATION_MIN_LIQUIDITY_USDC * 0.1

        for (w, out), amt in wallet_totals.items():
            total = outcome_totals.get(out, 1)
            pct = (amt / total) * 100 if total else 0

            if pct >= config.CONCENTRATION_THRESHOLD_PCT and amt >= min_value:
                alerts.append(Alert(
                    alert_type=AlertType.CONCENTRATION,
                    market_question=question,
                    market_id=market_id,
                    wallet=w if w != "unknown" else None,
                    outcome=out,
                    value_usdc=amt,
                    details=(
                        f"Single wallet accounted for {pct:.1f}% of '{out}' "
                        f"volume across {len(unique_wallets)} traders. "
                        f"Market liquidity: ${liquidity:,.0f}."
                    ),
                    severity="critical" if pct >= 50 else "high" if pct >= 35 else "medium",
                ))

        return alerts

    def _check_volume_spike(self, market_id: str, question: str, new_trades: list[dict]) -> Optional[Alert]:
        now = int(time.time())
        state = self._volume_state.setdefault(market_id, {"samples": []})

        batch_volume = sum(_value_from_trade(t) for t in new_trades)
        state["samples"].append((now, batch_volume))
        state["samples"] = [(ts, v) for ts, v in state["samples"] if now - ts < 86400]

        if len(state["samples"]) < 10:
            return None

        historical = [v for _, v in state["samples"][:-1]]
        avg = sum(historical) / len(historical)
        if avg <= 0:
            return None

        multiplier = batch_volume / avg
        if multiplier >= config.VOLUME_SPIKE_MULTIPLIER:
            return Alert(
                alert_type=AlertType.VOLUME_SPIKE,
                market_question=question,
                market_id=market_id,
                wallet=None,
                value_usdc=batch_volume,
                details=(
                    f"Volume this cycle: ${batch_volume:,.0f} USDC "
                    f"({multiplier:.1f}x the {len(historical)}-cycle average of ${avg:,.0f})."
                ),
                severity="high" if multiplier >= 5 else "medium",
            )
        return None
