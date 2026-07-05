from pathlib import Path

from app.json_store import atomic_write_json, load_json_file


ROOT_DIR = Path(__file__).resolve().parent.parent
WATCHDOG_STATE_FILE = ROOT_DIR / "state" / "watchdog_state.json"

DEFAULT_WATCHDOG_STATE = {
    "active_robot_alert": None,
    "active_robot_alert_reason": None,
    "robot_alert_started_at": None,
    "robot_alert_instance_id": None,
    "active_robot_alert_priority": 0,
    "active_telegram_alert": None,
    "active_telegram_alert_reason": None,
    "telegram_alert_started_at": None,
    "telegram_alert_instance_id": None,
    "active_telegram_alert_priority": 0,
    "last_start_notified_instance_id": None,
    "last_robot_recovery_at": None,
    "last_telegram_recovery_at": None,
}


def _copy_known_fields(data: dict) -> dict:
    """Return only fields supported by the current watchdog state schema."""
    state = dict(DEFAULT_WATCHDOG_STATE)
    for key in DEFAULT_WATCHDOG_STATE:
        if key in data:
            state[key] = data[key]
    return state


def load_watchdog_state() -> dict:
    data = load_json_file(WATCHDOG_STATE_FILE, DEFAULT_WATCHDOG_STATE)
    if not isinstance(data, dict):
        data = {}

    state = _copy_known_fields(data)

    # One-time migration from v2. Legacy fields are intentionally NOT copied
    # into the returned state. Otherwise active_alert would resurrect after a
    # successful recovery and the watchdog would send recovery every cycle.
    legacy_alert = data.get("active_alert")
    if legacy_alert and state.get("active_robot_alert") is None:
        state["active_robot_alert"] = legacy_alert
        state["active_robot_alert_reason"] = data.get("active_alert_reason")
        state["robot_alert_started_at"] = data.get("alert_started_at")
        state["robot_alert_instance_id"] = data.get("alert_instance_id")
        try:
            state["active_robot_alert_priority"] = int(
                data.get("active_alert_priority", 0) or 0
            )
        except (TypeError, ValueError):
            state["active_robot_alert_priority"] = 0

    return state


def save_watchdog_state(state: dict) -> None:
    # Persist only current schema fields. This permanently removes legacy keys
    # such as active_alert after the first save.
    normalized = _copy_known_fields(state if isinstance(state, dict) else {})
    atomic_write_json(WATCHDOG_STATE_FILE, normalized)
