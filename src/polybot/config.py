from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="POLYBOT_", extra="ignore")

    mode: Literal["paper", "live"] = "paper"
    initial_equity: Decimal = Decimal(300)
    risk_per_trade_pct: Decimal = Decimal("0.01")
    max_market_exposure_pct: Decimal = Decimal("0.05")
    max_total_exposure_pct: Decimal = Decimal("0.25")
    daily_loss_limit_pct: Decimal = Decimal("0.02")
    max_drawdown_pct: Decimal = Decimal("0.08")
    min_liquidity: Decimal = Decimal(10000)
    discovery_limit: int = 500
    neg_risk_event_limit: int = 50
    neg_risk_max_outcomes: int = 20
    maker_enabled: bool = True
    maker_max_markets: int = 3
    maker_order_shares: Decimal = Decimal(5)
    maker_compound: bool = True
    maker_order_equity_pct: Decimal = Decimal("0.02")
    maker_max_capital_pct: Decimal = Decimal("0.20")
    maker_max_directional_exposure_pct: Decimal = Decimal("0.02")
    maker_min_spread: Decimal = Decimal("0.02")
    maker_min_price: Decimal = Decimal("0.15")
    maker_max_price: Decimal = Decimal("0.85")
    maker_min_hours_to_end: int = 6
    maker_max_hours_to_end: int = 720
    maker_max_capital: Decimal = Decimal(60)
    maker_take_profit_per_share: Decimal = Decimal("0.005")
    maker_max_fee_rate: Decimal = Decimal("0.07")
    maker_max_directional_shares: Decimal = Decimal(5)
    maker_pair_min_edge: Decimal = Decimal("0.01")
    maker_inventory_skew_per_share: Decimal = Decimal("0.002")
    maker_hedge_timeout_seconds: int = 15
    maker_force_flatten_seconds: int = 60
    maker_max_flatten_loss_per_share: Decimal = Decimal("0.015")
    maker_max_book_age_seconds: int = 30
    maker_toxicity_window_seconds: int = 300
    maker_max_midpoint_jump: Decimal = Decimal("0.035")
    maker_reward_weight: Decimal = Decimal("0.25")
    realtime_quote_interval_seconds: int = 5
    realtime_refresh_seconds: int = 900
    realtime_max_markets: int = 20
    realtime_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    min_net_edge: Decimal = Decimal("0.0075")
    slippage_bps: Decimal = Decimal(20)
    db_path: Path = Path("polybot.sqlite3")
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    live_acknowledgement: str = Field(default="")

    @model_validator(mode="after")
    def safe_defaults(self) -> "Settings":
        if self.mode == "live":
            raise ValueError(
                "Live trading is locked in v0.1. Validate paper results and implement "
                "wallet-specific execution/reconciliation first."
            )
        percentages = (
            self.risk_per_trade_pct,
            self.max_market_exposure_pct,
            self.max_total_exposure_pct,
            self.daily_loss_limit_pct,
            self.max_drawdown_pct,
        )
        if any(x <= 0 or x > 1 for x in percentages):
            raise ValueError("Risk percentages must be in (0, 1].")
        if not 1 <= self.discovery_limit <= 5000:
            raise ValueError("discovery_limit must be between 1 and 5000.")
        if not 1 <= self.neg_risk_event_limit <= 200:
            raise ValueError("neg_risk_event_limit must be between 1 and 200.")
        if not 3 <= self.neg_risk_max_outcomes <= 50:
            raise ValueError("neg_risk_max_outcomes must be between 3 and 50.")
        if not 1 <= self.maker_max_markets <= 10:
            raise ValueError("maker_max_markets must be between 1 and 10.")
        if self.maker_order_shares <= 0 or self.maker_max_capital <= 0:
            raise ValueError("Maker size and capital limits must be positive.")
        maker_percentages = (
            self.maker_order_equity_pct,
            self.maker_max_capital_pct,
            self.maker_max_directional_exposure_pct,
        )
        if any(x <= 0 or x > 1 for x in maker_percentages):
            raise ValueError("Maker percentages must be in (0, 1].")
        if self.maker_take_profit_per_share <= 0 or self.maker_max_fee_rate < 0:
            raise ValueError("Maker profit and fee settings must be non-negative.")
        if not 0 < self.maker_pair_min_edge < 1:
            raise ValueError("maker_pair_min_edge must be between 0 and 1.")
        if self.maker_inventory_skew_per_share < 0:
            raise ValueError("maker_inventory_skew_per_share cannot be negative.")
        if self.maker_hedge_timeout_seconds < 10:
            raise ValueError("maker_hedge_timeout_seconds must be at least 10.")
        if self.maker_force_flatten_seconds <= self.maker_hedge_timeout_seconds:
            raise ValueError(
                "maker_force_flatten_seconds must exceed maker_hedge_timeout_seconds."
            )
        if self.maker_max_flatten_loss_per_share < 0:
            raise ValueError("maker_max_flatten_loss_per_share cannot be negative.")
        if self.maker_max_book_age_seconds < 5:
            raise ValueError("maker_max_book_age_seconds must be at least 5.")
        if self.maker_toxicity_window_seconds < 30:
            raise ValueError("maker_toxicity_window_seconds must be at least 30.")
        if not 0 < self.maker_max_midpoint_jump < 1:
            raise ValueError("maker_max_midpoint_jump must be between 0 and 1.")
        if self.realtime_quote_interval_seconds < 1:
            raise ValueError("realtime_quote_interval_seconds must be positive.")
        if self.realtime_refresh_seconds < 60:
            raise ValueError("realtime_refresh_seconds must be at least 60.")
        if not 1 <= self.realtime_max_markets <= 200:
            raise ValueError("realtime_max_markets must be between 1 and 200.")
        if not 0 < self.maker_min_price < self.maker_max_price < 1:
            raise ValueError("Invalid maker price range.")
        if (
            self.maker_min_hours_to_end < 1
            or self.maker_max_hours_to_end <= self.maker_min_hours_to_end
        ):
            raise ValueError(
                "Maker end-time window must be positive and max must exceed min."
            )
        return self
