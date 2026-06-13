import pandas as pd


def detect_pivots(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> pd.DataFrame:
    """
    IMPORTANT:
    Pivot becomes CONFIRMED only after `right` candles.

    This prevents repainting / future leak.
    """

    df = df.copy()

    df["pivot_high"] = False
    df["pivot_low"] = False

    for i in range(left, len(df) - right):
        current_high = df.loc[i, "high"]
        current_low = df.loc[i, "low"]

        left_highs = df.loc[i - left:i - 1, "high"]
        right_highs = df.loc[i + 1:i + right, "high"]

        left_lows = df.loc[i - left:i - 1, "low"]
        right_lows = df.loc[i + 1:i + right, "low"]

        is_pivot_high = (
            current_high > left_highs.max()
            and current_high > right_highs.max()
        )

        is_pivot_low = (
            current_low < left_lows.min()
            and current_low < right_lows.min()
        )

        # CONFIRM pivot only AFTER right candles
        confirmed_index = i + right

        if confirmed_index >= len(df):
            continue

        if is_pivot_high:
            df.loc[confirmed_index, "pivot_high"] = True
            df.loc[confirmed_index, "pivot_high_price"] = current_high

        if is_pivot_low:
            df.loc[confirmed_index, "pivot_low"] = True
            df.loc[confirmed_index, "pivot_low_price"] = current_low

    return df


def get_market_structure(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    lookback: int = 120,
) -> str:
    recent = df.tail(lookback).reset_index(drop=True)

    recent = detect_pivots(
        recent,
        left=left,
        right=right,
    )

    pivot_highs = recent[
        recent["pivot_high"] == True
    ]

    pivot_lows = recent[
        recent["pivot_low"] == True
    ]

    if len(pivot_highs) < 2:
        return "UNKNOWN"

    if len(pivot_lows) < 2:
        return "UNKNOWN"

    previous_high = pivot_highs[
        "pivot_high_price"
    ].iloc[-2]

    last_high = pivot_highs[
        "pivot_high_price"
    ].iloc[-1]

    previous_low = pivot_lows[
        "pivot_low_price"
    ].iloc[-2]

    last_low = pivot_lows[
        "pivot_low_price"
    ].iloc[-1]

    # Higher High + Higher Low
    if (
        last_high > previous_high
        and last_low > previous_low
    ):
        return "BULLISH"

    # Lower High + Lower Low
    if (
        last_high < previous_high
        and last_low < previous_low
    ):
        return "BEARISH"

    return "RANGE"


def get_last_swing_low(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    lookback: int = 120,
) -> float | None:
    recent = df.tail(lookback).reset_index(drop=True)

    recent = detect_pivots(
        recent,
        left=left,
        right=right,
    )

    pivot_lows = recent[
        recent["pivot_low"] == True
    ]

    if pivot_lows.empty:
        return None

    return float(
        pivot_lows["pivot_low_price"].iloc[-1]
    )


def get_last_swing_high(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    lookback: int = 120,
) -> float | None:
    recent = df.tail(lookback).reset_index(drop=True)

    recent = detect_pivots(
        recent,
        left=left,
        right=right,
    )

    pivot_highs = recent[
        recent["pivot_high"] == True
    ]

    if pivot_highs.empty:
        return None

    return float(
        pivot_highs["pivot_high_price"].iloc[-1]
    )