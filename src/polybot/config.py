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
        return self
