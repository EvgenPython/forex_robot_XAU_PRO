import math

from app.mt5_client import get_symbol_info


def calculate_tp(
    direction: str,
    entry_price: float,
    stop_loss: float,
    rr_multiplier: float,
) -> float:
    risk = abs(entry_price - stop_loss)

    if direction == "BUY":
        return entry_price + risk * rr_multiplier

    if direction == "SELL":
        return entry_price - risk * rr_multiplier

    raise ValueError(f"Неизвестное направление: {direction}")


def calculate_rr(
    direction: str,
    entry_price: float,
    close_price: float,
    stop_loss: float,
) -> float:
    risk = abs(entry_price - stop_loss)

    if risk <= 0:
        return 0.0

    if direction == "BUY":
        return (close_price - entry_price) / risk

    if direction == "SELL":
        return (entry_price - close_price) / risk

    raise ValueError(f"Неизвестное направление: {direction}")


def calculate_result_percent(rr: float, risk_percent: float) -> float:
    return rr * risk_percent


def calculate_risk_amount(balance: float, risk_percent: float) -> float:
    return balance * (risk_percent / 100)


def calculate_stop_distance(entry_price: float, stop_loss: float) -> float:
    return abs(entry_price - stop_loss)


def normalize_volume_down(volume: float, volume_step: float) -> float:
    if volume_step <= 0:
        return 0.0

    steps = math.floor(volume / volume_step)
    return round(steps * volume_step, 2)


def calculate_position_size(
    balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
    symbol: str,
) -> float:
    """
    Рассчитывает объём позиции по фиксированному риску счёта.

    Если рассчитанный объём меньше минимального лота брокера,
    функция возвращает 0.0 и не увеличивает риск.
    """

    if balance <= 0:
        return 0.0

    if risk_percent <= 0:
        return 0.0

    stop_distance = abs(entry_price - stop_loss)

    if stop_distance <= 0:
        return 0.0

    risk_amount = balance * (risk_percent / 100)

    symbol_info = get_symbol_info(symbol)

    if symbol_info is None:
        raise RuntimeError(f"Не удалось получить данные инструмента {symbol}")

    tick_size = float(symbol_info.trade_tick_size)
    tick_value = float(symbol_info.trade_tick_value)

    volume_min = float(symbol_info.volume_min)
    volume_step = float(symbol_info.volume_step)
    volume_max = float(symbol_info.volume_max)

    if tick_size <= 0:
        raise RuntimeError(f"Некорректный размер тика для {symbol}")

    if tick_value <= 0:
        raise RuntimeError(f"Некорректная стоимость тика для {symbol}")

    if volume_min <= 0:
        raise RuntimeError(f"Некорректный минимальный объём для {symbol}")

    if volume_step <= 0:
        raise RuntimeError(f"Некорректный шаг объёма для {symbol}")

    if volume_max <= 0:
        raise RuntimeError(f"Некорректный максимальный объём для {symbol}")

    money_loss_per_lot = (stop_distance / tick_size) * tick_value

    if money_loss_per_lot <= 0:
        return 0.0

    raw_volume = risk_amount / money_loss_per_lot

    if raw_volume < volume_min:
        return 0.0

    volume = normalize_volume_down(raw_volume, volume_step)

    if volume < volume_min:
        return 0.0

    if volume > volume_max:
        volume = volume_max

    return round(volume, 2)