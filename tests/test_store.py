from pathlib import Path

from polybot.store import AuditStore


def test_store_summary_persists_cycle(tmp_path: Path):
    store = AuditStore(tmp_path / "test.sqlite3")
    store.record("cycle", {"markets": 10})
    store.record("manual_review_candidate", {"id": 1})
    summary = store.summary()
    store.close()

    assert summary["total_events"] == 2
    assert summary["counts"]["cycle"] == 1
    assert summary["latest_cycle"] == {"markets": 10}
