import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
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
                cost_basis TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_metrics (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                realized_pnl TEXT NOT NULL,
                fees TEXT NOT NULL
            );
            INSERT OR IGNORE INTO paper_metrics(id, realized_pnl, fees)
            VALUES (1, '0', '0');
            """
        )
        self.db.commit()

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
        return {
            "initial_equity": row[0],
            "cash": row[1],
            "peak_equity": row[2],
            "fills": fills,
            "open_quotes": open_quotes,
            "inventory_tokens": inventory,
            "realized_pnl": metrics[0],
            "fees": metrics[1],
        }

    def directional(self) -> dict[str, dict[str, str]]:
        rows = self.db.execute(
            """SELECT token_id, shares, cost_basis FROM paper_directional
               WHERE CAST(shares AS REAL) > 0"""
        ).fetchall()
        return {
            row[0]: {"shares": row[1], "cost_basis": row[2]} for row in rows
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
        self.db.execute(
            """INSERT INTO paper_directional(token_id, shares, cost_basis)
               VALUES (?, ?, ?)
               ON CONFLICT(token_id) DO UPDATE SET
               shares = excluded.shares, cost_basis = excluded.cost_basis""",
            (token_id, str(new_shares), str(new_cost)),
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
        self.db.execute(
            """UPDATE paper_directional SET shares = ?, cost_basis = ?
               WHERE token_id = ?""",
            (str(old_shares - sold_shares), str(old_cost - sold_cost), token_id),
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
