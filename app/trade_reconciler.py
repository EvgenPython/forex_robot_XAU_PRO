from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.executor import BOT_MAGIC
from app.logger import log_event, log_trade_event
from app.monitoring_config import load_monitoring_config
from app.daily_guard import get_trading_day_context
from app.mt5_client import (
    get_deal_direction,
    get_deals_for_position,
    get_history_deals,
    get_trade_deals_from_history,
    is_closing_deal,
    is_opening_deal,
)
from app.position_manager import (
    find_position_for_pending_open,
    get_open_position,
    get_position_direction,
    get_position_identifier,
    validate_position_integrity,
)
from app.state_manager import (
    block_state_safety,
    clear_pending_open_after_recovery,
    clear_state_safety,
    activate_trade,
    finalize_trade_close,
    get_active_trade,
    get_pending_open,
    get_state_safety,
    get_warning,
    is_close_deal_processed,
    mark_state_safety_notification_sent,
    save_pending_open,
    save_trade,
    set_warning,
)
from app.text_ru import translate_direction
from app.telegram_notifier import (
    notify_open_trade,
    notify_state_warning,
    notify_trade_closed,
    telegram_notifications_enabled,
)


STATS_TIMEZONE = ZoneInfo("Europe/Kyiv")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_datetime(value) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def _notification_is_optional() -> bool:
    return not telegram_notifications_enabled()


def _activate_reconciliation_anomaly(
    reason_code: str,
    title: str,
    message: str,
    details: dict,
) -> None:
    changed = block_state_safety(reason_code, message, details=details)
    if not changed:
        return
    sent = notify_state_warning(title, message)
    if sent or _notification_is_optional():
        mark_state_safety_notification_sent()
    log_event(f"Аномалия сверки состояния: {reason_code}: {message}")


def ensure_open_notification(trade: dict) -> dict:
    if trade.get("open_notification_sent", False):
        return trade

    if _notification_is_optional():
        trade["open_notification_sent"] = True
        save_trade(trade)
        log_event("Уведомление об открытии пропущено: Telegram-уведомления отключены")
        return trade

    sent = notify_open_trade(
        symbol=str(trade.get("symbol", "")),
        direction=str(trade.get("direction", "")),
        entry_price=float(trade.get("entry_price", 0.0)),
        stop_loss=float(trade.get("stop_loss", 0.0)),
        tp1=float(trade.get("tp1", 0.0)),
        tp2=float(trade.get("tp2", 0.0)),
        tp3=float(trade.get("tp3", 0.0)),
        volume=float(trade.get("volume", 0.0)),
        score=int(trade.get("score", 0)),
        server=trade.get("server"),
    )

    if sent:
        trade["open_notification_sent"] = True
        save_trade(trade)
        log_event(
            f"Уведомление об открытии отправлено: position_id={trade.get('position_id')}, "
            f"ticket={trade.get('position_ticket')}"
        )

    return trade


def _position_opened_at(position) -> str:
    timestamp = int(getattr(position, "time", 0) or 0)
    if timestamp <= 0:
        return utc_now_iso()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _build_trade_from_pending(pending: dict, position) -> dict:
    actual_sl = float(getattr(position, "sl", 0.0) or 0.0)
    requested_sl = float(pending.get("stop_loss", 0.0) or 0.0)

    return {
        "symbol": str(pending["symbol"]),
        "request_id": pending.get("request_id"),
        "order_comment": pending.get("order_comment"),
        "direction": get_position_direction(position),
        "signal_entry_price": float(pending.get("signal_entry_price", 0.0)),
        "entry_price": float(getattr(position, "price_open", 0.0) or 0.0),
        "initial_stop_loss": actual_sl or requested_sl,
        "stop_loss": actual_sl or requested_sl,
        "tp1": float(pending["tp1"]),
        "tp2": float(pending["tp2"]),
        "tp3": float(pending["tp3"]),
        "volume": float(getattr(position, "volume", pending.get("volume", 0.0)) or 0.0),
        "score": int(pending.get("score", 0)),
        "reasons": list(pending.get("reasons", [])),
        "ticket": int(pending.get("order_ticket", 0) or 0),
        "order_ticket": int(pending.get("order_ticket", 0) or 0),
        "opening_deal_ticket": int(pending.get("deal_ticket", 0) or 0),
        "position_ticket": int(getattr(position, "ticket", 0) or 0),
        "position_id": get_position_identifier(position),
        "magic": int(getattr(position, "magic", BOT_MAGIC) or BOT_MAGIC),
        "opened_at": _position_opened_at(position),
        "open_time": pending.get("signal_candle_time"),
        "last_managed_m15_candle": pending.get("signal_candle_time"),
        "balance_at_open": float(pending.get("balance_at_open", 0.0) or 0.0),
        "account_login": pending.get("account_login"),
        "server": pending.get("server"),
        "strategy_config_hash": pending.get("strategy_config_hash"),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "breakeven_active": False,
        "candles_in_trade": 0,
        "requested_close_reason": None,
        "close_requested_at": None,
        "close_request_status": None,
        "close_request_order_ticket": None,
        "close_request_deal_ticket": None,
        "missing_position_since": None,
        "history_lookup_attempts": 0,
        "history_warning_sent": False,
        "open_notification_sent": False,
        "pending_sl_notification": None,
        "processed_close_deal_ticket": None,
        "current_price": None,
        "floating_profit": 0.0,
        "floating_percent": 0.0,
        "cache_updated_at": None,
    }


def _find_opening_deal_for_pending(pending: dict) -> dict | None:
    sent_at = parse_datetime(pending.get("sent_at")) or (utc_now() - timedelta(minutes=5))
    deals = get_history_deals(
        date_from=sent_at - timedelta(minutes=2),
        date_to=utc_now() + timedelta(minutes=1),
        symbol=str(pending.get("symbol", "")),
    )

    order_ticket = int(pending.get("order_ticket", 0) or 0)
    deal_ticket = int(pending.get("deal_ticket", 0) or 0)

    candidates = []
    for deal in deals:
        if not is_opening_deal(deal):
            continue
        if deal_ticket and int(deal.get("ticket", 0)) == deal_ticket:
            return deal
        if order_ticket and int(deal.get("order", 0)) == order_ticket:
            candidates.append(deal)

    if len(candidates) == 1:
        return candidates[0]

    # Crash window: order_send may have succeeded but the process stopped before
    # order/deal tickets were persisted. Recover only a unique, tightly matching
    # opening deal from the same strategy and time window.
    if not order_ticket and not deal_ticket:
        expected_direction = str(pending.get("direction", "") or "").upper()
        expected_volume = float(pending.get("volume", 0.0) or 0.0)
        expected_magic = int(pending.get("magic", BOT_MAGIC) or BOT_MAGIC)
        expected_comment = str(pending.get("order_comment", "") or "").strip()
        created_at = parse_datetime(pending.get("created_at")) or sent_at
        fallback = []
        comment_matches = []
        for deal in deals:
            if not is_opening_deal(deal):
                continue
            if int(deal.get("magic", 0) or 0) != expected_magic:
                continue
            if deal.get("time") < created_at - timedelta(minutes=2):
                continue
            if expected_direction and get_deal_direction(deal) != expected_direction:
                continue
            if expected_volume and abs(float(deal.get("volume", 0.0)) - expected_volume) > 0.000001:
                continue
            fallback.append(deal)
            if (
                expected_comment
                and str(deal.get("comment", "") or "").strip() == expected_comment
            ):
                comment_matches.append(deal)

        if len(comment_matches) == 1:
            return comment_matches[0]

        position_ids = {int(item.get("position_id", 0) or 0) for item in fallback}
        position_ids.discard(0)
        if len(fallback) == 1 or len(position_ids) == 1:
            fallback.sort(key=lambda item: (item.get("time_msc", 0), item.get("ticket", 0)))
            return fallback[0]

    return None


def _build_trade_from_history(pending: dict, opening_deal: dict) -> dict:
    return {
        "symbol": str(pending["symbol"]),
        "request_id": pending.get("request_id"),
        "order_comment": pending.get("order_comment"),
        "direction": str(pending.get("direction", "")),
        "signal_entry_price": float(pending.get("signal_entry_price", 0.0)),
        "entry_price": float(opening_deal.get("price", pending.get("signal_entry_price", 0.0))),
        "initial_stop_loss": float(pending.get("stop_loss", 0.0)),
        "stop_loss": float(pending.get("stop_loss", 0.0)),
        "tp1": float(pending["tp1"]),
        "tp2": float(pending["tp2"]),
        "tp3": float(pending["tp3"]),
        "volume": float(opening_deal.get("volume", pending.get("volume", 0.0))),
        "score": int(pending.get("score", 0)),
        "reasons": list(pending.get("reasons", [])),
        "ticket": int(pending.get("order_ticket", 0) or 0),
        "order_ticket": int(pending.get("order_ticket", 0) or 0),
        "opening_deal_ticket": int(opening_deal.get("ticket", 0) or 0),
        "position_ticket": None,
        "position_id": int(opening_deal.get("position_id", 0) or 0),
        "magic": int(opening_deal.get("magic", BOT_MAGIC) or BOT_MAGIC),
        "opened_at": opening_deal["time"].isoformat(),
        "open_time": pending.get("signal_candle_time"),
        "last_managed_m15_candle": pending.get("signal_candle_time"),
        "balance_at_open": float(pending.get("balance_at_open", 0.0) or 0.0),
        "account_login": pending.get("account_login"),
        "server": pending.get("server"),
        "strategy_config_hash": pending.get("strategy_config_hash"),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "breakeven_active": False,
        "candles_in_trade": 0,
        "requested_close_reason": None,
        "close_requested_at": None,
        "close_request_status": None,
        "close_request_order_ticket": None,
        "close_request_deal_ticket": None,
        "missing_position_since": utc_now_iso(),
        "history_lookup_attempts": 0,
        "history_warning_sent": False,
        "open_notification_sent": False,
        "pending_sl_notification": None,
        "processed_close_deal_ticket": None,
        "current_price": None,
        "floating_profit": 0.0,
        "floating_percent": 0.0,
        "cache_updated_at": None,
    }


def reconcile_pending_open(account) -> str:
    pending = get_pending_open()
    if pending is None:
        return "NONE"

    try:
        pending_login = int(pending.get("account_login") or 0)
    except (TypeError, ValueError):
        pending_login = 0
    pending_server = str(pending.get("server", "") or "").strip()
    current_login = int(getattr(account, "login", 0) or 0)
    current_server = str(getattr(account, "server", "") or "").strip()

    if (
        pending_login > 0
        and pending_server
        and (pending_login != current_login or pending_server != current_server)
    ):
        message = (
            "Ожидающее открытие относится к другому счёту MT5: "
            f"состояние={pending_login}@{pending_server}, "
            f"текущий={current_login}@{current_server}. "
            "Новые сделки запрещены до ручной проверки."
        )
        block_state_safety(
            "PENDING_ACCOUNT_MISMATCH",
            message,
            details={
                "pending_login": pending_login,
                "pending_server": pending_server,
                "current_login": current_login,
                "current_server": current_server,
            },
        )
        log_event(message)
        return "BLOCKED"

    position = find_position_for_pending_open(pending)
    trade = None

    if position is not None:
        trade = _build_trade_from_pending(pending, position)
    else:
        opening_deal = _find_opening_deal_for_pending(pending)
        if opening_deal is not None:
            trade = _build_trade_from_history(pending, opening_deal)

    if trade is not None:
        activate_trade(trade)
        current_safety = get_state_safety()
        if current_safety.get("reason_code") in {
            "ORDER_SEND_RESULT_UNKNOWN",
            "PARTIAL_OPEN_EXECUTION",
        }:
            clear_state_safety(
                "Позиция MT5 найдена, ожидающее открытие безопасно восстановлено."
            )
        log_event(
            f"Ожидающее открытие восстановлено: position_id={trade.get('position_id')}, "
            f"position_ticket={trade.get('position_ticket')}, "
            f"entry={trade.get('entry_price')}"
        )
        log_trade_event(
            event="OPEN_POSITION_CONFIRMED",
            symbol=trade.get("symbol", ""),
            direction=trade.get("direction", ""),
            entry_price=trade.get("entry_price", ""),
            stop_loss=trade.get("stop_loss", ""),
            tp1=trade.get("tp1", ""),
            tp2=trade.get("tp2", ""),
            tp3=trade.get("tp3", ""),
            volume=trade.get("volume", ""),
            score=trade.get("score", ""),
            reasons=" | ".join(trade.get("reasons", [])),
            ticket=trade.get("position_ticket") or trade.get("position_id"),
            balance=getattr(account, "balance", ""),
            equity=getattr(account, "equity", ""),
            comment=(
                f"order={trade.get('order_ticket')}; "
                f"opening_deal={trade.get('opening_deal_ticket')}; "
                f"position_id={trade.get('position_id')}"
            ),
        )
        ensure_open_notification(trade)
        return "ACTIVATED"

    started_at = parse_datetime(pending.get("created_at")) or utc_now()
    elapsed = (utc_now() - started_at).total_seconds()
    monitoring = load_monitoring_config()
    warning_after = int(monitoring.get("pending_open_warning_seconds", 60))
    prepared_timeout = int(
        monitoring.get("pending_open_prepared_timeout_seconds", 90)
    )
    pending_status = str(pending.get("status", "prepared") or "prepared").lower()
    order_ticket = int(pending.get("order_ticket", 0) or 0)
    deal_ticket = int(pending.get("deal_ticket", 0) or 0)

    # The process may stop after saving "prepared" but before order_send().
    # We search MT5 position/history first. If there is still no execution evidence
    # after the grace period, clearing this placeholder is safer than blocking forever.
    if (
        pending_status == "prepared"
        and order_ticket == 0
        and deal_ticket == 0
        and elapsed >= prepared_timeout
    ):
        message = (
            f"Незавершённое ожидающее открытие для {pending.get('symbol')} сброшено: "
            f"за {int(elapsed)} сек. в MT5 не найдено позиции или сделки открытия. "
            "Свеча сигнала помечена обработанной, повторный ордер по ней не будет отправлен."
        )
        notify_state_warning("Незавершённое открытие безопасно сброшено", message)
        clear_pending_open_after_recovery(
            reason=message,
            signal_candle_time=pending.get("signal_candle_time"),
        )
        log_event(message)
        return "CLEARED_NO_EVIDENCE"

    if elapsed >= warning_after and not pending.get("warning_sent", False):
        message = (
            f"Ордер был отправлен, но позиция {pending.get('symbol')} не найдена. "
            f"ордер={pending.get('order_ticket')}, торговая_операция={pending.get('deal_ticket')}. "
            "Новые сделки заблокированы до восстановления состояния."
        )
        sent = notify_state_warning("Не удалось подтвердить открытие", message)
        if sent or _notification_is_optional():
            pending["warning_sent"] = True
            pending["warning_sent_at"] = utc_now_iso()
            save_pending_open(pending)
        log_event(f"Ожидающее открытие не восстановлено: {message}")

    return "PENDING"


def handle_position_without_state(position) -> None:
    ticket = int(getattr(position, "ticket", 0) or 0)
    current_warning = get_warning("position_without_state")
    current_safety = get_state_safety()
    if (
        isinstance(current_warning, dict)
        and int(current_warning.get("ticket", 0)) == ticket
        and current_safety.get("blocked", False)
        and current_safety.get("reason_code") == "POSITION_WITHOUT_STATE"
    ):
        return

    message = (
        f"В MT5 обнаружена позиция, которой нет в состоянии робота: "
        f"инструмент={position.symbol}, номер={ticket}, "
        f"направление={translate_direction(get_position_direction(position))}, "
        f"объём={position.volume}. Робот не будет открывать новую сделку "
        "и не будет автоматически управлять неизвестной позицией."
    )
    block_state_safety(
        "POSITION_WITHOUT_STATE",
        message,
        details={
            "ticket": ticket,
            "symbol": str(getattr(position, "symbol", "")),
            "direction": get_position_direction(position),
            "volume": float(getattr(position, "volume", 0.0) or 0.0),
            "magic": int(getattr(position, "magic", 0) or 0),
        },
    )
    sent = notify_state_warning("Позиция MT5 без состояния робота", message)

    if sent or _notification_is_optional():
        mark_state_safety_notification_sent()
        set_warning(
            "position_without_state",
            {"ticket": ticket, "sent_at": utc_now_iso(), "message": message},
        )
    log_event(message)


def _find_position_deals(trade: dict) -> list[dict]:
    position_id = int(trade.get("position_id") or 0)
    symbol = str(trade.get("symbol", ""))

    if position_id:
        deals = get_deals_for_position(position_id=position_id, symbol=symbol)
        if deals:
            return deals

    opened_at = parse_datetime(trade.get("opened_at") or trade.get("open_time"))
    date_from = (opened_at or (utc_now() - timedelta(days=7))) - timedelta(minutes=10)
    deals = get_history_deals(
        date_from=date_from,
        date_to=utc_now() + timedelta(minutes=1),
        symbol=symbol,
    )

    opening_deal_ticket = int(trade.get("opening_deal_ticket") or 0)
    order_ticket = int(trade.get("order_ticket") or trade.get("ticket") or 0)
    close_deal_ticket = int(trade.get("close_request_deal_ticket") or 0)

    matching_position_ids: set[int] = set()
    for deal in deals:
        if opening_deal_ticket and int(deal.get("ticket", 0)) == opening_deal_ticket:
            matching_position_ids.add(int(deal.get("position_id", 0)))
        if close_deal_ticket and int(deal.get("ticket", 0)) == close_deal_ticket:
            matching_position_ids.add(int(deal.get("position_id", 0)))
        if order_ticket and int(deal.get("order", 0)) == order_ticket:
            matching_position_ids.add(int(deal.get("position_id", 0)))

    matching_position_ids.discard(0)

    if len(matching_position_ids) == 1:
        resolved_position_id = next(iter(matching_position_ids))
        trade["position_id"] = resolved_position_id
        save_trade(trade)
        return [deal for deal in deals if int(deal.get("position_id", 0)) == resolved_position_id]

    candidates = [
        deal
        for deal in deals
        if int(deal.get("magic", 0) or 0) == int(trade.get("magic", BOT_MAGIC))
        and (opened_at is None or deal["time"] >= opened_at - timedelta(minutes=2))
    ]
    candidate_position_ids = {int(deal.get("position_id", 0)) for deal in candidates}
    candidate_position_ids.discard(0)

    if len(candidate_position_ids) == 1:
        resolved_position_id = next(iter(candidate_position_ids))
        trade["position_id"] = resolved_position_id
        save_trade(trade)
        return [deal for deal in candidates if int(deal.get("position_id", 0)) == resolved_position_id]

    return []


def _resolve_close_reason(trade: dict, closing_deal: dict) -> str:
    reason_name = str(closing_deal.get("reason_name", "UNKNOWN"))
    requested = str(trade.get("requested_close_reason") or "")

    if reason_name == "SL":
        if trade.get("tp3_hit"):
            return "СТОП-ЛОСС — уровень цели 2"
        if trade.get("tp2_hit"):
            return "СТОП-ЛОСС — уровень цели 1"
        if trade.get("breakeven_active") or trade.get("tp1_hit"):
            return "СТОП-ЛОСС — безубыток+"
        return "СТОП-ЛОСС"

    if reason_name == "TP":
        return "ТЕЙК-ПРОФИТ"
    if reason_name == "CLIENT":
        return "РУЧНОЕ ЗАКРЫТИЕ — ТЕРМИНАЛ"
    if reason_name == "MOBILE":
        return "РУЧНОЕ ЗАКРЫТИЕ — МОБИЛЬНЫЙ ТЕРМИНАЛ"
    if reason_name == "WEB":
        return "РУЧНОЕ ЗАКРЫТИЕ — ВЕБ-ТЕРМИНАЛ"
    if reason_name == "STOP_OUT":
        return "БРОКЕР — СТОП-АУТ"
    if reason_name == "EXPERT" and requested:
        mapping = {
            "EXIT_SIGNAL": "ОБРАТНЫЙ СИГНАЛ",
            "DAILY_HARD_STOP": "ЖЁСТКАЯ ДНЕВНАЯ БЛОКИРОВКА",
            "ACCOUNT_GUARD": "ЗАЩИТА СЧЁТА",
            "MANUAL_COMMAND": "РУЧНАЯ КОМАНДА",
        }
        return mapping.get(requested, requested.replace("_", " "))
    if reason_name == "EXPERT":
        return "ЗАКРЫТИЕ РОБОТОМ"

    return reason_name.replace("_", " ") or "НЕИЗВЕСТНО"


def _today_realized_result(symbol: str) -> float:
    day = get_trading_day_context()
    deals = get_trade_deals_from_history(
        day["start_utc"],
        day["now_utc"] + timedelta(minutes=1),
        symbol=symbol,
    )
    return sum(
        float(deal.get("profit", 0.0))
        + float(deal.get("commission", 0.0))
        + float(deal.get("swap", 0.0))
        + float(deal.get("fee", 0.0))
        for deal in deals
    )


def reconcile_active_trade(symbol: str, account) -> str:
    trade = get_active_trade()
    if trade is None:
        return "NONE"

    ensure_open_notification(trade)
    trade = get_active_trade() or trade
    position = get_open_position(symbol=symbol, trade=trade)

    if position is not None:
        integrity = validate_position_integrity(position, trade)
        if not integrity["valid"]:
            message = (
                f"Позиция {symbol} найдена, но её параметры не совпадают "
                "с состоянием робота: "
                + "; ".join(integrity["issues"])
                + ". Робот не будет изменять или закрывать эту позицию до ручной проверки."
            )
            _activate_reconciliation_anomaly(
                "POSITION_INTEGRITY_MISMATCH",
                "Несоответствие позиции и состояния робота",
                message,
                integrity,
            )
            return "POSITION_ANOMALY"

        if trade.get("missing_position_since") is not None:
            trade["missing_position_since"] = None
            trade["history_lookup_attempts"] = 0
            trade["history_warning_sent"] = False
            save_trade(trade)
            log_event(f"Позиция снова найдена: номер={position.ticket}")
        return "OPEN"

    now = utc_now()
    if trade.get("missing_position_since") is None:
        trade["missing_position_since"] = now.isoformat()
        trade["history_lookup_attempts"] = 0
        log_event(
            f"Позиция исчезла. Начата проверка истории MT5: "
            f"position_id={trade.get('position_id')}, ticket={trade.get('position_ticket')}"
        )

    trade["history_lookup_attempts"] = int(trade.get("history_lookup_attempts", 0)) + 1
    save_trade(trade)

    deals = _find_position_deals(trade)
    closing_deals = [deal for deal in deals if is_closing_deal(deal)]

    if not closing_deals:
        missing_since = parse_datetime(trade.get("missing_position_since")) or now
        elapsed = (now - missing_since).total_seconds()
        wait_seconds = int(load_monitoring_config().get("position_history_wait_seconds", 60))

        if elapsed >= wait_seconds and not trade.get("history_warning_sent", False):
            message = (
                f"Состояние робота содержит сделку {symbol}, но позиция отсутствует, "
                f"а закрывающая сделка не найдена в истории MT5 за {int(elapsed)} сек. "
                f"position_id={trade.get('position_id')}, "
                f"position_ticket={trade.get('position_ticket')}"
            )
            sent = notify_state_warning("Не удалось восстановить закрытие", message)
            if sent or _notification_is_optional():
                trade["history_warning_sent"] = True
                trade["history_warning_sent_at"] = utc_now_iso()
                save_trade(trade)
            log_event(message)

        return "WAITING_HISTORY"

    closing_deals.sort(key=lambda item: (item["time_msc"], item["ticket"]))

    if len(closing_deals) != 1:
        tickets = [int(item.get("ticket", 0) or 0) for item in closing_deals]
        message = (
            f"Для позиции {symbol} найдено {len(closing_deals)} закрывающих сделок "
            "вместо одной. Стратегия робота не использует частичные закрытия, поэтому "
            "автоматическая фиксация результата остановлена до ручной проверки."
        )
        _activate_reconciliation_anomaly(
            "MULTIPLE_CLOSING_DEALS",
            "Неоднозначная история закрытия",
            message,
            {
                "position_id": trade.get("position_id"),
                "closing_deal_tickets": tickets,
            },
        )
        return "CLOSE_HISTORY_ANOMALY"

    final_deal = closing_deals[0]
    final_ticket = int(final_deal["ticket"])
    expected_volume = float(trade.get("volume", 0.0) or 0.0)
    closed_volume = float(final_deal.get("volume", 0.0) or 0.0)
    if expected_volume > 0 and abs(closed_volume - expected_volume) > 0.000001:
        message = (
            f"Закрывающая сделка {final_ticket} имеет объём {closed_volume}, "
            f"а состояние робота ожидает полное закрытие объёма {expected_volume}. "
            "Новые сделки заблокированы до ручной проверки."
        )
        _activate_reconciliation_anomaly(
            "CLOSE_DEAL_VOLUME_MISMATCH",
            "Несоответствие объёма закрытия",
            message,
            {
                "position_id": trade.get("position_id"),
                "closing_deal_ticket": final_ticket,
                "closed_volume": closed_volume,
                "expected_volume": expected_volume,
            },
        )
        return "CLOSE_HISTORY_ANOMALY"

    if is_close_deal_processed(final_ticket):
        close_summary = {
            "closing_deal_ticket": final_ticket,
            "processed_at": utc_now_iso(),
            "duplicate_reconciliation": True,
        }
        finalize_trade_close(final_ticket, close_summary)
        log_event(f"Повторная обработка закрытия пропущена; состояние очищено: {final_ticket}")
        return "CLOSED_ALREADY_PROCESSED"

    gross_profit = sum(float(deal.get("profit", 0.0)) for deal in deals)
    commission = sum(float(deal.get("commission", 0.0)) for deal in deals)
    swap = sum(float(deal.get("swap", 0.0)) for deal in deals)
    fee = sum(float(deal.get("fee", 0.0)) for deal in deals)
    net_profit = gross_profit + commission + swap + fee
    close_price = float(final_deal.get("price", 0.0) or 0.0)
    close_time = final_deal["time"].astimezone(STATS_TIMEZONE)
    reason = _resolve_close_reason(trade, final_deal)
    balance_at_open = float(trade.get("balance_at_open", 0.0) or 0.0)
    percent_result = (net_profit / balance_at_open * 100) if balance_at_open > 0 else 0.0
    daily_money = _today_realized_result(symbol)

    from app.state_manager import load_state
    state = load_state()
    daily_guard = state.get("daily_guard") or {}
    daily_start_balance = float(daily_guard.get("start_balance", 0.0) or 0.0)
    daily_percent = (
        daily_money / daily_start_balance * 100 if daily_start_balance > 0 else 0.0
    )

    close_summary = {
        "symbol": symbol,
        "direction": trade.get("direction"),
        "reason": reason,
        "close_price": round(close_price, 8),
        "gross_profit": round(gross_profit, 2),
        "commission": round(commission, 2),
        "swap": round(swap, 2),
        "fee": round(fee, 2),
        "net_profit": round(net_profit, 2),
        "percent_result": round(percent_result, 4),
        "balance": float(getattr(account, "balance", 0.0) or 0.0),
        "daily_money": round(daily_money, 2),
        "daily_percent": round(daily_percent, 4),
        "close_time": close_time.isoformat(),
        "closing_deal_ticket": final_ticket,
        "position_id": int(final_deal.get("position_id", 0)),
        "processed_at": utc_now_iso(),
    }

    notification_sent = notify_trade_closed(
        symbol=symbol,
        direction=str(trade.get("direction", "")),
        reason=reason,
        close_price=close_price,
        gross_profit=gross_profit,
        commission=commission,
        swap=swap,
        fee=fee,
        net_profit=net_profit,
        percent_result=percent_result,
        balance=float(getattr(account, "balance", 0.0) or 0.0),
        daily_money=daily_money,
        daily_percent=daily_percent,
        close_time=close_time.strftime("%d.%m.%Y %H:%M:%S"),
        server=trade.get("server") or getattr(account, "server", None),
    )

    if not notification_sent and not _notification_is_optional():
        log_event(
            f"Не удалось отправить уведомление о закрытии; состояние сохранено для повторной попытки: торговая_операция={final_ticket}"
        )
        return "NOTIFICATION_PENDING"

    log_trade_event(
        event="CLOSE_POSITION_CONFIRMED",
        symbol=symbol,
        direction=trade.get("direction", ""),
        entry_price=trade.get("entry_price", ""),
        stop_loss=trade.get("stop_loss", ""),
        tp1=trade.get("tp1", ""),
        tp2=trade.get("tp2", ""),
        tp3=trade.get("tp3", ""),
        volume=trade.get("volume", ""),
        ticket=final_ticket,
        balance=getattr(account, "balance", ""),
        equity=getattr(account, "equity", ""),
        profit=round(net_profit, 2),
        comment=(
            f"reason={reason}; position_id={final_deal.get('position_id')}; "
            f"close_price={close_price}; gross={gross_profit}; commission={commission}; "
            f"swap={swap}; fee={fee}"
        ),
    )
    finalize_trade_close(final_ticket, close_summary)
    log_event(
        f"Закрытие сделки подтверждено: торговая_операция={final_ticket}, причина={reason}, "
        f"итог={net_profit:.2f}"
    )
    return "CLOSED"
