from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.logger import log_event
from app.state_manager import load_state, save_state


GUARD_TIMEZONE = ZoneInfo("Europe/Kyiv")


def get_trading_day_context(now: datetime | None = None) -> dict:
    """Единая граница торгового дня для state и истории MT5."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_local = current.astimezone(GUARD_TIMEZONE)
    start_local = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return {
        "date": start_local.date().isoformat(),
        "timezone": str(GUARD_TIMEZONE),
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": end_local.astimezone(timezone.utc),
        "now_utc": current.astimezone(timezone.utc),
    }


def get_today_key() -> str:
    return str(get_trading_day_context()["date"])


def ensure_daily_guard_state(
    account_balance: float,
    account_login: int,
    server: str,
) -> dict:
    state = load_state()
    day_context = get_trading_day_context()
    today = str(day_context["date"])
    daily_guard = state.get("daily_guard")

    try:
        stored_login = int(daily_guard.get("account_login")) if isinstance(daily_guard, dict) else None
    except (TypeError, ValueError):
        stored_login = None
    stored_server = (
        str(daily_guard.get("server", "") or "").strip()
        if isinstance(daily_guard, dict)
        else ""
    )

    must_initialize = (
        not isinstance(daily_guard, dict)
        or daily_guard.get("date") != today
        or stored_login != int(account_login)
        or stored_server != str(server).strip()
    )

    if must_initialize:
        daily_guard = {
            "date": today,
            "timezone": str(GUARD_TIMEZONE),
            "day_start_utc": day_context["start_utc"].isoformat(),
            "account_login": int(account_login),
            "server": str(server),
            "start_balance": float(account_balance),
            "trading_blocked": False,
            "hard_stop_triggered": False,
            "notification_sent": False,
            "notification_type": None,
            "stop_type": None,
        }
        state["daily_guard"] = daily_guard
        save_state(state)
        log_event(
            f"Дневная защита инициализирована: счёт={account_login}@{server}, "
            f"дата={today}, начальный_баланс={account_balance}"
        )

    daily_guard.setdefault("notification_sent", False)
    daily_guard.setdefault("notification_type", None)
    daily_guard.setdefault("stop_type", None)
    daily_guard["account_login"] = int(account_login)
    daily_guard["server"] = str(server)
    return daily_guard


def calculate_daily_drawdown_percent(
    start_balance: float,
    daily_closed_profit: float,
    current_profit: float,
) -> float:
    if start_balance <= 0:
        return 0.0

    daily_result = float(daily_closed_profit) + float(current_profit)
    if daily_result >= 0:
        return 0.0
    return abs((daily_result / start_balance) * 100)


def update_daily_guard(
    account_balance: float,
    account_equity: float,
    account_profit: float,
    daily_closed_profit: float,
    soft_stop_percent: float,
    hard_stop_percent: float,
    account_login: int,
    server: str,
) -> dict:
    state = load_state()
    daily_guard = ensure_daily_guard_state(
        account_balance=account_balance,
        account_login=account_login,
        server=server,
    )
    previous_stop_type = daily_guard.get("stop_type")
    start_balance = float(daily_guard["start_balance"])

    closed_result = float(daily_closed_profit)
    floating_result = float(account_profit)
    daily_result = closed_result + floating_result
    drawdown_percent = calculate_daily_drawdown_percent(
        start_balance=start_balance,
        daily_closed_profit=closed_result,
        current_profit=floating_result,
    )

    if drawdown_percent >= hard_stop_percent:
        stop_type = "HARD STOP"
        trading_blocked = True
        hard_stop_triggered = True
    elif drawdown_percent >= soft_stop_percent:
        stop_type = "SOFT STOP"
        trading_blocked = True
        hard_stop_triggered = False
    else:
        stop_type = None
        trading_blocked = False
        hard_stop_triggered = False

    daily_guard.update({
        "account_login": int(account_login),
        "server": str(server),
        "current_balance": float(account_balance),
        "current_equity": float(account_equity),
        "current_profit": float(account_profit),
        "closed_result": round(closed_result, 2),
        "floating_result": round(floating_result, 2),
        "daily_result": round(daily_result, 2),
        "drawdown_percent": round(drawdown_percent, 2),
        "trading_blocked": trading_blocked,
        "hard_stop_triggered": hard_stop_triggered,
        "stop_type": stop_type,
    })

    if stop_type != previous_stop_type:
        daily_guard["notification_sent"] = False
        daily_guard["notification_type"] = None

        if stop_type is None and previous_stop_type is not None:
            log_event(f"ДНЕВНАЯ ЗАЩИТА СНЯТА: предыдущий режим={previous_stop_type}")
        elif stop_type is not None:
            threshold = hard_stop_percent if stop_type == "HARD STOP" else soft_stop_percent
            log_event(
                f"ДНЕВНАЯ ЗАЩИТА СРАБОТАЛА ({stop_type}): "
                f"счёт={account_login}@{server} | "
                f"просадка={drawdown_percent:.2f}% >= {threshold:.2f}%"
            )

    state["daily_guard"] = daily_guard
    save_state(state)
    return daily_guard


def is_trading_blocked(daily_guard: dict) -> bool:
    return bool(daily_guard.get("trading_blocked", False))


def is_hard_stop_triggered(daily_guard: dict) -> bool:
    return bool(daily_guard.get("hard_stop_triggered", False))
