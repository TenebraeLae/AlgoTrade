"""Read-only evidence collector for this isolated acceptance database."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def main() -> None:
    """Print a consistent read-only snapshot; never open the working database."""
    root = Path(__file__).resolve().parent
    db = root / "tbot.db"
    if not db.is_file() or db.resolve() == (root.parent / "data/tbot.db").resolve():
        raise SystemExit("isolated database required")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        tables = ("decisions", "trades", "positions", "paper_source_episodes", "event_log", "portfolio_balances")
        pool_events: list[dict[str, object]] = []
        snapshots: dict[str, dict[str, object]] = {}
        report: dict[str, object] = {
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "db_path": str(db),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "counts": {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables},
            "schema_versions": [row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")],
            "decision_groups": conn.execute("SELECT mode, processing_state, verdict, reason, COUNT(*) FROM decisions GROUP BY mode, processing_state, verdict, reason ORDER BY mode, processing_state, verdict, reason").fetchall(),
            "duplicate_bar_keys": conn.execute("SELECT mode, ticker, source_bar_ts, COUNT(*) FROM decisions WHERE source_bar_ts IS NOT NULL GROUP BY mode, ticker, source_bar_ts HAVING COUNT(*) > 1").fetchall(),
            "episode_groups": conn.execute("SELECT mode, stage, error_type, action, status, COUNT(*), SUM(attempt_count) FROM paper_source_episodes GROUP BY mode, stage, error_type, action, status").fetchall(),
            "balances": conn.execute("SELECT * FROM portfolio_balances").fetchall(),
            "positions": conn.execute("SELECT ticker, side, lots, mode, source FROM positions ORDER BY ticker").fetchall(),
            "trade_modes": conn.execute("SELECT mode, COUNT(*) FROM trades GROUP BY mode").fetchall(),
            "latest_decisions": conn.execute("SELECT id, ts, ticker, source_bar_ts, regime, verdict, reason, processing_state, length(indicators), length(filter_categories) FROM decisions ORDER BY id DESC LIMIT 5").fetchall(),
            "pool_events": pool_events,
            "immutable_snapshots": snapshots,
        }
        for ts, event, ctx in conn.execute("SELECT ts, event, ctx FROM event_log WHERE event LIKE 'pool_refresh_%' ORDER BY ts"):
            payload = json.loads(ctx)
            pool_events.append({
                "ts": ts, "event": event, "slot": payload.get("slot"),
                "generation": payload.get("generation"), "selected": payload.get("selected"),
                "monitoring": payload.get("monitoring"), "dates": payload.get("dates"),
                "index_id": payload.get("index_id"), "error_type": payload.get("error_type"),
                "bootstrap": payload.get("bootstrap"),
            })
        for table in ("decisions", "trades"):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            snapshots[table] = {
                "max_id": max((row[0] for row in rows), default=0),
                "count": len(rows),
                "sha256": sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            }
        conn.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
