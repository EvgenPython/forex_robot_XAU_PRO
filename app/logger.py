import csv
from pathlib import Path
from datetime import datetime, timezone


ROOT_DIR = Path(__file__).resolve().parent.parent

LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TRADES_LOG_FILE = LOGS_DIR / "trades.csv"
EVENTS_LOG_FILE = LOGS_DIR / "events.log"


TRADE_FIELDNAMES = [
    "time",
    "event",
    "symbol",
    "direction",
    "entry_price",
    "stop_loss",
    "tp1",
    "tp2",
    "tp3",
    "volume",
    "score",
    "reasons",
    "ticket",
    "balance",
    "equity",
    "profit",
    "comment",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_trades_log():
    if TRADES_LOG_FILE.exists():
        return

    with open(
        TRADES_LOG_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=TRADE_FIELDNAMES,
        )
        writer.writeheader()


def log_trade_event(**kwargs):
    init_trades_log()

    row = {}

    for field in TRADE_FIELDNAMES:
        row[field] = kwargs.get(field, "")

    if not row["time"]:
        row["time"] = utc_now()

    with open(
        TRADES_LOG_FILE,
        "a",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=TRADE_FIELDNAMES,
        )
        writer.writerow(row)


def log_event(message: str):
    line = f"[{utc_now()}] {message}"

    with open(
        EVENTS_LOG_FILE,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(line + "\n")

    print(line)