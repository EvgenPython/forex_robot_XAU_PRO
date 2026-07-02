import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

STATE_FILE = STATE_DIR / "XAUUSD_state.json"


DEFAULT_STATE = {
    "active_trade": None,
    "last_m15_candle": None,
    "last_signal": None,
    "daily_guard": None,
    "account_guard": None,
}


def load_state():
    if not STATE_FILE.exists():
        return DEFAULT_STATE.copy()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        backup_file = STATE_DIR / "XAUUSD_state_corrupted.json"

        try:
            os.replace(STATE_FILE, backup_file)
        except Exception:
            pass

        return DEFAULT_STATE.copy()


def save_state(state: dict):
    temp_file = STATE_DIR / "XAUUSD_state.tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            indent=4,
            ensure_ascii=False,
        )
        file.flush()
        os.fsync(file.fileno())

    os.replace(temp_file, STATE_FILE)


def clear_trade():
    state = load_state()
    state["active_trade"] = None
    save_state(state)


def save_trade(trade_data: dict):
    state = load_state()
    state["active_trade"] = trade_data
    save_state(state)


def get_active_trade():
    state = load_state()
    return state.get("active_trade")


def save_last_m15_candle(candle_time: str):
    state = load_state()
    state["last_m15_candle"] = candle_time
    save_state(state)


def get_last_m15_candle():
    state = load_state()
    return state.get("last_m15_candle")


def save_last_signal(signal_data: dict):
    state = load_state()
    state["last_signal"] = signal_data
    save_state(state)


def get_last_signal():
    state = load_state()
    return state.get("last_signal")