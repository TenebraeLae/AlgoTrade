"""Read-only snapshots of the isolated network acceptance database, never working DB."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def main() -> None:
    """Collect counts and immutable row digests in one consistent read transaction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", choices=("initial-stop", "restart-stop", "quote-stop", "resume-stop"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    db = root / "tbot.db"
    if not db.is_file() or db.resolve() == (root.parent / "data/tbot.db").resolve():
        raise SystemExit("isolated existing database required")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        tables = ("decisions", "trades", "positions", "paper_source_episodes", "event_log", "portfolio_balances")
        report = {
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "db_path": str(db),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_check": conn.execute("PRAGMA foreign_key_check").fetchall(),
            "counts": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables},
            "schema_versions": [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")],
            "decision_groups": conn.execute("SELECT mode, processing_state, verdict, reason, COUNT(*) FROM decisions GROUP BY mode, processing_state, verdict, reason").fetchall(),
            "duplicate_bar_keys": conn.execute("SELECT mode, ticker, source_bar_ts, COUNT(*) FROM decisions WHERE source_bar_ts IS NOT NULL GROUP BY mode, ticker, source_bar_ts HAVING COUNT(*) > 1").fetchall(),
            "episode_groups": conn.execute("SELECT mode, stage, error_type, action, status, COUNT(*), SUM(attempt_count) FROM paper_source_episodes GROUP BY mode, stage, error_type, action, status").fetchall(),
            "balances": conn.execute("SELECT * FROM portfolio_balances").fetchall(),
            "positions": conn.execute("SELECT ticker, side, lots, mode, source FROM positions ORDER BY ticker").fetchall(),
            "trade_modes": conn.execute("SELECT mode, COUNT(*) FROM trades GROUP BY mode").fetchall(),
            "latest_decisions": conn.execute("SELECT id, ts, ticker, source_bar_ts, regime, verdict, reason, processing_state, length(indicators), length(filter_categories) FROM decisions ORDER BY id DESC LIMIT 5").fetchall(),
            "pool_events": [],
            "immutable_snapshots": {},
        }
        for ts, event, ctx in conn.execute("SELECT ts, event, ctx FROM event_log WHERE event LIKE 'pool_refresh_%' ORDER BY ts"):
            payload = json.loads(ctx)
            report["pool_events"].append({
                "ts": ts, "event": event, "slot": payload.get("slot"),
                "generation": payload.get("generation"), "selected": payload.get("selected"),
                "monitoring": payload.get("monitoring"), "dates": payload.get("dates"),
                "index_id": payload.get("index_id"), "error_type": payload.get("error_type"),
                "bootstrap": payload.get("bootstrap"),
            })
        for table in ("decisions", "trades"):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            report["immutable_snapshots"][table] = {
                "max_id": max((r[0] for r in rows), default=0),
                "count": len(rows),
                "sha256": sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
            }
        prior_label = {
            "restart-stop": "initial-stop",
            "quote-stop": "restart-stop",
            "resume-stop": "quote-stop",
        }.get(args.save)
        prior_file = root / f"{prior_label}.json"
        if prior_label is not None and prior_file.is_file():
            prior = json.loads(prior_file.read_text(encoding="utf-8"))
            checks = {}
            for table in ("decisions", "trades"):
                old = prior["immutable_snapshots"][table]
                rows = conn.execute(f"SELECT * FROM {table} WHERE id <= ? ORDER BY id", (old["max_id"],)).fetchall()
                digest = sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                checks[table] = {"prior_count": old["count"], "preserved": len(rows) == old["count"] and digest == old["sha256"]}
            report["restart_preservation"] = checks
        conn.rollback()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.save:
        with (root / f"{args.save}.json").open("x", encoding="utf-8") as target:
            target.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
