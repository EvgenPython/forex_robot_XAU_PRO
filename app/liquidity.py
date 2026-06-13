from dataclasses import dataclass
from typing import Optional


@dataclass
class LiquidityLevel:
    kind: str  # SWING_HIGH, SWING_LOW, EQUAL_HIGHS, EQUAL_LOWS
    price: float
    zone_low: float
    zone_high: float
    touches: int
    strength: int
    index: int


@dataclass
class LiquiditySnapshot:
    nearest_high_above: Optional[LiquidityLevel]
    nearest_high_below: Optional[LiquidityLevel]

    nearest_low_above: Optional[LiquidityLevel]
    nearest_low_below: Optional[LiquidityLevel]

    swing_highs: list[LiquidityLevel]
    swing_lows: list[LiquidityLevel]

    equal_highs: list[LiquidityLevel]
    equal_lows: list[LiquidityLevel]


def detect_swing_highs(
    df,
    left: int = 2,
    right: int = 2,
) -> list[LiquidityLevel]:
    levels = []

    if len(df) < left + right + 1:
        return levels

    for i in range(left, len(df) - right):
        current_high = float(df["high"].iloc[i])

        left_highs = df["high"].iloc[i - left:i]
        right_highs = df["high"].iloc[i + 1:i + 1 + right]

        if current_high > left_highs.max() and current_high > right_highs.max():
            levels.append(
                LiquidityLevel(
                    kind="SWING_HIGH",
                    price=current_high,
                    zone_low=current_high,
                    zone_high=current_high,
                    touches=1,
                    strength=1,
                    index=i,
                )
            )

    return levels


def detect_swing_lows(
    df,
    left: int = 2,
    right: int = 2,
) -> list[LiquidityLevel]:
    levels = []

    if len(df) < left + right + 1:
        return levels

    for i in range(left, len(df) - right):
        current_low = float(df["low"].iloc[i])

        left_lows = df["low"].iloc[i - left:i]
        right_lows = df["low"].iloc[i + 1:i + 1 + right]

        if current_low < left_lows.min() and current_low < right_lows.min():
            levels.append(
                LiquidityLevel(
                    kind="SWING_LOW",
                    price=current_low,
                    zone_low=current_low,
                    zone_high=current_low,
                    touches=1,
                    strength=1,
                    index=i,
                )
            )

    return levels


def group_equal_levels(
    levels: list[LiquidityLevel],
    tolerance: float,
    kind: str,
) -> list[LiquidityLevel]:
    if not levels:
        return []

    sorted_levels = sorted(
        levels,
        key=lambda x: x.price,
    )

    groups = []
    current_group = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        group_prices = [x.price for x in current_group]
        group_avg = sum(group_prices) / len(group_prices)

        if abs(level.price - group_avg) <= tolerance:
            current_group.append(level)
        else:
            if len(current_group) >= 2:
                groups.append(
                    _make_equal_level(
                        group=current_group,
                        kind=kind,
                    )
                )

            current_group = [level]

    if len(current_group) >= 2:
        groups.append(
            _make_equal_level(
                group=current_group,
                kind=kind,
            )
        )

    return groups


def _make_equal_level(
    group: list[LiquidityLevel],
    kind: str,
) -> LiquidityLevel:
    prices = [x.price for x in group]

    zone_low = min(prices)
    zone_high = max(prices)
    avg_price = sum(prices) / len(prices)

    return LiquidityLevel(
        kind=kind,
        price=avg_price,
        zone_low=zone_low,
        zone_high=zone_high,
        touches=len(group),
        strength=min(5, len(group)),
        index=max(x.index for x in group),
    )


def _nearest_above(
    levels: list[LiquidityLevel],
    current_price: float,
) -> Optional[LiquidityLevel]:
    candidates = [
        level for level in levels
        if level.zone_low > current_price
    ]

    return min(
        candidates,
        key=lambda x: x.zone_low - current_price,
        default=None,
    )


def _nearest_below(
    levels: list[LiquidityLevel],
    current_price: float,
) -> Optional[LiquidityLevel]:
    candidates = [
        level for level in levels
        if level.zone_high < current_price
    ]

    return min(
        candidates,
        key=lambda x: current_price - x.zone_high,
        default=None,
    )


def analyze_liquidity(
    df,
    lookback: int = 150,
    swing_left: int = 2,
    swing_right: int = 2,
    tolerance_atr_multiplier: float = 0.15,
) -> LiquiditySnapshot:
    recent = df.tail(lookback).copy()

    current_price = float(recent["close"].iloc[-1])

    if "atr" in recent.columns:
        atr = float(recent["atr"].iloc[-1])
    else:
        atr = current_price * 0.001

    tolerance = atr * tolerance_atr_multiplier

    swing_highs = detect_swing_highs(
        recent,
        left=swing_left,
        right=swing_right,
    )

    swing_lows = detect_swing_lows(
        recent,
        left=swing_left,
        right=swing_right,
    )

    equal_highs = group_equal_levels(
        levels=swing_highs,
        tolerance=tolerance,
        kind="EQUAL_HIGHS",
    )

    equal_lows = group_equal_levels(
        levels=swing_lows,
        tolerance=tolerance,
        kind="EQUAL_LOWS",
    )

    high_liquidity = swing_highs + equal_highs
    low_liquidity = swing_lows + equal_lows

    return LiquiditySnapshot(
        nearest_high_above=_nearest_above(
            high_liquidity,
            current_price,
        ),
        nearest_high_below=_nearest_below(
            high_liquidity,
            current_price,
        ),
        nearest_low_above=_nearest_above(
            low_liquidity,
            current_price,
        ),
        nearest_low_below=_nearest_below(
            low_liquidity,
            current_price,
        ),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        equal_highs=equal_highs,
        equal_lows=equal_lows,
    )


def format_liquidity_level(
    level: Optional[LiquidityLevel],
) -> str:
    if level is None:
        return "None"

    return (
        f"{level.kind} "
        f"price={level.price:.2f} "
        f"zone=({level.zone_low:.2f}-{level.zone_high:.2f}) "
        f"touches={level.touches} "
        f"strength={level.strength}"
    )


def describe_liquidity(
    liquidity: LiquiditySnapshot,
) -> dict:
    return {
        "nearest_high_above": format_liquidity_level(
            liquidity.nearest_high_above
        ),
        "nearest_high_below": format_liquidity_level(
            liquidity.nearest_high_below
        ),
        "nearest_low_above": format_liquidity_level(
            liquidity.nearest_low_above
        ),
        "nearest_low_below": format_liquidity_level(
            liquidity.nearest_low_below
        ),
    }