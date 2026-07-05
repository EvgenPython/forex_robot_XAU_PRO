from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.logger import log_event
from app.mt5_client import get_positions
from app.state_manager import (
    clear_state_safety,
    clear_warning,
    get_pending_safety_recovery,
    get_state_safety,
    load_state,
    mark_safety_recovery_notification_sent,
    mark_state_safety_notification_sent,
)
from app.text_ru import translate_safety_reason
from app.telegram_notifier import (
    notify_state_recovery,
    notify_state_warning,
    telegram_notifications_enabled,
)


KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")


def _format_kyiv(value: str | None) -> str:
    if not value:
        return "нет данных"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KYIV_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return str(value)


AUTO_RECOVERABLE_WITH_EMPTY_MT5 = {
    "STATE_CORRUPTED",
    "POSITION_WITHOUT_STATE",
}


def _notification_optional() -> bool:
    return not telegram_notifications_enabled()


def _position_summary(positions: Any) -> str:
    if not positions:
        return "позиций нет"

    parts = []
    for position in list(positions)[:5]:
        parts.append(
            f"ticket={getattr(position, 'ticket', '—')}, "
            f"volume={getattr(position, 'volume', '—')}, "
            f"magic={getattr(position, 'magic', '—')}"
        )
    if len(positions) > 5:
        parts.append(f"ещё {len(positions) - 5}")
    return "; ".join(parts)


def _send_block_notification(safety: dict, symbol: str, positions: Any) -> None:
    if safety.get("notification_sent", False):
        return

    reason_code = str(safety.get("reason_code") or "STATE_SAFETY_BLOCK")
    message = str(safety.get("message") or "Защита состояния заблокировала новые сделки")
    backup_file = safety.get("backup_file")
    details = safety.get("details") if isinstance(safety.get("details"), dict) else {}

    extra = [
        f"Причина: {translate_safety_reason(reason_code)}",
        f"Технический код: {reason_code}",
        f"Инструмент: {symbol}",
        f"MT5: {_position_summary(positions)}",
    ]
    if backup_file:
        extra.append(f"Резервная копия: {backup_file}")
    if details.get("load_error"):
        extra.append(f"Ошибка чтения: {details['load_error']}")

    sent = notify_state_warning(
        "Защита состояния активирована",
        message + "\n\n" + "\n".join(extra),
    )
    if sent or _notification_optional():
        mark_state_safety_notification_sent()
        log_event(f"Уведомление защиты состояния отправлено: причина={reason_code}")


def flush_safety_recovery_notification(symbol: str) -> bool:
    recovery = get_pending_safety_recovery()
    if recovery is None:
        return False

    sent = notify_state_recovery(
        symbol=symbol,
        message=str(recovery.get("message") or "Состояние синхронизировано."),
        previous_reason=translate_safety_reason(
            recovery.get("previous_reason_code")
        ),
        recovered_at=_format_kyiv(recovery.get("recovered_at")),
    )
    if sent or _notification_optional():
        mark_safety_recovery_notification_sent()
        log_event(
            "Уведомление о восстановлении состояния отправлено: "
            f"предыдущая_причина={recovery.get('previous_reason_code')}"
        )
        return True
    return False


def reconcile_state_safety(symbol: str, account) -> str:
    """Resolve state-level safety blocks without risking a duplicate trade.

    Returns: SAFE | BLOCKED | RECOVERED
    """
    safety = get_state_safety()
    if not safety.get("blocked", False):
        flush_safety_recovery_notification(symbol)
        return "SAFE"

    positions = get_positions(symbol=symbol)
    if positions is None:
        _send_block_notification(safety, symbol, [])
        return "BLOCKED"

    state = load_state()
    has_trade_state = bool(state.get("active_trade") or state.get("pending_open"))
    reason_code = str(safety.get("reason_code") or "")

    if (
        reason_code in AUTO_RECOVERABLE_WITH_EMPTY_MT5
        and not has_trade_state
        and len(positions) == 0
    ):
        if reason_code == "STATE_CORRUPTED":
            recovery_message = (
                "Повреждённый файл состояния сохранён в резервную копию. В MT5 открытых "
                "позиций по инструменту нет, поэтому создано чистое безопасное состояние."
            )
        else:
            recovery_message = (
                "Позиция без состояния больше не обнаружена. MT5 и state снова "
                "согласованы, новые сделки разрешены."
            )

        clear_state_safety(recovery_message)
        if reason_code == "POSITION_WITHOUT_STATE":
            clear_warning("position_without_state")
        log_event(
            f"Защита состояния автоматически снята: причина={reason_code}, "
            f"account={getattr(account, 'login', None)}@{getattr(account, 'server', None)}"
        )
        flush_safety_recovery_notification(symbol)
        return "RECOVERED"

    _send_block_notification(safety, symbol, positions)
    return "BLOCKED"
