from datetime import date

from app.logger import log_event
from app.state_manager import load_state, save_state


def get_today_key() -> str:
    return date.today().isoformat()


def ensure_daily_guard_state(account_balance: float) -> dict:
    state = load_state()
    today = get_today_key()

    daily_guard = state.get("daily_guard")

    if daily_guard is None or daily_guard.get("date") != today:
        daily_guard = {
            "date": today,
            "start_balance": float(account_balance),
            "trading_blocked": False,
            "hard_stop_triggered": False,
        }

        state["daily_guard"] = daily_guard
        save_state(state)

        log_event(
            f"Daily guard initialized: date={today}, "
            f"start_balance={account_balance}"
        )

    return daily_guard


def calculate_daily_drawdown_percent(
    start_balance: float,
    current_equity: float,
) -> float:
    if start_balance <= 0:
        return 0.0

    result = ((current_equity - start_balance) / start_balance) * 100

    if result >= 0:
        return 0.0

    return abs(result)


def update_daily_guard(
    account_balance: float,
    account_equity: float,
    soft_stop_percent: float,
    hard_stop_percent: float,
) -> dict:
    state = load_state()
    daily_guard = ensure_daily_guard_state(account_balance)

    start_balance = float(daily_guard["start_balance"])

    drawdown_percent = calculate_daily_drawdown_percent(
        start_balance=start_balance,
        current_equity=float(account_equity),
    )

    daily_guard["current_equity"] = float(account_equity)
    daily_guard["drawdown_percent"] = round(drawdown_percent, 2)

    if drawdown_percent >= hard_stop_percent:
        daily_guard["trading_blocked"] = True
        daily_guard["hard_stop_triggered"] = True

        log_event(
            f"DAILY HARD STOP TRIGGERED: "
            f"drawdown={drawdown_percent:.2f}% >= {hard_stop_percent}%"
        )

    elif drawdown_percent >= soft_stop_percent:
        daily_guard["trading_blocked"] = True

        log_event(
            f"DAILY SOFT STOP TRIGGERED: "
            f"drawdown={drawdown_percent:.2f}% >= {soft_stop_percent}%"
        )

    state["daily_guard"] = daily_guard
    save_state(state)

    return daily_guard


def is_trading_blocked(daily_guard: dict) -> bool:
    return bool(daily_guard.get("trading_blocked", False))


def is_hard_stop_triggered(daily_guard: dict) -> bool:
    return bool(daily_guard.get("hard_stop_triggered", False))