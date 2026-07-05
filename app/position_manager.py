import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from app.executor import BOT_MAGIC
from app.logger import log_event, log_trade_event
from app.mt5_client import (
    get_last_error,
    get_positions,
    get_symbol_info,
    get_tick,
    send_order,
)
from app.state_manager import (
    block_state_safety,
    get_active_trade,
    mark_state_safety_notification_sent,
    save_trade,
)
from app.strategy import should_exit_trade
from app.text_ru import translate_retcode
from app.telegram_notifier import (
    notify_sl_moved,
    notify_state_warning,
    telegram_notifications_enabled,
)


DONE_RETCODE = int(getattr(mt5, "TRADE_RETCODE_DONE", 10009))
PARTIAL_RETCODE = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))
SUCCESS_RETCODES = {DONE_RETCODE}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_position_direction(position) -> str:
    if int(position.type) == int(mt5.POSITION_TYPE_BUY):
        return "BUY"
    if int(position.type) == int(mt5.POSITION_TYPE_SELL):
        return "SELL"
    return ""


def get_position_identifier(position) -> int:
    identifier = int(getattr(position, "identifier", 0) or 0)
    return identifier or int(getattr(position, "ticket", 0) or 0)


def _volume_matches(actual: float, expected: float) -> bool:
    return abs(float(actual) - float(expected)) <= 0.000001


def validate_position_integrity(position, trade: dict) -> dict:
    issues: list[str] = []
    expected_symbol = str(trade.get("symbol", "") or "")
    expected_direction = str(trade.get("direction", "") or "").upper()
    expected_volume = float(trade.get("volume", 0.0) or 0.0)
    expected_magic = int(trade.get("magic", BOT_MAGIC) or BOT_MAGIC)

    actual_symbol = str(getattr(position, "symbol", "") or "")
    actual_direction = get_position_direction(position)
    actual_volume = float(getattr(position, "volume", 0.0) or 0.0)
    actual_magic = int(getattr(position, "magic", 0) or 0)

    if expected_symbol and actual_symbol != expected_symbol:
        issues.append(f"symbol {actual_symbol} != {expected_symbol}")
    if expected_direction and actual_direction != expected_direction:
        issues.append(f"direction {actual_direction} != {expected_direction}")
    if expected_magic and actual_magic != expected_magic:
        issues.append(f"magic {actual_magic} != {expected_magic}")
    if expected_volume and not _volume_matches(actual_volume, expected_volume):
        issues.append(f"volume {actual_volume} != {expected_volume}")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "actual_symbol": actual_symbol,
        "actual_direction": actual_direction,
        "actual_volume": actual_volume,
        "actual_magic": actual_magic,
        "ticket": int(getattr(position, "ticket", 0) or 0),
        "position_id": get_position_identifier(position),
    }


def _activate_execution_anomaly(reason_code: str, title: str, message: str, details: dict) -> None:
    changed = block_state_safety(reason_code, message, details=details)
    if not changed:
        return
    sent = notify_state_warning(title, message)
    if sent or not telegram_notifications_enabled():
        mark_state_safety_notification_sent()
    log_event(f"Аномалия защиты исполнения: {reason_code}: {message}")


def get_open_position(symbol: str, trade: dict | None = None):
    positions = get_positions(symbol=symbol)

    if positions is None or len(positions) == 0:
        return None

    if trade:
        position_ticket = int(trade.get("position_ticket") or 0)
        position_id = int(trade.get("position_id") or 0)
        order_ticket = int(trade.get("order_ticket") or trade.get("ticket") or 0)

        for position in positions:
            ticket = int(getattr(position, "ticket", 0) or 0)
            identifier = get_position_identifier(position)
            if position_ticket and ticket == position_ticket:
                return position
            if position_id and identifier == position_id:
                return position
            if order_ticket and ticket == order_ticket:
                return position

        expected_direction = str(trade.get("direction", "")).upper()
        expected_volume = float(trade.get("volume", 0.0) or 0.0)
        expected_magic = int(trade.get("magic", BOT_MAGIC) or BOT_MAGIC)

        candidates = []
        for position in positions:
            if expected_direction and get_position_direction(position) != expected_direction:
                continue
            position_magic = int(getattr(position, "magic", 0) or 0)
            if expected_magic and position_magic != expected_magic:
                continue
            if expected_volume and not _volume_matches(position.volume, expected_volume):
                continue
            candidates.append(position)

        if len(candidates) == 1:
            return candidates[0]

        return None

    if len(positions) == 1:
        return positions[0]

    bot_positions = [
        position
        for position in positions
        if int(getattr(position, "magic", 0) or 0) == BOT_MAGIC
    ]
    if len(bot_positions) == 1:
        return bot_positions[0]

    return None




def get_any_open_position(symbol: str):
    positions = get_positions(symbol=symbol)
    if positions is None or len(positions) == 0:
        return None
    return positions[0]

def find_position_for_pending_open(pending_open: dict):
    symbol = str(pending_open.get("symbol", ""))
    if not symbol:
        return None

    positions = get_positions(symbol=symbol)
    if positions is None or len(positions) == 0:
        return None

    expected_direction = str(pending_open.get("direction", "")).upper()
    expected_volume = float(pending_open.get("volume", 0.0) or 0.0)
    expected_magic = int(pending_open.get("magic", BOT_MAGIC) or BOT_MAGIC)
    order_ticket = int(pending_open.get("order_ticket", 0) or 0)

    candidates = []
    for position in positions:
        ticket = int(getattr(position, "ticket", 0) or 0)
        if order_ticket and ticket == order_ticket:
            return position
        if expected_direction and get_position_direction(position) != expected_direction:
            continue
        position_magic = int(getattr(position, "magic", 0) or 0)
        if expected_magic and position_magic != expected_magic:
            continue
        if expected_volume and not _volume_matches(position.volume, expected_volume):
            continue
        candidates.append(position)

    if len(candidates) == 1:
        return candidates[0]

    return None


def wait_for_open_position(pending_open: dict, timeout_seconds: int = 10):
    deadline = time.monotonic() + max(0, int(timeout_seconds))

    while True:
        position = find_position_for_pending_open(pending_open)
        if position is not None:
            return position
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def has_open_position(symbol: str, trade: dict | None = None) -> bool:
    return get_open_position(symbol=symbol, trade=trade) is not None


def get_current_price(symbol: str, direction: str) -> float | None:
    tick = get_tick(symbol)
    if tick is None:
        log_event(f"Не удалось получить котировку для сопровождения позиции: {symbol}")
        return None
    if direction == "BUY":
        return float(tick.bid)
    if direction == "SELL":
        return float(tick.ask)
    return None


def _confirm_stop_loss(position_ticket: int, new_sl: float, symbol: str) -> bool:
    info = get_symbol_info(symbol)
    tolerance = float(getattr(info, "point", 0.01) or 0.01) * 1.5

    for _ in range(6):
        positions = get_positions(ticket=position_ticket)
        if positions:
            actual_sl = float(getattr(positions[0], "sl", 0.0) or 0.0)
            if abs(actual_sl - float(new_sl)) <= tolerance:
                return True
        time.sleep(0.5)

    return False


def modify_stop_loss(position, new_sl: float) -> bool:
    symbol = str(position.symbol)
    position_ticket = int(position.ticket)
    info = get_symbol_info(symbol)
    digits = int(getattr(info, "digits", 2) or 2)
    normalized_sl = round(float(new_sl), digits)

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": position_ticket,
        "sl": normalized_sl,
        "tp": float(getattr(position, "tp", 0.0) or 0.0),
    }

    result = send_order(request)

    if result is None:
        log_event(f"Не удалось изменить стоп-лосс: {get_last_error()}")
        return False

    retcode = int(getattr(result, "retcode", 0) or 0)
    if retcode == PARTIAL_RETCODE:
        _activate_execution_anomaly(
            "PARTIAL_SL_EXECUTION",
            "Аномалия изменения стоп-лосса",
            (
                "MT5 вернул неполное исполнение операции изменения стоп-лосса. "
                "Новые сделки заблокированы до ручной проверки."
            ),
            {"position_ticket": position_ticket, "retcode": retcode},
        )
        return False

    if retcode not in SUCCESS_RETCODES:
        log_event(
            f"Изменение стоп-лосса отклонено: ticket={position_ticket}; "
            f"{translate_retcode(retcode, getattr(result, 'comment', ''))}"
        )
        return False

    if not _confirm_stop_loss(position_ticket, normalized_sl, symbol):
        log_event(
            f"Изменение стоп-лосса принято, но не подтверждено в позиции: "
            f"ticket={position_ticket}, ожидаемый_стоп={normalized_sl}"
        )
        return False

    log_event(f"Стоп-лосс изменён и подтверждён: ticket={position_ticket}, new_sl={normalized_sl}")
    return True


def _flush_pending_sl_notification(trade: dict) -> dict:
    pending = trade.get("pending_sl_notification")
    if not isinstance(pending, dict):
        return trade

    if not telegram_notifications_enabled():
        trade["pending_sl_notification"] = None
        save_trade(trade)
        log_event("Уведомление о переносе стоп-лосса пропущено: Telegram-уведомления отключены")
        return trade

    sent = notify_sl_moved(
        symbol=str(trade.get("symbol", "")),
        direction=str(trade.get("direction", "")),
        new_sl=float(pending.get("new_sl", trade.get("stop_loss", 0.0))),
        reason=str(pending.get("reason", "Стоп-лосс изменён")),
        server=trade.get("server"),
    )

    if sent:
        log_event(
            f"Уведомление о переносе стоп-лосса отправлено: этап={pending.get('stage')}, "
            f"new_sl={pending.get('new_sl')}"
        )
        trade["pending_sl_notification"] = None
        save_trade(trade)

    return trade


def _apply_sl_stage(
    trade: dict,
    position,
    new_sl: float,
    stage: str,
    reason: str,
    flag_name: str,
    activate_breakeven: bool = False,
) -> tuple[dict, object]:
    if not modify_stop_loss(position, new_sl):
        return trade, position

    info = get_symbol_info(str(position.symbol))
    digits = int(getattr(info, "digits", 2) or 2)
    normalized_sl = round(float(new_sl), digits)

    trade["stop_loss"] = normalized_sl
    trade[flag_name] = True
    if activate_breakeven:
        trade["breakeven_active"] = True

    trade["pending_sl_notification"] = {
        "stage": stage,
        "new_sl": normalized_sl,
        "reason": reason,
        "created_at": utc_now_iso(),
    }
    save_trade(trade)

    log_trade_event(
        event=stage,
        symbol=trade.get("symbol", ""),
        direction=trade.get("direction", ""),
        entry_price=trade.get("entry_price", ""),
        stop_loss=normalized_sl,
        tp1=trade.get("tp1", ""),
        tp2=trade.get("tp2", ""),
        tp3=trade.get("tp3", ""),
        volume=trade.get("volume", ""),
        ticket=trade.get("position_ticket", trade.get("ticket", "")),
        comment=reason,
    )

    trade = _flush_pending_sl_notification(trade)
    refreshed = get_open_position(str(position.symbol), trade=trade)
    return trade, refreshed or position


def close_position(symbol: str, reason: str = "EXIT_SIGNAL") -> bool:
    trade = get_active_trade()
    if trade is None:
        log_event(f"Не удалось закрыть {symbol}: состояние активной сделки отсутствует")
        return False

    position = get_open_position(symbol=symbol, trade=trade)
    if position is None:
        log_event(f"Не найдена соответствующая открытая позиция для закрытия: {symbol}")
        return False

    now = datetime.now(timezone.utc)
    previous_request_at = trade.get("close_requested_at")
    previous_status = trade.get("close_request_status")

    if previous_request_at and previous_status == "sent":
        try:
            previous_dt = datetime.fromisoformat(str(previous_request_at).replace("Z", "+00:00"))
            if previous_dt.tzinfo is None:
                previous_dt = previous_dt.replace(tzinfo=timezone.utc)
            if (now - previous_dt).total_seconds() < 15:
                return True
        except ValueError:
            pass

    direction = get_position_direction(position)
    tick = get_tick(symbol)
    if tick is None:
        log_event(f"Не удалось получить котировку для закрытия: {symbol}")
        return False

    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_SELL
        price = float(tick.bid)
    elif direction == "SELL":
        order_type = mt5.ORDER_TYPE_BUY
        price = float(tick.ask)
    else:
        log_event(f"Неподдерживаемый тип позиции при закрытии: ticket={position.ticket}")
        return False

    trade["requested_close_reason"] = reason
    trade["close_requested_at"] = now.isoformat()
    trade["close_request_status"] = "sending"
    save_trade(trade)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": int(position.ticket),
        "symbol": symbol,
        "volume": float(position.volume),
        "type": order_type,
        "price": price,
        "deviation": 30,
        "magic": BOT_MAGIC,
        "comment": reason[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = send_order(request)

    if result is None:
        trade["close_request_status"] = "failed"
        trade["close_request_error"] = str(get_last_error())
        save_trade(trade)
        log_event(f"Не удалось отправить заявку на закрытие: {get_last_error()}")
        return False

    retcode = int(getattr(result, "retcode", 0) or 0)
    if retcode == PARTIAL_RETCODE:
        trade["close_request_status"] = "partial_execution_anomaly"
        trade["close_request_error"] = (
            f"retcode={retcode}, comment={getattr(result, 'comment', '')}"
        )
        trade["close_request_order_ticket"] = int(getattr(result, "order", 0) or 0)
        trade["close_request_deal_ticket"] = int(getattr(result, "deal", 0) or 0)
        save_trade(trade)
        _activate_execution_anomaly(
            "PARTIAL_CLOSE_EXECUTION",
            "Аномалия закрытия позиции",
            (
                f"MT5 сообщил о частичном исполнении команды полного закрытия {symbol}. "
                "Робот не будет управлять возможным остатком и заблокирует новые сделки "
                "до ручной проверки."
            ),
            {
                "position_ticket": int(position.ticket),
                "requested_volume": float(position.volume),
                "executed_volume": float(getattr(result, "volume", 0.0) or 0.0),
                "retcode": retcode,
            },
        )
        return False

    if retcode not in SUCCESS_RETCODES:
        trade["close_request_status"] = "failed"
        trade["close_request_error"] = (
            f"retcode={retcode}, comment={getattr(result, 'comment', '')}"
        )
        save_trade(trade)
        log_event(
            "Заявка на закрытие отклонена: "
            + translate_retcode(retcode, getattr(result, "comment", ""))
        )
        return False

    executed_volume = float(getattr(result, "volume", position.volume) or position.volume)
    if not _volume_matches(executed_volume, float(position.volume)):
        trade["close_request_status"] = "volume_mismatch_anomaly"
        trade["close_request_error"] = (
            f"requested={position.volume}, executed={executed_volume}"
        )
        save_trade(trade)
        _activate_execution_anomaly(
            "CLOSE_EXECUTED_VOLUME_MISMATCH",
            "Аномалия объёма при закрытии",
            (
                f"MT5 подтвердил закрытие {symbol}, но исполненный объём "
                f"{executed_volume} не равен полному объёму позиции {position.volume}. "
                "Новые сделки заблокированы до ручной проверки."
            ),
            {
                "position_ticket": int(position.ticket),
                "requested_volume": float(position.volume),
                "executed_volume": executed_volume,
                "retcode": retcode,
            },
        )
        return False

    trade["close_request_status"] = "sent"
    trade["close_request_order_ticket"] = int(getattr(result, "order", 0) or 0)
    trade["close_request_deal_ticket"] = int(getattr(result, "deal", 0) or 0)
    save_trade(trade)

    log_event(
        f"Заявка на полное закрытие принята: позиция={position.ticket}, причина={reason}, "
        f"ордер={trade['close_request_order_ticket']}, "
        f"торговая_операция={trade['close_request_deal_ticket']}"
    )
    log_trade_event(
        event="CLOSE_REQUEST",
        symbol=symbol,
        direction=direction,
        volume=position.volume,
        ticket=position.ticket,
        comment=reason,
    )
    return True


def is_new_m15_for_trade(trade: dict, current_candle_time: str) -> bool:
    return trade.get("last_managed_m15_candle") != current_candle_time


def update_candle_counter(trade: dict, current_candle_time: str) -> dict:
    if is_new_m15_for_trade(trade, current_candle_time):
        trade["candles_in_trade"] = int(trade.get("candles_in_trade", 0)) + 1
        trade["last_managed_m15_candle"] = current_candle_time
    return trade


def manage_open_position(symbol: str, m15, settings: dict, current_candle_time: str) -> str:
    trade = get_active_trade()
    if trade is None:
        return "NO_STATE"

    position = get_open_position(symbol=symbol, trade=trade)
    if position is None:
        return "POSITION_MISSING"

    trade = _flush_pending_sl_notification(trade)

    requested_reason = trade.get("requested_close_reason")
    if requested_reason:
        close_position(symbol=symbol, reason=str(requested_reason))
        return "CLOSE_RETRY"

    direction = str(trade.get("direction", "")).upper()
    current_price = get_current_price(symbol, direction)
    if current_price is None:
        return "PRICE_UNAVAILABLE"

    trade = update_candle_counter(trade, current_candle_time)
    entry = float(trade["entry_price"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    tp3 = float(trade["tp3"])
    breakeven_offset = float(settings.get("breakeven_offset", 0.10))

    if not trade.get("requested_close_reason"):
        if direction == "BUY":
            if current_price >= tp1 and not trade.get("tp1_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, entry + breakeven_offset,
                    "TP1_HIT_SL_TO_BE_PLUS",
                    "Цель 1 достигнута, стоп-лосс перенесён в безубыток+",
                    "tp1_hit",
                    activate_breakeven=True,
                )
            if current_price >= tp2 and not trade.get("tp2_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, tp1,
                    "TP2_HIT_SL_TO_TP1",
                    "Цель 2 достигнута, стоп-лосс перенесён на уровень цели 1",
                    "tp2_hit",
                )
            if current_price >= tp3 and not trade.get("tp3_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, tp2,
                    "TP3_HIT_SL_TO_TP2",
                    "Цель 3 достигнута, стоп-лосс перенесён на уровень цели 2",
                    "tp3_hit",
                )
        elif direction == "SELL":
            if current_price <= tp1 and not trade.get("tp1_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, entry - breakeven_offset,
                    "TP1_HIT_SL_TO_BE_PLUS",
                    "Цель 1 достигнута, стоп-лосс перенесён в безубыток+",
                    "tp1_hit",
                    activate_breakeven=True,
                )
            if current_price <= tp2 and not trade.get("tp2_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, tp1,
                    "TP2_HIT_SL_TO_TP1",
                    "Цель 2 достигнута, стоп-лосс перенесён на уровень цели 1",
                    "tp2_hit",
                )
            if current_price <= tp3 and not trade.get("tp3_hit", False):
                trade, position = _apply_sl_stage(
                    trade, position, tp2,
                    "TP3_HIT_SL_TO_TP2",
                    "Цель 3 достигнута, стоп-лосс перенесён на уровень цели 2",
                    "tp3_hit",
                )

        if should_exit_trade(
            direction=direction,
            m15=m15,
            candles_in_trade=int(trade.get("candles_in_trade", 0)),
            breakeven_active=bool(trade.get("breakeven_active", False)),
        ):
            close_position(symbol=symbol, reason="EXIT_SIGNAL")
            return "CLOSE_REQUESTED"

    save_trade(trade)
    return "MANAGED"
