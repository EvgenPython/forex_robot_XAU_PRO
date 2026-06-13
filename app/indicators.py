import pandas as pd
import ta


def add_indicators(df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = df.copy()

    ema_fast = settings["ema"]["fast_period"]
    ema_slow = settings["ema"]["slow_period"]

    macd_fast = settings["macd"]["fast_period"]
    macd_slow = settings["macd"]["slow_period"]
    macd_signal = settings["macd"]["signal_period"]

    atr_period = settings["atr"]["period"]

    df["ema_fast"] = ta.trend.EMAIndicator(
        close=df["close"],
        window=ema_fast,
    ).ema_indicator()

    df["ema_slow"] = ta.trend.EMAIndicator(
        close=df["close"],
        window=ema_slow,
    ).ema_indicator()

    macd = ta.trend.MACD(
        close=df["close"],
        window_fast=macd_fast,
        window_slow=macd_slow,
        window_sign=macd_signal,
    )

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    df["atr"] = ta.volatility.AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=atr_period,
    ).average_true_range()

    return df