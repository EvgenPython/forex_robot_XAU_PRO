import pandas as pd
import MetaTrader5 as mt5


TIMEFRAME_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


def get_rates(symbol: str, timeframe: str, bars: int = 500) -> pd.DataFrame:
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    rates = mt5.copy_rates_from_pos(
        symbol,
        TIMEFRAME_MAP[timeframe],
        0,
        bars,
    )

    if rates is None:
        raise RuntimeError(f"MT5 returned no rates for {symbol} {timeframe}: {mt5.last_error()}")

    if len(rates) == 0:
        raise RuntimeError(f"No candles loaded for {symbol} {timeframe}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    return df


def load_market_data(symbol: str) -> dict:
    h4 = get_rates(symbol=symbol, timeframe="H4", bars=500)
    h1 = get_rates(symbol=symbol, timeframe="H1", bars=500)
    m15 = get_rates(symbol=symbol, timeframe="M15", bars=500)

    print("=" * 50)
    print("MARKET DATA LOADED")
    print(f"{symbol} H4 candles: {len(h4)} | last: {h4.iloc[-1]['time']}")
    print(f"{symbol} H1 candles: {len(h1)} | last: {h1.iloc[-1]['time']}")
    print(f"{symbol} M15 candles: {len(m15)} | last: {m15.iloc[-1]['time']}")
    print("=" * 50)

    return {
        "h4": h4,
        "h1": h1,
        "m15": m15,
    }