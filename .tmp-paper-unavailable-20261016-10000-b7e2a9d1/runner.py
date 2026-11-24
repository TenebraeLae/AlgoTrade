"""Bounded real-network paper acceptance; market data and strategy are never replaced.

Only settings/logging and stop control are wrapped. Production main builds the
sandbox gateway, feed, pool, cycle and SQLite. No account/order operations.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import logging
import os
from pathlib import Path
import signal
import ssl
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("phase", choices=("initial", "restart", "quote", "resume"))
parser.add_argument("--limit-sec", type=int, default=2700)
args = parser.parse_args()
DB = ROOT / "tbot.db"
STOP = ROOT / f"STOP-{args.phase}"
if args.limit_sec <= 0 or STOP.exists():
    raise SystemExit("acceptance preflight: invalid limit or existing stop file")
if (args.phase == "initial" and DB.exists()) or (args.phase != "initial" and not DB.is_file()):
    raise SystemExit("acceptance preflight: unexpected test database state")
if DB.resolve() == (WORKSPACE / "data/tbot.db").resolve():
    raise SystemExit("acceptance preflight: working database forbidden")
CA = WORKSPACE / ".tls-verify/Russian_Trusted_Root_CA.pem"
der = ssl.PEM_cert_to_DER_cert(CA.read_text(encoding="ascii"))
if hashlib.sha256(der).hexdigest().upper() != "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31":
    raise SystemExit("acceptance preflight: official CA fingerprint mismatch")
os.environ.update({
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": str(CA),
    "GRPC_VERBOSITY": "ERROR",
    "TBOT_TRADING_MODE": "paper",
    "TBOT_DB_PATH": str(DB),
    "TBOT_PAPER_STARTING_BALANCE": "10000",
    "TBOT_PAPER_MAX_QUOTE_STALENESS_SEC": "30",
    "TBOT_CANDLE_MIN_INTERVAL_SEC": "0.1",
    "TBOT_CANDLE_MAX_ATTEMPTS": "4",
    "TBOT_CANDLE_RETRY_BASE_SEC": "5",
    "TBOT_CANDLE_MAX_RETRY_WAIT_SEC": "120",
    "TBOT_LOG_LEVEL": "INFO",
})
from trading_bot import main as app
from trading_bot.cancellation import CancellationToken
from trading_bot.config import Settings

original_settings = app.load_settings
original_logging = app.setup_logging


def checked_settings() -> Settings:
    """Validate effective isolated settings before main opens a DB or gateway."""
    settings = original_settings()
    if (
        settings.trading_mode != "paper"
        or settings.db_path.resolve() != DB.resolve()
        or settings.paper_starting_balance != Decimal("10000")
        or settings.paper_max_quote_staleness_sec != 30
        or not settings.paper_source_enabled
        or settings.candle_min_interval_sec != 0.1
        or settings.candle_max_attempts != 4
        or settings.candle_retry_base_sec != 5
        or settings.candle_max_retry_wait_sec != 120
    ):
        raise SystemExit("acceptance preflight: effective settings mismatch")
    return settings


class ConsoleMilestones(logging.Filter):
    """Keep console short while the local file retains sanitized app records."""
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return record.levelno >= logging.WARNING or any(marker in message for marker in (
            "event=paper_bootstrap_stage", "event=paper_pool_applied",
            "event=pool_liquidity_candidate", "event=pool_refresh_",
            "event=acceptance_", "event=tbot_", "tbot ",
        ))


def capture_logging(level: str = "INFO") -> None:
    """Capture logs locally without changing market-data or execution sources."""
    original_logging(level)
    root = logging.getLogger()
    stream = root.handlers[0]
    file_handler = logging.FileHandler(ROOT / f"{args.phase}.log", encoding="utf-8", mode="x")
    file_handler.setFormatter(stream.formatter)
    root.addHandler(file_handler)
    stream.addFilter(ConsoleMilestones())
    logging.getLogger("tinkoff.invest.logging").disabled = True
    logging.getLogger(__name__).info(
        "event=acceptance_start phase=%s db=%s balance=10000 mode=paper target=sandbox "
        "attempts=4 retry_base_sec=5 budget_sec=120 quote_max_age_sec=30 "
        "control=stopfile_or_deadline limit_sec=%s",
        args.phase, DB, args.limit_sec,
    )


class AcceptanceCancellation(CancellationToken):
    """Trigger installed SIGINT on runtime-thread at a stop file or deadline."""
    def __init__(self) -> None:
        super().__init__()
        self._deadline = monotonic() + args.limit_sec
        self._triggered = False

    def is_cancelled(self) -> bool:
        if not self._triggered and (STOP.exists() or monotonic() >= self._deadline):
            self._triggered = True
            logging.getLogger(__name__).info(
                "event=acceptance_stop_request reason=%s action=raise_sigint",
                "stopfile" if STOP.exists() else "deadline",
            )
            signal.raise_signal(signal.SIGINT)
        return super().is_cancelled()


app.load_settings = checked_settings
app.setup_logging = capture_logging
app.CancellationToken = AcceptanceCancellation
code = app.main()
logging.getLogger(__name__).info("event=acceptance_exit phase=%s code=%s", args.phase, code)
logging.shutdown()
raise SystemExit(code)
