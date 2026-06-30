from datetime import datetime, timedelta

from app.logger import log_event
from app.state_manager import load_state, save_state


def get_now() -> datetime:
    return datetime.now()


def ensure_account_guard_state(account_balance: float) -> dict:
    state = load_state()
    account_guard = state.get("account_guard")

    if account_guard is None:
        account_guard = {
            "activation_balance": float(account_balance),
            "blocked": False,
            "blocked_since": None,
            "blocked_until": None,
            "block_reason": None,
            "current_equity": float(account_balance),
            "drawdown_percent": 0.0,
            "notification_sent": False,
        }

        state["account_guard"] = account_guard
        save_state(state)

        log_event(
            f"Account guard initialized: "
            f"activation_balance={account_balance}"
        )

    return account_guard


def calculate_account_drawdown_percent(
    activation_balance: float,
    current_equity: float,
) -> float:
    if activation_balance <= 0:
        return 0.0

    result = ((current_equity - activation_balance) / activation_balance) * 100

    if result >= 0:
        return 0.0

    return abs(result)


def update_account_guard(
    account_balance: float,
    account_equity: float,
    max_drawdown_percent: float = 8.0,
    pause_days: int = 14,
) -> dict:
    state = load_state()
    account_guard = ensure_account_guard_state(account_balance)

    now = get_now()

    blocked_until = account_guard.get("blocked_until")

    if account_guard.get("blocked") and blocked_until:
        try:
            blocked_until_dt = datetime.fromisoformat(blocked_until)

            if now >= blocked_until_dt:
                account_guard["blocked"] = False
                account_guard["blocked_since"] = None
                account_guard["blocked_until"] = None
                account_guard["block_reason"] = None
                account_guard["notification_sent"] = False

                log_event("ACCOUNT GUARD UNBLOCKED: pause period expired")

        except Exception as error:
            log_event(f"Account guard unblock check failed: {error}")

    activation_balance = float(account_guard["activation_balance"])

    drawdown_percent = calculate_account_drawdown_percent(
        activation_balance=activation_balance,
        current_equity=float(account_equity),
    )

    account_guard["current_equity"] = float(account_equity)
    account_guard["drawdown_percent"] = round(drawdown_percent, 2)
    account_guard["max_drawdown_percent"] = float(max_drawdown_percent)
    account_guard["pause_days"] = int(pause_days)

    if (
        not account_guard.get("blocked", False)
        and drawdown_percent >= max_drawdown_percent
    ):
        blocked_since = now
        blocked_until_dt = now + timedelta(days=pause_days)

        account_guard["blocked"] = True
        account_guard["blocked_since"] = blocked_since.isoformat()
        account_guard["blocked_until"] = blocked_until_dt.isoformat()
        account_guard["notification_sent"] = False
        account_guard["block_reason"] = (
            f"Account drawdown {drawdown_percent:.2f}% "
            f">= {max_drawdown_percent:.2f}%"
        )

        log_event(
            f"ACCOUNT GUARD TRIGGERED: "
            f"drawdown={drawdown_percent:.2f}% >= {max_drawdown_percent}% | "
            f"blocked_until={blocked_until_dt.isoformat()}"
        )

    state["account_guard"] = account_guard
    save_state(state)

    return account_guard


def is_account_blocked(account_guard: dict) -> bool:
    return bool(account_guard.get("blocked", False))