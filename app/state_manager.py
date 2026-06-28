import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

STATE_FILE = STATE_DIR / "XAUUSD_state.json"


def load_state():
    if not STATE_FILE.exists():
        return {
            "active_trade": None,
            "last_m15_candle": None,
        }

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            indent=4,
            ensure_ascii=False,
        )


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
