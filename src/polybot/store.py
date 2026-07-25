import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS events (
               id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
               payload TEXT NOT NULL)"""
        )
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_equity TEXT NOT NULL,
                cash TEXT NOT NULL,
                peak_equity TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_inventory (
                token_id TEXT PRIMARY KEY,
                condition_id TEXT NOT NULL,
                question TEXT NOT NULL,
                outcome TEXT NOT NULL,
                shares TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_quotes (
                id INTEGER PRIMARY KEY,
                token_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                price TEXT NOT NULL,
                size TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_fills (
                id INTEGER PRIMARY KEY,
                quote_id INTEGER NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price TEXT NOT NULL,
                size TEXT NOT NULL,
                notional TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_directional (
                token_id TEXT PRIMARY KEY,
                shares TEXT NOT NULL,
                cost_basis TEXT NOT NULL,
                opened_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_metrics (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                realized_pnl TEXT NOT NULL,
                fees TEXT NOT NULL
            );
            INSERT OR IGNORE INTO paper_metrics(id, realized_pnl, fees)
            VALUES (1, '0', '0');
            CREATE TABLE IF NOT EXISTS market_ticks (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                bid TEXT NOT NULL,
                ask TEXT NOT NULL,
                midpoint TEXT NOT NULL,
                event_type TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_market_ticks_condition_time
                ON market_ticks(condition_id, created_at);
            """
        )
        self._ensure_column("paper_directional", "opened_at", "TEXT")
        self.db.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row[1] for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events(created_at, kind, payload) VALUES (?, ?, ?)",
            (datetime.now(UTC).isoformat(), kind, json.dumps(payload, default=str)),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def summary(self) -> dict[str, Any]:
        counts = dict(
            self.db.execute(
                "SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY kind"
            ).fetchall()
        )
        latest = self.db.execute(
            "SELECT created_at, payload FROM events WHERE kind = 'cycle' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_events": sum(counts.values()),
            "counts": counts,
            "latest_cycle_at": latest[0] if latest else None,
            "latest_cycle": json.loads(latest[1]) if latest else None,
            "paper": self.paper_summary(),
        }

    def init_paper_account(self, initial_equity: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT OR IGNORE INTO paper_account
               (id, initial_equity, cash, peak_equity, updated_at)
               VALUES (1, ?, ?, ?, ?)""",
            (initial_equity, initial_equity, initial_equity, now),
        )
        self.db.commit()

    def paper_account(self) -> dict[str, str]:
        row = self.db.execute(
            "SELECT initial_equity, cash, peak_equity FROM paper_account WHERE id = 1"
        ).fetchone()
        if not row:
            raise RuntimeError("Paper account is not initialized.")
        return {"initial_equity": row[0], "cash": row[1], "peak_equity": row[2]}

    def set_paper_account(self, cash: str, peak_equity: str) -> None:
        self.db.execute(
            "UPDATE paper_account SET cash = ?, peak_equity = ?, updated_at = ? WHERE id = 1",
            (cash, peak_equity, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def inventory(self) -> list[dict[str, str]]:
        rows = self.db.execute(
            """SELECT token_id, condition_id, question, outcome, shares
               FROM paper_inventory WHERE CAST(shares AS REAL) > 0"""
        ).fetchall()
        return [
            {
                "token_id": row[0],
                "condition_id": row[1],
                "question": row[2],
                "outcome": row[3],
                "shares": row[4],
            }
            for row in rows
        ]

    def adjust_inventory(
        self,
        token_id: str,
        condition_id: str,
        question: str,
        outcome: str,
        delta: str,
    ) -> None:
        current = self.db.execute(
            "SELECT shares FROM paper_inventory WHERE token_id = ?", (token_id,)
        ).fetchone()
        from decimal import Decimal

        shares = Decimal(current[0]) if current else Decimal(0)
        shares += Decimal(delta)
        if shares < 0:
            raise RuntimeError("Paper inventory cannot be negative.")
        self.db.execute(
            """INSERT INTO paper_inventory(token_id, condition_id, question, outcome, shares)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(token_id) DO UPDATE SET shares = excluded.shares""",
            (token_id, condition_id, question, outcome, str(shares)),
        )
        self.db.commit()

    def open_quotes(self) -> list[dict[str, str | int]]:
        rows = self.db.execute(
            """SELECT id, token_id, condition_id, outcome, side, price, size
               FROM paper_quotes WHERE status = 'OPEN'"""
        ).fetchall()
        keys = ("id", "token_id", "condition_id", "outcome", "side", "price", "size")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def add_quote(
        self,
        token_id: str,
        condition_id: str,
        outcome: str,
        side: str,
        price: str,
        size: str,
    ) -> int:
        cursor = self.db.execute(
            """INSERT INTO paper_quotes
               (token_id, condition_id, outcome, side, price, size, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)""",
            (
                token_id,
                condition_id,
                outcome,
                side,
                price,
                size,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def close_quote(self, quote_id: int, status: str) -> None:
        self.db.execute(
            """UPDATE paper_quotes SET status = ?, closed_at = ?
               WHERE id = ? AND status = 'OPEN'""",
            (status, datetime.now(UTC).isoformat(), quote_id),
        )
        self.db.commit()

    def add_fill(
        self,
        quote_id: int,
        token_id: str,
        side: str,
        price: str,
        size: str,
    ) -> None:
        from decimal import Decimal

        notional = Decimal(price) * Decimal(size)
        self.db.execute(
            """INSERT INTO paper_fills
               (quote_id, token_id, side, price, size, notional, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                quote_id,
                token_id,
                side,
                price,
                size,
                str(notional),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.close_quote(quote_id, "FILLED")

    def paper_summary(self) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT initial_equity, cash, peak_equity FROM paper_account WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        fills = self.db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        open_quotes = self.db.execute(
            "SELECT COUNT(*) FROM paper_quotes WHERE status = 'OPEN'"
        ).fetchone()[0]
        inventory = self.db.execute(
            "SELECT COUNT(*) FROM paper_inventory WHERE CAST(shares AS REAL) > 0"
        ).fetchone()[0]
        metrics = self.db.execute(
            "SELECT realized_pnl, fees FROM paper_metrics WHERE id = 1"
        ).fetchone()
        ticks = self.db.execute("SELECT COUNT(*) FROM market_ticks").fetchone()[0]
        return {
            "initial_equity": row[0],
            "cash": row[1],
            "peak_equity": row[2],
            "fills": fills,
            "open_quotes": open_quotes,
            "inventory_tokens": inventory,
            "realized_pnl": metrics[0],
            "fees": metrics[1],
            "market_ticks": ticks,
        }

    def directional(self) -> dict[str, dict[str, str]]:
        rows = self.db.execute(
            """SELECT token_id, shares, cost_basis, opened_at FROM paper_directional
               WHERE CAST(shares AS REAL) > 0"""
        ).fetchall()
        return {
            row[0]: {
                "shares": row[1],
                "cost_basis": row[2],
                "opened_at": row[3] or datetime.now(UTC).isoformat(),
            }
            for row in rows
        }

    def add_directional_buy(self, token_id: str, shares: str, cost: str) -> None:
        from decimal import Decimal

        current = self.directional().get(token_id)
        new_shares = Decimal(shares) + (
            Decimal(current["shares"]) if current else Decimal(0)
        )
        new_cost = Decimal(cost) + (
            Decimal(current["cost_basis"]) if current else Decimal(0)
        )
        opened_at = current["opened_at"] if current else datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT INTO paper_directional(token_id, shares, cost_basis, opened_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(token_id) DO UPDATE SET
               shares = excluded.shares, cost_basis = excluded.cost_basis,
               opened_at = COALESCE(paper_directional.opened_at, excluded.opened_at)""",
            (token_id, str(new_shares), str(new_cost), opened_at),
        )
        self.db.commit()

    def consume_directional(self, token_id: str, shares: str) -> str:
        from decimal import Decimal

        current = self.directional().get(token_id)
        if not current or Decimal(current["shares"]) < Decimal(shares):
            raise RuntimeError("Insufficient directional paper position.")
        old_shares = Decimal(current["shares"])
        old_cost = Decimal(current["cost_basis"])
        sold_shares = Decimal(shares)
        sold_cost = old_cost * sold_shares / old_shares
        remaining = old_shares - sold_shares
        if remaining == 0:
            self.db.execute(
                "DELETE FROM paper_directional WHERE token_id = ?", (token_id,)
            )
        else:
            self.db.execute(
                """UPDATE paper_directional SET shares = ?, cost_basis = ?
                   WHERE token_id = ?""",
                (str(remaining), str(old_cost - sold_cost), token_id),
            )
        self.db.commit()
        return str(sold_cost)

    def add_paper_metrics(self, realized_pnl: str, fees: str) -> None:
        from decimal import Decimal

        current = self.db.execute(
            "SELECT realized_pnl, fees FROM paper_metrics WHERE id = 1"
        ).fetchone()
        self.db.execute(
            "UPDATE paper_metrics SET realized_pnl = ?, fees = ? WHERE id = 1",
            (
                str(Decimal(current[0]) + Decimal(realized_pnl)),
                str(Decimal(current[1]) + Decimal(fees)),
            ),
        )
        self.db.commit()

    def record_tick(
        self,
        condition_id: str,
        token_id: str,
        bid: str,
        ask: str,
        event_type: str,
    ) -> None:
        from decimal import Decimal

        midpoint = (Decimal(bid) + Decimal(ask)) / Decimal(2)
        self.db.execute(
            """INSERT INTO market_ticks
               (created_at, condition_id, token_id, bid, ask, midpoint, event_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(UTC).isoformat(),
                condition_id,
                token_id,
                bid,
                ask,
                str(midpoint),
                event_type,
            ),
        )
        self.db.commit()

    def midpoint_jump(self, condition_id: str, since_iso: str) -> str:
        from decimal import Decimal

        rows = self.db.execute(
            """SELECT midpoint FROM market_ticks
               WHERE condition_id = ? AND created_at >= ?
               ORDER BY id""",
            (condition_id, since_iso),
        ).fetchall()
        if len(rows) < 2:
            return "0"
        values = [Decimal(row[0]) for row in rows]
        return str(max(values) - min(values))

    def latest_book(self, token_id: str) -> dict[str, str] | None:
        row = self.db.execute(
            """SELECT condition_id, bid, ask, midpoint, created_at
               FROM market_ticks WHERE token_id = ? ORDER BY id DESC LIMIT 1""",
            (token_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "condition_id": row[0],
            "bid": row[1],
            "ask": row[2],
            "midpoint": row[3],
            "created_at": row[4],
        }

    def dashboard_state(self) -> dict[str, Any]:
        from decimal import Decimal

        account = self.paper_account()
        directional = self.directional()
        positions: list[dict[str, Any]] = []
        inventory_value = Decimal(0)
        directional_unrealized = Decimal(0)
        for item in self.inventory():
            book = self.latest_book(item["token_id"])
            shares = Decimal(item["shares"])
            midpoint = Decimal(book["midpoint"]) if book else Decimal(0)
            market_value = shares * midpoint
            inventory_value += market_value
            position = directional.get(item["token_id"])
            directional_shares = Decimal(position["shares"]) if position else Decimal(0)
            cost_basis = Decimal(position["cost_basis"]) if position else Decimal(0)
            directional_value = directional_shares * midpoint
            unrealized = directional_value - cost_basis
            directional_unrealized += unrealized
            positions.append(
                {
                    **item,
                    "midpoint": str(midpoint),
                    "bid": book["bid"] if book else None,
                    "ask": book["ask"] if book else None,
                    "book_at": book["created_at"] if book else None,
                    "market_value": str(market_value),
                    "directional_shares": str(directional_shares),
                    "average_cost": (
                        str(cost_basis / directional_shares)
                        if directional_shares > 0
                        else None
                    ),
                    "unrealized_pnl": str(unrealized),
                    "opened_at": position["opened_at"] if position else None,
                }
            )
        metrics = self.db.execute(
            "SELECT realized_pnl, fees FROM paper_metrics WHERE id = 1"
        ).fetchone()
        fills = self.db.execute(
            """SELECT token_id, side, price, size, notional, created_at
               FROM paper_fills ORDER BY id DESC LIMIT 50"""
        ).fetchall()
        quotes = self.open_quotes()
        events = self.db.execute(
            """SELECT created_at, kind, payload FROM events
               ORDER BY id DESC LIMIT 50"""
        ).fetchall()
        cash = Decimal(account["cash"])
        return {
            "mode": "paper",
            "account": {
                **account,
                "equity": str(cash + inventory_value),
                "inventory_value": str(inventory_value),
                "unrealized_pnl": str(directional_unrealized),
                "realized_pnl": metrics[0],
                "fees": metrics[1],
            },
            "positions": positions,
            "quotes": quotes,
            "fills": [
                {
                    "token_id": row[0],
                    "side": row[1],
                    "price": row[2],
                    "size": row[3],
                    "notional": row[4],
                    "created_at": row[5],
                }
                for row in fills
            ],
            "events": [
                {
                    "created_at": row[0],
                    "kind": row[1],
                    "payload": json.loads(row[2]),
                }
                for row in events
            ],
        }
