from datetime import datetime, timedelta, timezone

from app.logger import log_event
from app.state_manager import load_state, save_state


def get_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _new_account_guard(
    account_balance: float,
    account_login: int,
    server: str,
) -> dict:
    return {
        "account_login": int(account_login),
        "server": str(server),
        "activation_balance": float(account_balance),
        "blocked": False,
        "blocked_since": None,
        "blocked_until": None,
        "block_reason": None,
        "current_equity": float(account_balance),
        "drawdown_percent": 0.0,
        "notification_sent": False,
    }


def ensure_account_guard_state(
    account_balance: float,
    account_login: int,
    server: str,
) -> dict:
    state = load_state()
    account_guard = state.get("account_guard")

    try:
        stored_login = int(account_guard.get("account_login")) if isinstance(account_guard, dict) else None
    except (TypeError, ValueError):
        stored_login = None
    stored_server = (
        str(account_guard.get("server", "") or "").strip()
        if isinstance(account_guard, dict)
        else ""
    )

    identity_matches = (
        stored_login == int(account_login)
        and stored_server == str(server).strip()
    )

    if not isinstance(account_guard, dict) or not identity_matches:
        account_guard = _new_account_guard(
            account_balance=account_balance,
            account_login=account_login,
            server=server,
        )
        state["account_guard"] = account_guard
        save_state(state)
        log_event(
            "Защита счёта инициализирована для счёта "
            f"{account_login}@{server}: начальный_баланс={account_balance}"
        )

    account_guard.setdefault("notification_sent", False)
    account_guard.setdefault("blocked", False)
    account_guard.setdefault("activation_balance", float(account_balance))
    account_guard["account_login"] = int(account_login)
    account_guard["server"] = str(server)

    if not account_guard.get("blocked", False):
        account_guard["blocked_since"] = None
        account_guard["blocked_until"] = None
        account_guard["block_reason"] = None

    return account_guard


def calculate_account_drawdown_percent(
    activation_balance: float,
    current_equity: float,
) -> float:
    if activation_balance <= 0:
        return 0.0

    result = ((current_equity - activation_balance) / activation_balance) * 100
    return 0.0 if result >= 0 else abs(result)


def update_account_guard(
    account_balance: float,
    account_equity: float,
    account_login: int,
    server: str,
    max_drawdown_percent: float = 8.0,
    pause_days: int = 14,
) -> dict:
    state = load_state()
    account_guard = ensure_account_guard_state(
        account_balance=account_balance,
        account_login=account_login,
        server=server,
    )
    now = get_now()

    if account_guard.get("blocked"):
        blocked_until_dt = parse_datetime(account_guard.get("blocked_until"))
        if blocked_until_dt is not None and now >= blocked_until_dt:
            account_guard.update({
                "blocked": False,
                "blocked_since": None,
                "blocked_until": None,
                "block_reason": None,
                "notification_sent": False,
            })
            log_event("ЗАЩИТА СЧЁТА СНЯТА: период блокировки завершён")

    activation_balance = float(account_guard.get("activation_balance", account_balance))
    drawdown_percent = calculate_account_drawdown_percent(
        activation_balance=activation_balance,
        current_equity=float(account_equity),
    )

    account_guard.update({
        "account_login": int(account_login),
        "server": str(server),
        "current_equity": float(account_equity),
        "drawdown_percent": round(drawdown_percent, 2),
        "max_drawdown_percent": float(max_drawdown_percent),
        "pause_days": int(pause_days),
    })

    if (
        not account_guard.get("blocked", False)
        and drawdown_percent >= max_drawdown_percent
    ):
        blocked_since = now
        blocked_until_dt = now + timedelta(days=pause_days)
        account_guard.update({
            "blocked": True,
            "blocked_since": blocked_since.isoformat(),
            "blocked_until": blocked_until_dt.isoformat(),
            "notification_sent": False,
            "block_reason": (
                f"Просадка счёта {drawdown_percent:.2f}% "
                f">= {max_drawdown_percent:.2f}%"
            ),
        })
        log_event(
            f"ЗАЩИТА СЧЁТА СРАБОТАЛА: "
            f"счёт={account_login}@{server} | "
            f"просадка={drawdown_percent:.2f}% >= {max_drawdown_percent:.2f}% | "
            f"блокировка_до={blocked_until_dt.isoformat()}"
        )

    state["account_guard"] = account_guard
    save_state(state)
    return account_guard


def is_account_blocked(account_guard: dict) -> bool:
    return bool(account_guard.get("blocked", False))
