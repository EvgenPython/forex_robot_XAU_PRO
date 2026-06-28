from app.models import Signal, SignalAction
from app.risk import calculate_tp
from app.structure import (
    get_market_structure,
    get_last_swing_low,
    get_last_swing_high,
)


def is_h4_bullish(h4) -> bool:
    return h4["ema_fast"].iloc[-1] > h4["ema_slow"].iloc[-1]


def is_h4_bearish(h4) -> bool:
    return h4["ema_fast"].iloc[-1] < h4["ema_slow"].iloc[-1]


def is_h1_bullish(h1) -> bool:
    return h1["ema_fast"].iloc[-1] > h1["ema_slow"].iloc[-1]


def is_h1_bearish(h1) -> bool:
    return h1["ema_fast"].iloc[-1] < h1["ema_slow"].iloc[-1]


def is_m15_buy_timing(m15) -> bool:
    candle = m15.iloc[-1]

    return (
        candle["close"] > candle["ema_fast"]
        and candle["macd"] > candle["macd_signal"]
    )


def is_m15_sell_timing(m15) -> bool:
    candle = m15.iloc[-1]

    return (
        candle["close"] < candle["ema_fast"]
        and candle["macd"] < candle["macd_signal"]
    )


def distance_filter_ok(m15, h1, multiplier: float) -> bool:
    current_price = float(m15["close"].iloc[-1])
    ema = float(m15["ema_fast"].iloc[-1])
    atr = float(h1["atr"].iloc[-1])

    distance = abs(current_price - ema)

    return distance <= atr * multiplier


def stop_distance_filter_ok(
    direction: str,
    entry_price: float,
    stop_loss: float,
    h1,
    max_stop_atr: float,
) -> bool:
    atr = float(h1["atr"].iloc[-1])
    stop_distance = abs(entry_price - stop_loss)

    if atr <= 0:
        return False

    stop_atr_ratio = stop_distance / atr

    if stop_atr_ratio > max_stop_atr:
        return False

    if direction == "BUY" and stop_loss >= entry_price:
        return False

    if direction == "SELL" and stop_loss <= entry_price:
        return False

    return True


def should_exit_trade(
    direction: str,
    m15,
    candles_in_trade: int,
    breakeven_active: bool,
) -> bool:
    if len(m15) < 3:
        return False

    if not breakeven_active and candles_in_trade < 3:
        return False

    candle = m15.iloc[-1]

    close_price = candle["close"]
    ema_fast = candle["ema_fast"]
    macd = candle["macd"]
    macd_signal = candle["macd_signal"]

    if direction == "BUY":
        return close_price < ema_fast and macd < macd_signal

    if direction == "SELL":
        return close_price > ema_fast and macd > macd_signal

    return False


def generate_signal(h4, h1, m15, settings: dict) -> Signal:
    buy_score = 0
    sell_score = 0

    buy_reasons = []
    sell_reasons = []

    min_score = settings.get("min_score", 75)
    max_score = settings.get("max_score", 90)

    distance_multiplier = settings.get(
        "distance_from_ema_atr_multiplier",
        1.2,
    )

    max_stop_atr = settings.get("max_stop_atr", 2.0)

    # H4 context

    if is_h4_bullish(h4):
        buy_score += 20
        buy_reasons.append("H4 bullish context")

    if is_h4_bearish(h4):
        sell_score += 20
        sell_reasons.append("H4 bearish context")

    # H1 trend

    if is_h1_bullish(h1):
        buy_score += 30
        buy_reasons.append("H1 bullish trend")

    if is_h1_bearish(h1):
        sell_score += 30
        sell_reasons.append("H1 bearish trend")

    # H1 structure

    structure = get_market_structure(h1)

    if structure == "BULLISH":
        buy_score += 10
        buy_reasons.append("H1 bullish structure")

    if structure == "BEARISH":
        sell_score += 10
        sell_reasons.append("H1 bearish structure")

    # M15 timing

    if is_m15_buy_timing(m15):
        buy_score += 25
        buy_reasons.append("M15 bullish timing")

    if is_m15_sell_timing(m15):
        sell_score += 25
        sell_reasons.append("M15 bearish timing")

    # Distance filter

    if distance_filter_ok(
        m15=m15,
        h1=h1,
        multiplier=distance_multiplier,
    ):
        buy_score += 15
        sell_score += 15

        buy_reasons.append("Distance filter OK")
        sell_reasons.append("Distance filter OK")

    best_score = max(buy_score, sell_score)

    if best_score < min_score:
        return Signal(
            action=SignalAction.WAIT,
            score=best_score,
            reasons=[
                f"BUY score={buy_score}",
                f"SELL score={sell_score}",
                f"Minimum score not reached: {min_score}",
            ],
            buy_score=buy_score,
            sell_score=sell_score,
        )

    if best_score > max_score:
        return Signal(
            action=SignalAction.WAIT,
            score=best_score,
            reasons=[
                f"BUY score={buy_score}",
                f"SELL score={sell_score}",
                f"Max score exceeded: {max_score}",
            ],
            buy_score=buy_score,
            sell_score=sell_score,
        )

    # BUY

    if buy_score >= min_score and buy_score > sell_score:
        entry = float(m15["close"].iloc[-1])
        swing_low = get_last_swing_low(h1)

        if swing_low is None:
            return Signal(
                action=SignalAction.WAIT,
                score=buy_score,
                reasons=["No swing low"],
                buy_score=buy_score,
                sell_score=sell_score,
            )

        if not stop_distance_filter_ok(
            direction="BUY",
            entry_price=entry,
            stop_loss=swing_low,
            h1=h1,
            max_stop_atr=max_stop_atr,
        ):
            return Signal(
                action=SignalAction.WAIT,
                score=buy_score,
                reasons=[
                    *buy_reasons,
                    "Stop distance filter blocked BUY",
                ],
                buy_score=buy_score,
                sell_score=sell_score,
            )

        tp1 = calculate_tp(
            direction="BUY",
            entry_price=entry,
            stop_loss=swing_low,
            rr_multiplier=settings.get("tp1_rr", 1.0),
        )

        tp2 = calculate_tp(
            direction="BUY",
            entry_price=entry,
            stop_loss=swing_low,
            rr_multiplier=settings.get("tp2_rr", 2.0),
        )

        tp3 = calculate_tp(
            direction="BUY",
            entry_price=entry,
            stop_loss=swing_low,
            rr_multiplier=settings.get("tp3_rr", 3.0),
        )

        return Signal(
            action=SignalAction.BUY,
            score=buy_score,
            reasons=buy_reasons,
            entry_price=entry,
            stop_loss=swing_low,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            atr=float(h1["atr"].iloc[-1]),
            buy_score=buy_score,
            sell_score=sell_score,
        )

    # SELL

    if sell_score >= min_score and sell_score > buy_score:
        entry = float(m15["close"].iloc[-1])
        swing_high = get_last_swing_high(h1)

        if swing_high is None:
            return Signal(
                action=SignalAction.WAIT,
                score=sell_score,
                reasons=["No swing high"],
                buy_score=buy_score,
                sell_score=sell_score,
            )

        if not stop_distance_filter_ok(
            direction="SELL",
            entry_price=entry,
            stop_loss=swing_high,
            h1=h1,
            max_stop_atr=max_stop_atr,
        ):
            return Signal(
                action=SignalAction.WAIT,
                score=sell_score,
                reasons=[
                    *sell_reasons,
                    "Stop distance filter blocked SELL",
                ],
                buy_score=buy_score,
                sell_score=sell_score,
            )

        tp1 = calculate_tp(
            direction="SELL",
            entry_price=entry,
            stop_loss=swing_high,
            rr_multiplier=settings.get("tp1_rr", 1.0),
        )

        tp2 = calculate_tp(
            direction="SELL",
            entry_price=entry,
            stop_loss=swing_high,
            rr_multiplier=settings.get("tp2_rr", 2.0),
        )

        tp3 = calculate_tp(
            direction="SELL",
            entry_price=entry,
            stop_loss=swing_high,
            rr_multiplier=settings.get("tp3_rr", 3.0),
        )

        return Signal(
            action=SignalAction.SELL,
            score=sell_score,
            reasons=sell_reasons,
            entry_price=entry,
            stop_loss=swing_high,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            atr=float(h1["atr"].iloc[-1]),
            buy_score=buy_score,
            sell_score=sell_score,
        )

    return Signal(
        action=SignalAction.WAIT,
        score=best_score,
        reasons=[
            f"BUY score={buy_score}",
            f"SELL score={sell_score}",
            "No dominant direction",
        ],
        buy_score=buy_score,
        sell_score=sell_score,
    )