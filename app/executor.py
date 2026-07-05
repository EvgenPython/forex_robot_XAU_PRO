import MetaTrader5 as mt5

from app.monitoring_config import load_monitoring_config
from app.mt5_client import (
    get_last_error,
    get_tick,
    get_tick_age_seconds,
    send_order,
)
from app.text_ru import translate_direction, translate_retcode


BOT_MAGIC = 260611
DONE_RETCODE = int(getattr(mt5, "TRADE_RETCODE_DONE", 10009))
PARTIAL_RETCODE = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))

# Эти ответы нельзя считать однозначным отказом: заявка могла попасть на сервер,
# а Python не получил окончательное подтверждение. В таком случае повторный
# ордер запрещён до сверки позиции и истории MT5.
UNCERTAIN_RETCODES = {
    int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
    int(getattr(mt5, "TRADE_RETCODE_TIMEOUT", 10012)),
    int(getattr(mt5, "TRADE_RETCODE_CONNECTION", 10031)),
}


class OrderExecutionError(RuntimeError):
    def __init__(self, message: str, *, request: dict | None = None, result=None):
        super().__init__(message)
        self.request = request
        self.result = result


class OrderPreparationError(OrderExecutionError):
    pass


class OrderRejectedError(OrderExecutionError):
    pass


class OrderSendUncertainError(OrderExecutionError):
    pass


class PartialExecutionError(OrderExecutionError):
    pass


def _validate_entry_tick(symbol: str, tick) -> float:
    monitoring = load_monitoring_config()
    max_age_seconds = max(
        1,
        int(monitoring.get("entry_tick_max_age_seconds", 30)),
    )
    max_future_skew_seconds = max(
        0,
        int(monitoring.get("entry_tick_max_future_skew_seconds", 5)),
    )

    age_seconds = get_tick_age_seconds(tick)
    if age_seconds is None:
        raise OrderPreparationError(
            f"У котировки {symbol} отсутствует корректное время. Ордер не отправлен."
        )

    if age_seconds < -max_future_skew_seconds:
        raise OrderPreparationError(
            f"Время котировки {symbol} находится в будущем на "
            f"{abs(age_seconds):.1f} сек. Ордер не отправлен."
        )

    if age_seconds > max_age_seconds:
        raise OrderPreparationError(
            f"Котировка {symbol} устарела на {age_seconds:.1f} сек. "
            f"Допустимо не более {max_age_seconds} сек. Ордер не отправлен."
        )

    return age_seconds


def prepare_order(
    symbol: str,
    direction: str,
    volume: float,
    stop_loss: float,
    comment: str = "mt5_xau_bot",
) -> dict:
    tick = get_tick(symbol)

    if tick is None:
        raise OrderPreparationError(
            f"Не удалось получить котировку {symbol}: {get_last_error()}"
        )

    _validate_entry_tick(symbol, tick)

    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    elif direction == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        raise OrderPreparationError(f"Неподдерживаемое направление: {direction}")

    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": float(price),
        "sl": float(stop_loss),
        "deviation": 30,
        "magic": BOT_MAGIC,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }


def send_market_order(
    symbol: str,
    direction: str,
    volume: float,
    stop_loss: float,
    comment: str = "mt5_xau_bot",
) -> dict:
    request = prepare_order(
        symbol=symbol,
        direction=direction,
        volume=volume,
        stop_loss=stop_loss,
        comment=comment,
    )

    print("=" * 50)
    print("ЗАЯВКА ПОДГОТОВЛЕНА")
    print(f"Инструмент: {symbol}")
    print(f"Направление: {translate_direction(direction)}")
    print(f"Объём: {volume}")
    print(f"Цена: {request['price']}")
    print(f"Стоп-лосс: {stop_loss}")

    result = send_order(request)

    print("РЕЗУЛЬТАТ ОТПРАВКИ ЗАЯВКИ")
    print(result)
    print("=" * 50)

    if result is None:
        raise OrderSendUncertainError(
            f"MT5 не вернул результат отправки ордера: {get_last_error()}",
            request=request,
        )

    retcode = int(getattr(result, "retcode", 0) or 0)
    comment_text = str(getattr(result, "comment", "") or "")

    if retcode == PARTIAL_RETCODE:
        raise PartialExecutionError(
            "MT5 сообщил о неполном исполнении заявки на открытие полного объёма. "
            + translate_retcode(retcode, comment_text),
            request=request,
            result=result,
        )

    if retcode in UNCERTAIN_RETCODES:
        raise OrderSendUncertainError(
            "Итог исполнения заявки нельзя определить однозначно. "
            + translate_retcode(retcode, comment_text),
            request=request,
            result=result,
        )

    if retcode != DONE_RETCODE:
        raise OrderRejectedError(
            "Заявка отклонена. " + translate_retcode(retcode, comment_text),
            request=request,
            result=result,
        )

    executed_volume = float(getattr(result, "volume", volume) or volume)
    if abs(executed_volume - float(volume)) > 0.000001:
        raise PartialExecutionError(
            "MT5 вернул код полного исполнения, но исполненный объём отличается "
            f"от запрошенного: запрошено={volume}, исполнено={executed_volume}",
            request=request,
            result=result,
        )

    return {
        "sent": True,
        "request": request,
        "result": result,
        "order_ticket": int(getattr(result, "order", 0) or 0),
        "deal_ticket": int(getattr(result, "deal", 0) or 0),
        "executed_volume": executed_volume,
        "executed_price": float(
            getattr(result, "price", request["price"]) or request["price"]
        ),
    }
