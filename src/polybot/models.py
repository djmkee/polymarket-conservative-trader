from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    yes_token: str
    no_token: str
    yes_ask: Decimal
    no_ask: Decimal
    yes_bid: Decimal
    no_bid: Decimal
    liquidity: Decimal
    end_time: datetime | None
    yes_ask_size: Decimal = Decimal(0)
    no_ask_size: Decimal = Decimal(0)
    yes_bid_size: Decimal = Decimal(0)
    no_bid_size: Decimal = Decimal(0)
    min_order_size: Decimal = Decimal(0)
    tick_size: Decimal = Decimal("0.01")
    reward_min_size: Decimal = Decimal(0)
    reward_max_spread: Decimal = Decimal(0)
    reward_daily_rate: Decimal = Decimal(0)
    yes_updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    no_updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    active: bool = True


@dataclass(frozen=True)
class MarketGroup:
    event_id: str
    title: str
    neg_risk_id: str
    markets: tuple[Market, ...]


@dataclass(frozen=True)
class Signal:
    strategy: str
    condition_id: str
    token_id: str
    side: Side
    price: Decimal
    edge: Decimal
    confidence: Decimal
    max_loss_per_share: Decimal
    rationale: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OrderIntent:
    signal: Signal
    shares: Decimal
    notional: Decimal


@dataclass
class Portfolio:
    cash: Decimal
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    open_exposure: Decimal = Decimal(0)
    peak_equity: Decimal = Decimal(0)
    day_start_equity: Decimal = Decimal(0)

    @property
    def equity(self) -> Decimal:
        return self.cash + self.open_exposure + self.unrealized_pnl
