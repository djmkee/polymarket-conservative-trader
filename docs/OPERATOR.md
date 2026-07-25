# Operator runbook

## Your steps now

1. Create a new wallet used only for this bot. Do not send its seed phrase or
   private key through chat.
2. Create/fund a Polymarket account only after confirming that trading is
   permitted where you reside.
3. Install Python 3.11 or newer on an always-on computer or VPS.
4. Run the quick-start commands in the README.
5. Leave the bot in paper mode for at least 30 days.
6. Review the SQLite event log weekly. Compare simulated prices against actual
   executable books and resolutions.
7. Do not fund live trading until every validation gate in the README passes.

## Emergency behavior required for live v0.2

- authenticated order-update stream;
- order heartbeat so stale orders cancel if the process dies;
- cancel-all command independent of the strategy loop;
- exchange-vs-local position reconciliation;
- loss and drawdown circuit breakers;
- secret injection via host environment, never `.env` in Git;
- withdrawal wallet separated from the bot signer.
