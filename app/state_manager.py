import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.json_store import atomic_write_json, load_json_file_detailed


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "XAUUSD_state.json"
STATE_SCHEMA_VERSION = 6
STATE_VERSION = STATE_SCHEMA_VERSION  # backward-compatible key used by older builds
ROBOT_VERSION = "3.4.0-final-safety"
MAX_PROCESSED_CLOSE_DEALS = 200


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_safety() -> dict:
    return {
        "blocked": False,
        "reason_code": None,
        "message": None,
        "detected_at": None,
        "notification_sent": False,
        "notification_sent_at": None,
        "backup_file": None,
        "details": {},
        "last_recovery": None,
    }


DEFAULT_STATE = {
    "state_schema_version": STATE_SCHEMA_VERSION,
    "state_version": STATE_VERSION,
    "robot_version": ROBOT_VERSION,
    "strategy_context": None,
    "account_context": None,
    "safety": _default_safety(),
    "active_trade": None,
    "pending_open": None,
    "last_m15_candle": None,
    "last_signal": None,
    "daily_guard": None,
    "account_guard": None,
    "processed_close_deals": [],
    "last_closed_trade": None,
    "warnings": {},
}


def calculate_strategy_config_hash(settings: dict) -> str:
    canonical = json.dumps(
        settings,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_safety(value: Any) -> dict:
    safety = copy.deepcopy(value) if isinstance(value, dict) else {}
    default = _default_safety()
    for key, default_value in default.items():
        safety.setdefault(key, copy.deepcopy(default_value))

    safety["blocked"] = bool(safety.get("blocked", False))
    safety["notification_sent"] = bool(safety.get("notification_sent", False))
    if not isinstance(safety.get("details"), dict):
        safety["details"] = {}
    if safety.get("last_recovery") is not None and not isinstance(
        safety.get("last_recovery"), dict
    ):
        safety["last_recovery"] = None
    return safety


def _normalize_account_context(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    try:
        account_login = int(value.get("account_login"))
    except (TypeError, ValueError):
        return None

    server = str(value.get("server", "") or "").strip()
    if account_login <= 0 or not server:
        return None

    normalized = copy.deepcopy(value)
    normalized["account_login"] = account_login
    normalized["server"] = server
    normalized.setdefault("bound_at", utc_now_iso())
    normalized.setdefault("balance_at_bind", None)
    return normalized


def _normalize_strategy_context(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    config_hash = str(value.get("config_hash", "") or "").strip()
    if not config_hash:
        return None

    normalized = copy.deepcopy(value)
    normalized["config_hash"] = config_hash
    normalized.setdefault("updated_at", utc_now_iso())
    normalized.setdefault("previous_config_hash", None)
    return normalized


def _normalize_active_trade(trade: Any) -> dict | None:
    if not isinstance(trade, dict):
        return None

    normalized = copy.deepcopy(trade)
    is_legacy_trade = "open_notification_sent" not in normalized
    normalized.pop("dry_run", None)

    if "open_time" in normalized and "opened_at" not in normalized:
        normalized["opened_at"] = normalized.get("open_time")

    if "ticket" in normalized and "order_ticket" not in normalized:
        normalized["order_ticket"] = normalized.get("ticket")

    normalized.setdefault("request_id", None)
    normalized.setdefault("order_comment", None)
    normalized.setdefault("signal_entry_price", normalized.get("entry_price"))
    normalized.setdefault("initial_stop_loss", normalized.get("stop_loss"))
    normalized.setdefault("position_ticket", None)
    normalized.setdefault("position_id", None)
    normalized.setdefault("opening_deal_ticket", None)
    normalized.setdefault("magic", 260611)
    normalized.setdefault("balance_at_open", None)
    normalized.setdefault("server", None)
    normalized.setdefault("account_login", None)
    normalized.setdefault("strategy_config_hash", None)
    normalized.setdefault("requested_close_reason", None)
    normalized.setdefault("close_requested_at", None)
    normalized.setdefault("close_request_status", None)
    normalized.setdefault("close_request_order_ticket", None)
    normalized.setdefault("close_request_deal_ticket", None)
    normalized.setdefault("missing_position_since", None)
    normalized.setdefault("history_lookup_attempts", 0)
    normalized.setdefault("history_warning_sent", False)
    normalized.setdefault("open_notification_sent", bool(is_legacy_trade))
    normalized.setdefault("pending_sl_notification", None)
    normalized.setdefault("processed_close_deal_ticket", None)
    normalized.setdefault("current_price", None)
    normalized.setdefault("floating_profit", 0.0)
    normalized.setdefault("floating_percent", 0.0)
    normalized.setdefault("cache_updated_at", None)
    normalized.setdefault("position_integrity_checked_at", None)
    normalized.setdefault("execution_anomaly", None)
    normalized.setdefault("tp1_hit", False)
    normalized.setdefault("tp2_hit", False)
    normalized.setdefault("tp3_hit", False)
    normalized.setdefault("breakeven_active", False)
    normalized.setdefault("candles_in_trade", 0)

    return normalized


def _normalize_pending_open(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None

    pending = copy.deepcopy(value)
    pending.pop("dry_run", None)
    pending.setdefault("request_id", None)
    pending.setdefault("order_comment", None)
    pending.setdefault("status", "prepared")
    pending.setdefault("created_at", utc_now_iso())
    pending.setdefault("sent_at", None)
    pending.setdefault("order_ticket", 0)
    pending.setdefault("deal_ticket", 0)
    pending.setdefault("warning_sent", False)
    pending.setdefault("warning_sent_at", None)
    pending.setdefault("account_login", None)
    pending.setdefault("server", None)
    pending.setdefault("strategy_config_hash", None)
    pending.setdefault("recovery_attempts", 0)
    pending.setdefault("execution_error", None)
    pending.setdefault("execution_retcode", None)
    pending.setdefault("executed_volume", None)
    return pending


def normalize_state(data: Any) -> dict:
    state = copy.deepcopy(data) if isinstance(data, dict) else {}

    for key, default_value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = copy.deepcopy(default_value)

    state["state_schema_version"] = STATE_SCHEMA_VERSION
    state["state_version"] = STATE_VERSION
    state["robot_version"] = ROBOT_VERSION
    state["strategy_context"] = _normalize_strategy_context(
        state.get("strategy_context")
    )
    state["account_context"] = _normalize_account_context(state.get("account_context"))
    state["safety"] = _normalize_safety(state.get("safety"))
    state["active_trade"] = _normalize_active_trade(state.get("active_trade"))
    state["pending_open"] = _normalize_pending_open(state.get("pending_open"))

    current_hash = None
    if isinstance(state.get("strategy_context"), dict):
        current_hash = state["strategy_context"].get("config_hash")
    if state.get("active_trade") is not None and not state["active_trade"].get(
        "strategy_config_hash"
    ):
        state["active_trade"]["strategy_config_hash"] = current_hash
    if state.get("pending_open") is not None and not state["pending_open"].get(
        "strategy_config_hash"
    ):
        state["pending_open"]["strategy_config_hash"] = current_hash

    processed = state.get("processed_close_deals")
    if not isinstance(processed, list):
        processed = []

    unique_processed: list[int] = []
    for value in processed:
        try:
            ticket = int(value)
        except (TypeError, ValueError):
            continue
        if ticket > 0 and ticket not in unique_processed:
            unique_processed.append(ticket)

    state["processed_close_deals"] = unique_processed[-MAX_PROCESSED_CLOSE_DEALS:]

    if not isinstance(state.get("warnings"), dict):
        state["warnings"] = {}

    return state


def _unique_corrupted_backup_path() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return STATE_DIR / f"XAUUSD_state.corrupted_{timestamp}.json"


def _backup_corrupted_state() -> Path | None:
    if not STATE_FILE.exists():
        return None

    backup_file = _unique_corrupted_backup_path()
    try:
        os.replace(STATE_FILE, backup_file)
        return backup_file
    except OSError:
        return None


def _make_blocked_recovery_state(error_text: str, backup_file: Path | None) -> dict:
    state = copy.deepcopy(DEFAULT_STATE)
    backup_text = str(backup_file) if backup_file is not None else None
    message = (
        "Основной файл состояния повреждён или недоступен. Новые сделки запрещены "
        "до проверки открытых позиций MT5."
    )
    state["safety"] = {
        **_default_safety(),
        "blocked": True,
        "reason_code": "STATE_CORRUPTED",
        "message": message,
        "detected_at": utc_now_iso(),
        "backup_file": backup_text,
        "details": {"load_error": error_text},
    }
    state["warnings"]["state_corrupted"] = {
        "message": message,
        "error": error_text,
        "backup_file": backup_text,
        "detected_at": utc_now_iso(),
    }
    return normalize_state(state)


def load_state() -> dict:
    result = load_json_file_detailed(STATE_FILE)

    if result["status"] == "missing":
        return copy.deepcopy(DEFAULT_STATE)

    raw = result.get("data")
    if result["status"] == "ok" and isinstance(raw, dict):
        return normalize_state(raw)

    error_text = str(result.get("error") or "Корневое значение JSON не является объектом")
    backup_file = _backup_corrupted_state()
    recovery_state = _make_blocked_recovery_state(error_text, backup_file)
    try:
        atomic_write_json(STATE_FILE, recovery_state)
    except OSError:
        # Return a blocked in-memory state even when the disk is temporarily unavailable.
        pass
    return recovery_state


def save_state(state: dict) -> None:
    atomic_write_json(STATE_FILE, normalize_state(state))


def _set_safety_on_state(
    state: dict,
    reason_code: str,
    message: str,
    *,
    backup_file: str | None = None,
    details: dict | None = None,
) -> bool:
    safety = _normalize_safety(state.get("safety"))
    same_incident = (
        safety.get("blocked", False)
        and safety.get("reason_code") == reason_code
        and safety.get("message") == message
    )
    if same_incident:
        state["safety"] = safety
        return False

    previous_recovery = safety.get("last_recovery")
    state["safety"] = {
        **_default_safety(),
        "blocked": True,
        "reason_code": str(reason_code),
        "message": str(message),
        "detected_at": utc_now_iso(),
        "backup_file": backup_file,
        "details": copy.deepcopy(details or {}),
        "last_recovery": previous_recovery,
    }
    return True


def block_state_safety(
    reason_code: str,
    message: str,
    *,
    backup_file: str | None = None,
    details: dict | None = None,
) -> bool:
    state = load_state()
    changed = _set_safety_on_state(
        state,
        reason_code,
        message,
        backup_file=backup_file,
        details=details,
    )
    if changed:
        save_state(state)
    return changed


def clear_state_safety(recovery_message: str) -> bool:
    state = load_state()
    safety = _normalize_safety(state.get("safety"))
    if not safety.get("blocked", False):
        return False

    previous_reason = safety.get("reason_code")
    previous_message = safety.get("message")
    recovery = {
        "previous_reason_code": previous_reason,
        "previous_message": previous_message,
        "message": str(recovery_message),
        "recovered_at": utc_now_iso(),
        "notification_sent": False,
        "notification_sent_at": None,
    }
    state["safety"] = {
        **_default_safety(),
        "last_recovery": recovery,
    }
    save_state(state)
    return True


def get_state_safety() -> dict:
    return _normalize_safety(load_state().get("safety"))


def mark_state_safety_notification_sent() -> None:
    state = load_state()
    safety = _normalize_safety(state.get("safety"))
    if not safety.get("blocked", False):
        return
    safety["notification_sent"] = True
    safety["notification_sent_at"] = utc_now_iso()
    state["safety"] = safety
    save_state(state)


def get_pending_safety_recovery() -> dict | None:
    recovery = get_state_safety().get("last_recovery")
    if not isinstance(recovery, dict):
        return None
    if recovery.get("notification_sent", False):
        return None
    return recovery


def mark_safety_recovery_notification_sent() -> None:
    state = load_state()
    safety = _normalize_safety(state.get("safety"))
    recovery = safety.get("last_recovery")
    if not isinstance(recovery, dict):
        return
    recovery["notification_sent"] = True
    recovery["notification_sent_at"] = utc_now_iso()
    safety["last_recovery"] = recovery
    state["safety"] = safety
    save_state(state)


def ensure_runtime_metadata(settings: dict) -> dict:
    current_hash = calculate_strategy_config_hash(settings)
    state = load_state()
    previous_context = _normalize_strategy_context(state.get("strategy_context"))
    previous_hash = previous_context.get("config_hash") if previous_context else None

    state["state_schema_version"] = STATE_SCHEMA_VERSION
    state["state_version"] = STATE_VERSION
    state["robot_version"] = ROBOT_VERSION

    if previous_hash != current_hash:
        state["strategy_context"] = {
            "config_hash": current_hash,
            "previous_config_hash": previous_hash,
            "updated_at": utc_now_iso(),
        }
        if previous_hash and (state.get("active_trade") or state.get("pending_open")):
            state.setdefault("warnings", {})["strategy_config_changed_during_trade"] = {
                "previous_hash": previous_hash,
                "current_hash": current_hash,
                "detected_at": utc_now_iso(),
                "message": (
                    "Настройки стратегии изменились при активной или ожидающей сделке. "
                    "Сохранённые уровни сделки остаются приоритетными."
                ),
            }
        else:
            state.setdefault("warnings", {}).pop(
                "strategy_config_changed_during_trade", None
            )
    elif previous_context is None:
        state["strategy_context"] = {
            "config_hash": current_hash,
            "previous_config_hash": None,
            "updated_at": utc_now_iso(),
        }

    if isinstance(state.get("active_trade"), dict) and not state["active_trade"].get(
        "strategy_config_hash"
    ):
        state["active_trade"]["strategy_config_hash"] = previous_hash or current_hash
    if isinstance(state.get("pending_open"), dict) and not state["pending_open"].get(
        "strategy_config_hash"
    ):
        state["pending_open"]["strategy_config_hash"] = previous_hash or current_hash

    save_state(state)
    return {
        "robot_version": ROBOT_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "config_hash": current_hash,
        "changed": bool(previous_hash and previous_hash != current_hash),
        "previous_hash": previous_hash,
    }


def get_current_strategy_config_hash() -> str | None:
    context = _normalize_strategy_context(load_state().get("strategy_context"))
    return context.get("config_hash") if context else None


def _trade_account_identity(state: dict) -> tuple[int | None, str | None]:
    for key in ("active_trade", "pending_open"):
        item = state.get(key)
        if not isinstance(item, dict):
            continue

        try:
            login = int(item.get("account_login"))
        except (TypeError, ValueError):
            login = None

        server = str(item.get("server", "") or "").strip() or None
        if login and server:
            return login, server

    return None, None


def _reset_account_scoped_state(state: dict) -> None:
    state["active_trade"] = None
    state["pending_open"] = None
    state["last_m15_candle"] = None
    state["last_signal"] = None
    state["daily_guard"] = None
    state["account_guard"] = None
    state["processed_close_deals"] = []
    state["last_closed_trade"] = None
    # Preserve safety and corruption diagnostics. Only account-scoped warnings reset.
    preserved = {}
    for key in ("state_corrupted",):
        if key in state.get("warnings", {}):
            preserved[key] = state["warnings"][key]
    state["warnings"] = preserved


def ensure_account_context(
    account_login: int,
    server: str,
    account_balance: float,
) -> dict:
    current_login = int(account_login)
    current_server = str(server or "").strip()
    if current_login <= 0 or not current_server:
        return {
            "safe": False,
            "changed": False,
            "reason": "Не удалось определить текущий счёт MT5",
        }

    state = load_state()
    context = _normalize_account_context(state.get("account_context"))
    trade_login, trade_server = _trade_account_identity(state)
    has_trade_state = bool(state.get("active_trade") or state.get("pending_open"))

    if context is None and has_trade_state:
        if trade_login != current_login or trade_server != current_server:
            reason = (
                "Файл состояния содержит активную или ожидающую сделку другого "
                f"или неизвестного счёта: состояние={trade_login}@{trade_server}, "
                f"текущий={current_login}@{current_server}"
            )
            state.setdefault("warnings", {})["account_context_mismatch"] = {
                "reason": reason,
                "detected_at": utc_now_iso(),
            }
            _set_safety_on_state(
                state,
                "ACCOUNT_CONTEXT_MISMATCH",
                reason,
                details={
                    "state_login": trade_login,
                    "state_server": trade_server,
                    "current_login": current_login,
                    "current_server": current_server,
                },
            )
            save_state(state)
            return {"safe": False, "changed": False, "reason": reason}

        state["account_context"] = {
            "account_login": current_login,
            "server": current_server,
            "bound_at": utc_now_iso(),
            "balance_at_bind": float(account_balance),
        }
        state.setdefault("warnings", {}).pop("account_context_mismatch", None)
        safety = _normalize_safety(state.get("safety"))
        if safety.get("reason_code") == "ACCOUNT_CONTEXT_MISMATCH":
            recovery = {
                "previous_reason_code": "ACCOUNT_CONTEXT_MISMATCH",
                "previous_message": safety.get("message"),
                "message": "Контекст счёта снова совпадает с активной или ожидающей сделкой.",
                "recovered_at": utc_now_iso(),
                "notification_sent": False,
                "notification_sent_at": None,
            }
            state["safety"] = {**_default_safety(), "last_recovery": recovery}
        save_state(state)
        return {"safe": True, "changed": True, "reason": "Старое состояние сделки привязано к текущему счёту"}

    if context is None:
        _reset_account_scoped_state(state)
        state["account_context"] = {
            "account_login": current_login,
            "server": current_server,
            "bound_at": utc_now_iso(),
            "balance_at_bind": float(account_balance),
        }
        save_state(state)
        return {
            "safe": True,
            "changed": True,
            "reason": "Старое непривязанное состояние сброшено для текущего счёта",
        }

    same_account = (
        int(context["account_login"]) == current_login
        and str(context["server"]) == current_server
    )
    if same_account:
        changed = False
        if state.get("warnings", {}).pop("account_context_mismatch", None) is not None:
            changed = True
        safety = _normalize_safety(state.get("safety"))
        if safety.get("reason_code") == "ACCOUNT_CONTEXT_MISMATCH":
            recovery = {
                "previous_reason_code": "ACCOUNT_CONTEXT_MISMATCH",
                "previous_message": safety.get("message"),
                "message": "Правильный контекст счёта MT5 восстановлен.",
                "recovered_at": utc_now_iso(),
                "notification_sent": False,
                "notification_sent_at": None,
            }
            state["safety"] = {**_default_safety(), "last_recovery": recovery}
            changed = True
        if changed:
            save_state(state)
        return {"safe": True, "changed": changed, "reason": "Контекст торгового счёта не изменился"}

    if has_trade_state and trade_login == current_login and trade_server == current_server:
        previous = f"{context['account_login']}@{context['server']}"
        state["account_context"] = {
            "account_login": current_login,
            "server": current_server,
            "bound_at": utc_now_iso(),
            "balance_at_bind": float(account_balance),
            "previous_account": previous,
            "rebound_from_trade_state": True,
        }
        state.setdefault("warnings", {}).pop("account_context_mismatch", None)
        safety = _normalize_safety(state.get("safety"))
        if safety.get("reason_code") in {
            "ACCOUNT_CONTEXT_MISMATCH",
            "PENDING_ACCOUNT_MISMATCH",
        }:
            recovery = {
                "previous_reason_code": safety.get("reason_code"),
                "previous_message": safety.get("message"),
                "message": "Контекст счёта безопасно восстановлен по данным сделки.",
                "recovered_at": utc_now_iso(),
                "notification_sent": False,
                "notification_sent_at": None,
            }
            state["safety"] = {**_default_safety(), "last_recovery": recovery}
        save_state(state)
        return {
            "safe": True,
            "changed": True,
            "reason": f"Контекст счёта восстановлен с {previous} по данным сделки",
        }

    if has_trade_state:
        reason = (
            "Счёт MT5 изменился при наличии активной или ожидающей сделки: "
            f"состояние={context['account_login']}@{context['server']}, "
            f"текущий={current_login}@{current_server}"
        )
        state.setdefault("warnings", {})["account_context_mismatch"] = {
            "reason": reason,
            "detected_at": utc_now_iso(),
        }
        _set_safety_on_state(
            state,
            "ACCOUNT_CONTEXT_MISMATCH",
            reason,
            details={
                "state_login": context["account_login"],
                "state_server": context["server"],
                "current_login": current_login,
                "current_server": current_server,
            },
        )
        save_state(state)
        return {"safe": False, "changed": False, "reason": reason}

    previous = f"{context['account_login']}@{context['server']}"
    _reset_account_scoped_state(state)
    state["account_context"] = {
        "account_login": current_login,
        "server": current_server,
        "bound_at": utc_now_iso(),
        "balance_at_bind": float(account_balance),
        "previous_account": previous,
    }
    save_state(state)
    return {
        "safe": True,
        "changed": True,
        "reason": f"Торговый счёт изменён с {previous}; данные предыдущего счёта сброшены",
    }


def clear_trade() -> None:
    state = load_state()
    state["active_trade"] = None
    save_state(state)


def save_trade(trade_data: dict) -> None:
    state = load_state()
    trade = _normalize_active_trade(trade_data)
    if trade is not None and not trade.get("strategy_config_hash"):
        trade["strategy_config_hash"] = get_current_strategy_config_hash()
    state["active_trade"] = trade
    save_state(state)


def activate_trade(trade_data: dict) -> None:
    state = load_state()
    trade = _normalize_active_trade(trade_data)
    if trade is not None and not trade.get("strategy_config_hash"):
        context = _normalize_strategy_context(state.get("strategy_context"))
        trade["strategy_config_hash"] = context.get("config_hash") if context else None
    state["active_trade"] = trade
    state["pending_open"] = None
    state["warnings"].pop("position_without_state", None)
    safety = _normalize_safety(state.get("safety"))
    if safety.get("reason_code") == "POSITION_WITHOUT_STATE":
        recovery = {
            "previous_reason_code": "POSITION_WITHOUT_STATE",
            "previous_message": safety.get("message"),
            "message": "Позиция успешно связана с состоянием активной сделки.",
            "recovered_at": utc_now_iso(),
            "notification_sent": False,
            "notification_sent_at": None,
        }
        state["safety"] = {**_default_safety(), "last_recovery": recovery}
    save_state(state)


def get_active_trade() -> dict | None:
    return load_state().get("active_trade")


def save_pending_open(pending_data: dict) -> None:
    state = load_state()
    pending = _normalize_pending_open(pending_data)
    if pending is not None and not pending.get("strategy_config_hash"):
        context = _normalize_strategy_context(state.get("strategy_context"))
        pending["strategy_config_hash"] = context.get("config_hash") if context else None
    state["pending_open"] = pending
    save_state(state)


def get_pending_open() -> dict | None:
    return load_state().get("pending_open")


def clear_pending_open() -> None:
    state = load_state()
    state["pending_open"] = None
    save_state(state)


def clear_pending_open_after_recovery(reason: str, signal_candle_time: str | None) -> None:
    state = load_state()
    old_pending = copy.deepcopy(state.get("pending_open"))
    state["pending_open"] = None
    if signal_candle_time:
        state["last_m15_candle"] = str(signal_candle_time)
    state.setdefault("warnings", {})["pending_open_recovered"] = {
        "reason": str(reason),
        "recovered_at": utc_now_iso(),
        "previous_pending": old_pending,
    }
    save_state(state)


def save_last_m15_candle(candle_time: str) -> None:
    state = load_state()
    state["last_m15_candle"] = candle_time
    save_state(state)


def get_last_m15_candle() -> str | None:
    return load_state().get("last_m15_candle")


def save_last_signal(signal_data: dict) -> None:
    state = load_state()
    state["last_signal"] = signal_data
    save_state(state)


def get_last_signal() -> dict | None:
    return load_state().get("last_signal")


def is_close_deal_processed(deal_ticket: int) -> bool:
    try:
        ticket = int(deal_ticket)
    except (TypeError, ValueError):
        return False

    return ticket in load_state().get("processed_close_deals", [])


def finalize_trade_close(deal_ticket: int, close_summary: dict) -> None:
    ticket = int(deal_ticket)
    state = load_state()
    processed = state.get("processed_close_deals", [])

    if ticket not in processed:
        processed.append(ticket)

    state["processed_close_deals"] = processed[-MAX_PROCESSED_CLOSE_DEALS:]
    state["last_closed_trade"] = copy.deepcopy(close_summary)
    state["active_trade"] = None
    state["warnings"].pop("position_without_state", None)
    save_state(state)


def set_warning(key: str, value: Any) -> None:
    state = load_state()
    state["warnings"][key] = value
    save_state(state)


def get_warning(key: str) -> Any:
    return load_state().get("warnings", {}).get(key)


def clear_warning(key: str) -> None:
    state = load_state()
    state["warnings"].pop(key, None)
    save_state(state)
