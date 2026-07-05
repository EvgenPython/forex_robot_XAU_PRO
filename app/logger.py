import csv
import threading
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
_LOG_LOCK = threading.RLock()


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


def get_month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}_{now.month:02d}"


def get_events_log_file() -> Path:
    return LOGS_DIR / f"events_{get_month_key()}.log"


def get_trades_log_file() -> Path:
    return LOGS_DIR / f"trades_{get_month_key()}.csv"


def _load_rotation_settings() -> tuple[int, int]:
    try:
        from app.monitoring_config import load_monitoring_config

        config = load_monitoring_config()
        max_bytes = max(1024 * 1024, int(config.get("events_log_max_bytes", 10 * 1024 * 1024)))
        backup_count = max(1, int(config.get("events_log_backup_count", 5)))
        return max_bytes, backup_count
    except Exception:
        return 10 * 1024 * 1024, 5


def _rotate_events_log_if_needed(log_file: Path, incoming_size: int) -> None:
    max_bytes, backup_count = _load_rotation_settings()
    try:
        current_size = log_file.stat().st_size if log_file.exists() else 0
    except OSError:
        current_size = 0

    if current_size + max(0, int(incoming_size)) <= max_bytes:
        return

    oldest = Path(f"{log_file}.{backup_count}")
    try:
        if oldest.exists():
            oldest.unlink()
    except OSError:
        pass

    for index in range(backup_count - 1, 0, -1):
        source = Path(f"{log_file}.{index}")
        target = Path(f"{log_file}.{index + 1}")
        try:
            if source.exists():
                source.replace(target)
        except OSError:
            pass

    try:
        if log_file.exists():
            log_file.replace(Path(f"{log_file}.1"))
    except OSError:
        # Ошибка ротации не должна останавливать торгового робота.
        pass


def init_trades_log() -> None:
    trades_log_file = get_trades_log_file()

    with _LOG_LOCK:
        if trades_log_file.exists():
            return

        with open(
            trades_log_file,
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=TRADE_FIELDNAMES,
            )
            writer.writeheader()


def log_trade_event(**kwargs) -> None:
    init_trades_log()

    row = {field: kwargs.get(field, "") for field in TRADE_FIELDNAMES}
    if not row["time"]:
        row["time"] = utc_now()

    with _LOG_LOCK:
        with open(
            get_trades_log_file(),
            "a",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=TRADE_FIELDNAMES,
            )
            writer.writerow(row)


def log_event(message: str) -> None:
    line = f"[{utc_now()}] {message}"
    encoded_size = len((line + "\n").encode("utf-8"))

    with _LOG_LOCK:
        log_file = get_events_log_file()
        _rotate_events_log_if_needed(log_file, encoded_size)
        with open(
            log_file,
            "a",
            encoding="utf-8",
        ) as file:
            file.write(line + "\n")

    print(line)
