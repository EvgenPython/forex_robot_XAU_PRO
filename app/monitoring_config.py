import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MONITORING_FILE = ROOT_DIR / "config" / "monitoring.json"

DEFAULT_MONITORING = {
    "enabled": True,
    "check_interval_seconds": 30,
    "stale_after_seconds": 180,
    "startup_grace_seconds": 180,
    "max_consecutive_errors": 3,
    "mt5_unavailable_after_seconds": 180,
    "account_unavailable_after_seconds": 180,
    "notify_on_robot_start": True,
    "notify_on_failure": True,
    "notify_on_recovery": True,
    "telegram_health_pulse_seconds": 15,
    "telegram_stale_after_seconds": 90,
    "telegram_max_consecutive_errors": 3,
    "telegram_unavailable_after_seconds": 90,
    "telegram_restart_initial_seconds": 5,
    "telegram_restart_max_seconds": 60,
    "notify_on_telegram_failure": True,
    "notify_on_telegram_recovery": True,
    "position_history_wait_seconds": 60,
    "pending_open_warning_seconds": 60,
    "open_confirmation_timeout_seconds": 10,
    "pending_open_prepared_timeout_seconds": 90,
    "entry_tick_max_age_seconds": 30,
    "entry_tick_max_future_skew_seconds": 5,
    "events_log_max_bytes": 10 * 1024 * 1024,
    "events_log_backup_count": 5,
}


def load_monitoring_config() -> dict:
    config = dict(DEFAULT_MONITORING)

    if MONITORING_FILE.exists():
        with open(MONITORING_FILE, "r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            config.update(loaded)

    return config
