import MetaTrader5 as mt5

from app.strategy import should_exit_trade
from app.state_manager import (
    get_active_trade,
    save_trade,
    clear_trade,
)
from app.logger import log_event, log_trade_event

from app.telegram_notifier import (
    notify_sl_moved,
    notify_trade_closed,
)


def safe_notify_sl_moved(
    symbol: str,
    direction: str,
    new_sl: float,
    reason: str,
    dry_run: bool,
):
    try:
        notify_sl_moved(
            symbol=symbol,
            direction=direction,
            new_sl=round(float(new_sl), 2),
            reason=reason,
            dry_run=dry_run,
        )
    except Exception as error:
        log_event(f"Telegram SL moved notification failed: {error}")


def safe_notify_trade_closed(
    symbol: str,
    direction: str,
    reason: str,
    close_price: float,
    money_result: float = 0.0,
    percent_result: float = 0.0,
    daily_percent: float = 0.0,
    dry_run: bool = True,
):
    try:
        notify_trade_closed(
            symbol=symbol,
            direction=direction,
            reason=reason,
            close_price=round(float(close_price), 2),
            money_result=float(money_result),
            percent_result=float(percent_result),
            daily_percent=float(daily_percent),
            dry_run=dry_run,
        )
    except Exception as error:
        log_event(f"Telegram close trade notification failed: {error}")


def get_open_position(symbol: str):
    positions = mt5.positions_get(symbol=symbol)

    if positions is None or len(positions) == 0:
        return None

    return positions[0]


def has_open_position(symbol: str) -> bool:
    return get_open_position(symbol) is not None


def modify_stop_loss(position_ticket: int, symbol: str, new_sl: float) -> bool:
    position = get_open_position(symbol)

    if position is None:
        log_event(f"No open position for SL modify: {symbol}")
        return False

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": position_ticket,
        "sl": round(float(new_sl), 2),
        "tp": position.tp,
    }

    result = mt5.order_send(request)

    if result is None:
        log_event(f"SL modification failed: {mt5.last_error()}")
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(f"SL modification rejected: {result}")
        return False

    log_event(f"SL modified: ticket={position_ticket}, new_sl={round(float(new_sl), 2)}")
    return True


def close_position(symbol: str, reason: str = "EXIT_SIGNAL") -> bool:
    position = get_open_position(symbol)

    if position is None:
        log_event(f"No open position to close: {symbol}")
        return False

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        log_event(f"Cannot get tick for close: {symbol}")
        return False

    if position.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        direction = "BUY"
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        direction = "SELL"

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": position.ticket,
        "symbol": symbol,
        "volume": position.volume,
        "type": order_type,
        "price": price,
        "deviation": 30,
        "magic": 260611,
        "comment": reason,
    }

    result = mt5.order_send(request)

    if result is None:
        log_event(f"Close position failed: {mt5.last_error()}")
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(f"Close position rejected: {result}")
        return False

    log_event(f"Position closed: ticket={position.ticket}, reason={reason}")

    log_trade_event(
        event="CLOSE_POSITION",
        symbol=symbol,
        direction=direction,
        volume=position.volume,
        ticket=position.ticket,
        profit=position.profit,
        comment=reason,
    )

    safe_notify_trade_closed(
        symbol=symbol,
        direction=direction,
        reason=reason,
        close_price=price,
        money_result=float(position.profit),
        percent_result=0.0,
        daily_percent=0.0,
        dry_run=False,
    )

    clear_trade()
    return True


def get_current_price(symbol: str, direction: str) -> float | None:
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        log_event(f"Cannot get tick for position management: {symbol}")
        return None

    if direction == "BUY":
        return float(tick.bid)

    if direction == "SELL":
        return float(tick.ask)

    return None


def is_new_m15_for_trade(trade: dict, current_candle_time: str) -> bool:
    last_managed = trade.get("last_managed_m15_candle")

    return last_managed != current_candle_time


def update_candle_counter(trade: dict, current_candle_time: str) -> dict:
    if is_new_m15_for_trade(trade, current_candle_time):
        trade["candles_in_trade"] = int(trade.get("candles_in_trade", 0)) + 1
        trade["last_managed_m15_candle"] = current_candle_time

    return trade


def calculate_dry_run_result(trade: dict, close_price: float) -> tuple[float, float]:
    entry = float(trade["entry_price"])
    volume = float(trade["volume"])
    direction = trade["direction"]

    if direction == "BUY":
        points_result = close_price - entry
    elif direction == "SELL":
        points_result = entry - close_price
    else:
        points_result = 0.0

    money_result = points_result * volume
    percent_result = 0.0

    return money_result, percent_result


def move_sl_dry_run(trade: dict, new_sl: float, event: str, comment: str) -> dict:
    trade["stop_loss"] = round(float(new_sl), 2)

    log_trade_event(
        event=event,
        symbol=trade.get("symbol", ""),
        direction=trade["direction"],
        entry_price=trade["entry_price"],
        stop_loss=trade["stop_loss"],
        tp1=trade["tp1"],
        tp2=trade["tp2"],
        tp3=trade["tp3"],
        volume=trade["volume"],
        ticket=trade.get("ticket", "DRY_RUN"),
        comment=f"DRY RUN: {comment}",
    )

    log_event(f"DRY RUN SL moved: {trade['stop_loss']} | {comment}")

    safe_notify_sl_moved(
        symbol=trade.get("symbol", ""),
        direction=trade["direction"],
        new_sl=trade["stop_loss"],
        reason=comment,
        dry_run=True,
    )

    return trade


def close_trade_dry_run(trade: dict, current_price: float, reason: str):
    money_result, percent_result = calculate_dry_run_result(
        trade=trade,
        close_price=current_price,
    )

    log_trade_event(
        event="DRY_RUN_CLOSE_POSITION",
        symbol=trade.get("symbol", ""),
        direction=trade["direction"],
        entry_price=trade["entry_price"],
        stop_loss=trade["stop_loss"],
        tp1=trade["tp1"],
        tp2=trade["tp2"],
        tp3=trade["tp3"],
        volume=trade["volume"],
        ticket=trade.get("ticket", "DRY_RUN"),
        profit=money_result,
        comment=f"{reason}, close_price={current_price}",
    )

    log_event(f"DRY RUN position closed: reason={reason}, price={current_price}")

    safe_notify_trade_closed(
        symbol=trade.get("symbol", ""),
        direction=trade["direction"],
        reason=reason,
        close_price=current_price,
        money_result=money_result,
        percent_result=percent_result,
        daily_percent=0.0,
        dry_run=True,
    )

    clear_trade()


def manage_dry_run_position(symbol: str, m15, settings: dict, current_candle_time: str):
    trade = get_active_trade()

    if trade is None:
        return

    direction = trade["direction"]

    current_price = get_current_price(symbol, direction)

    if current_price is None:
        return

    trade = update_candle_counter(trade, current_candle_time)

    entry = float(trade["entry_price"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    tp3 = float(trade["tp3"])
    stop_loss = float(trade["stop_loss"])

    breakeven_offset = float(settings.get("breakeven_offset", 0.10))

    log_event(
        f"DRY RUN MANAGE | direction={direction} | price={current_price} | "
        f"SL={stop_loss} | TP1={tp1} | TP2={tp2} | TP3={tp3} | "
        f"candles={trade.get('candles_in_trade', 0)}"
    )

    if direction == "BUY":
        if current_price <= stop_loss:
            close_trade_dry_run(trade, current_price, "STOP_LOSS")
            return

        if current_price >= tp1 and not trade.get("tp1_hit", False):
            new_sl = entry + breakeven_offset
            trade["tp1_hit"] = True
            trade["breakeven_active"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=new_sl,
                event="TP1_HIT_SL_TO_BE_PLUS",
                comment="TP1 достигнут, стоп перенесён в BE+",
            )

        if current_price >= tp2 and not trade.get("tp2_hit", False):
            trade["tp2_hit"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=tp1,
                event="TP2_HIT_SL_TO_TP1",
                comment="TP2 достигнут, стоп перенесён на TP1",
            )

        if current_price >= tp3 and not trade.get("tp3_hit", False):
            trade["tp3_hit"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=tp2,
                event="TP3_HIT_SL_TO_TP2",
                comment="TP3 достигнут, стоп перенесён на TP2",
            )

    elif direction == "SELL":
        if current_price >= stop_loss:
            close_trade_dry_run(trade, current_price, "STOP_LOSS")
            return

        if current_price <= tp1 and not trade.get("tp1_hit", False):
            new_sl = entry - breakeven_offset
            trade["tp1_hit"] = True
            trade["breakeven_active"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=new_sl,
                event="TP1_HIT_SL_TO_BE_PLUS",
                comment="TP1 достигнут, стоп перенесён в BE+",
            )

        if current_price <= tp2 and not trade.get("tp2_hit", False):
            trade["tp2_hit"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=tp1,
                event="TP2_HIT_SL_TO_TP1",
                comment="TP2 достигнут, стоп перенесён на TP1",
            )

        if current_price <= tp3 and not trade.get("tp3_hit", False):
            trade["tp3_hit"] = True
            trade = move_sl_dry_run(
                trade=trade,
                new_sl=tp2,
                event="TP3_HIT_SL_TO_TP2",
                comment="TP3 достигнут, стоп перенесён на TP2",
            )

    if should_exit_trade(
        direction=direction,
        m15=m15,
        candles_in_trade=int(trade.get("candles_in_trade", 0)),
        breakeven_active=bool(trade.get("breakeven_active", False)),
    ):
        close_trade_dry_run(trade, current_price, "EXIT_SIGNAL")
        return

    save_trade(trade)


def manage_real_position(symbol: str, m15, settings: dict, current_candle_time: str):
    trade = get_active_trade()
    position = get_open_position(symbol)

    if position is None:
        if trade is not None:
            log_event("State has active_trade, but MT5 position not found. Clearing state.")
            clear_trade()
        return

    if trade is None:
        log_event("MT5 position exists, but state is empty. Manual check required.")
        return

    direction = trade["direction"]

    current_price = get_current_price(symbol, direction)

    if current_price is None:
        return

    trade = update_candle_counter(trade, current_candle_time)

    entry = float(trade["entry_price"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    tp3 = float(trade["tp3"])

    breakeven_offset = float(settings.get("breakeven_offset", 0.10))

    if direction == "BUY":
        if current_price >= tp1 and not trade.get("tp1_hit", False):
            new_sl = entry + breakeven_offset

            if modify_stop_loss(position.ticket, symbol, new_sl):
                trade["stop_loss"] = round(float(new_sl), 2)
                trade["tp1_hit"] = True
                trade["breakeven_active"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP1 достигнут, стоп перенесён в BE+",
                    dry_run=False,
                )

        if current_price >= tp2 and not trade.get("tp2_hit", False):
            if modify_stop_loss(position.ticket, symbol, tp1):
                trade["stop_loss"] = round(float(tp1), 2)
                trade["tp2_hit"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP2 достигнут, стоп перенесён на TP1",
                    dry_run=False,
                )

        if current_price >= tp3 and not trade.get("tp3_hit", False):
            if modify_stop_loss(position.ticket, symbol, tp2):
                trade["stop_loss"] = round(float(tp2), 2)
                trade["tp3_hit"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP3 достигнут, стоп перенесён на TP2",
                    dry_run=False,
                )

    elif direction == "SELL":
        if current_price <= tp1 and not trade.get("tp1_hit", False):
            new_sl = entry - breakeven_offset

            if modify_stop_loss(position.ticket, symbol, new_sl):
                trade["stop_loss"] = round(float(new_sl), 2)
                trade["tp1_hit"] = True
                trade["breakeven_active"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP1 достигнут, стоп перенесён в BE+",
                    dry_run=False,
                )

        if current_price <= tp2 and not trade.get("tp2_hit", False):
            if modify_stop_loss(position.ticket, symbol, tp1):
                trade["stop_loss"] = round(float(tp1), 2)
                trade["tp2_hit"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP2 достигнут, стоп перенесён на TP1",
                    dry_run=False,
                )

        if current_price <= tp3 and not trade.get("tp3_hit", False):
            if modify_stop_loss(position.ticket, symbol, tp2):
                trade["stop_loss"] = round(float(tp2), 2)
                trade["tp3_hit"] = True

                safe_notify_sl_moved(
                    symbol=symbol,
                    direction=direction,
                    new_sl=trade["stop_loss"],
                    reason="TP3 достигнут, стоп перенесён на TP2",
                    dry_run=False,
                )

    if should_exit_trade(
        direction=direction,
        m15=m15,
        candles_in_trade=int(trade.get("candles_in_trade", 0)),
        breakeven_active=bool(trade.get("breakeven_active", False)),
    ):
        close_position(symbol=symbol, reason="EXIT_SIGNAL")
        return

    save_trade(trade)


def manage_open_position(
    symbol: str,
    m15,
    settings: dict,
    current_candle_time: str,
):
    dry_run = bool(settings.get("dry_run", True))

    if dry_run:
        manage_dry_run_position(
            symbol=symbol,
            m15=m15,
            settings=settings,
            current_candle_time=current_candle_time,
        )
        return

    manage_real_position(
        symbol=symbol,
        m15=m15,
        settings=settings,
        current_candle_time=current_candle_time,
    )