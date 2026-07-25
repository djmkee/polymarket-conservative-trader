from decimal import ROUND_DOWN, Decimal

from .config import Settings
from .models import OrderIntent, Portfolio, Signal


class RiskManager:
    def __init__(self, settings: Settings):
        self.s = settings

    def halted_reason(self, p: Portfolio) -> str | None:
        equity = p.equity
        if p.day_start_equity and equity <= p.day_start_equity * (1 - self.s.daily_loss_limit_pct):
            return "daily_loss_limit"
        if p.peak_equity and equity <= p.peak_equity * (1 - self.s.max_drawdown_pct):
            return "maximum_drawdown"
        if p.open_exposure >= equity * self.s.max_total_exposure_pct:
            return "portfolio_exposure_limit"
        return None

    def size(self, signal: Signal, p: Portfolio) -> OrderIntent | None:
        if self.halted_reason(p) or signal.edge < self.s.min_net_edge:
            return None
        equity = p.equity
        risk_budget = equity * self.s.risk_per_trade_pct
        market_cap = equity * self.s.max_market_exposure_pct
        portfolio_room = max(Decimal(0), equity * self.s.max_total_exposure_pct - p.open_exposure)
        notional = min(risk_budget, market_cap, portfolio_room, p.cash)
        shares = (notional / signal.price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if shares <= 0:
            return None
        return OrderIntent(signal=signal, shares=shares, notional=shares * signal.price)
