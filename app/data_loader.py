import MetaTrader5 as mt5
import pandas as pd

from app.mt5_client import get_last_error, get_rates_from_pos


TIMEFRAME_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


def load_rates(symbol: str, timeframe_name: str, count: int = 500) -> pd.DataFrame:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    rates = get_rates_from_pos(symbol, timeframe, 0, count)

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"MT5 не вернул свечи для {symbol} {timeframe_name}: {get_last_error()}"
        )

    dataframe = pd.DataFrame(rates)
    dataframe["time"] = pd.to_datetime(dataframe["time"], unit="s", utc=True)
    return dataframe


def load_market_data(symbol: str) -> dict[str, pd.DataFrame]:
    return {
        "h4": load_rates(symbol, "H4"),
        "h1": load_rates(symbol, "H1"),
        "m15": load_rates(symbol, "M15"),
    }
