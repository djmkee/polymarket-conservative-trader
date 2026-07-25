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
        self.db.commit()

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events(created_at, kind, payload) VALUES (?, ?, ?)",
            (datetime.now(UTC).isoformat(), kind, json.dumps(payload, default=str)),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
