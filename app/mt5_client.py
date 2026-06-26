import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5


ROOT_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_CONFIG_PATH = ROOT_DIR / "config" / "accounts.json"


_mt5_lock = threading.Lock()
_connected = False
_ready_symbols = set()


def load_accounts_config() -> dict:
    if not ACCOUNTS_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Accounts config not found: {ACCOUNTS_CONFIG_PATH}")

    with open(ACCOUNTS_CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def is_mt5_connected() -> bool:
    account_info = mt5.account_info()
    return account_info is not None


def connect_mt5(force_reconnect: bool = False, quiet: bool = False) -> bool:
    global _connected

    with _mt5_lock:
        if not force_reconnect and _connected and is_mt5_connected():
            return True

        config = load_accounts_config()

        login = int(config["login"])
        password = config["password"]
        server = config["server"]
        mt5_path = config.get("mt5_path")

        if force_reconnect:
            mt5.shutdown()
            _connected = False
            _ready_symbols.clear()

        if mt5_path:
            initialized = mt5.initialize(path=mt5_path)
        else:
            initialized = mt5.initialize()

        if not initialized:
            print("MT5 initialization failed")
            print(mt5.last_error())
            _connected = False
            return False

        authorized = mt5.login(
            login=login,
            password=password,
            server=server,
        )

        if not authorized:
            print("MT5 login failed")
            print(mt5.last_error())
            mt5.shutdown()
            _connected = False
            return False

        account_info = mt5.account_info()

        if account_info is None:
            print("Failed to get account info")
            print(mt5.last_error())
            mt5.shutdown()
            _connected = False
            return False

        _connected = True

        if not quiet:
            print("=" * 50)
            print("MT5 CONNECTED")
            print(f"Login: {account_info.login}")
            print(f"Server: {account_info.server}")
            print(f"Balance: {account_info.balance}")
            print(f"Equity: {account_info.equity}")
            print(f"Trade allowed: {account_info.trade_allowed}")
            print("=" * 50)

        return True


def ensure_mt5_connection(symbol: str | None = None, quiet: bool = False) -> bool:
    if not connect_mt5(quiet=quiet):
        return False

    if symbol is not None:
        return ensure_symbol(symbol=symbol, quiet=quiet)

    return True


def reconnect_mt5(symbol: str | None = None, quiet: bool = False) -> bool:
    if not connect_mt5(force_reconnect=True, quiet=quiet):
        return False

    if symbol is not None:
        return ensure_symbol(symbol=symbol, quiet=quiet, force_check=True)

    return True


def shutdown_mt5():
    global _connected

    with _mt5_lock:
        mt5.shutdown()
        _connected = False
        _ready_symbols.clear()
        print("MT5 shutdown")


def ensure_symbol(
    symbol: str,
    quiet: bool = False,
    force_check: bool = False,
) -> bool:
    if not force_check and symbol in _ready_symbols:
        return True

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        print(f"Symbol not found: {symbol}")
        return False

    if not symbol_info.visible:
        selected = mt5.symbol_select(symbol, True)

        if not selected:
            print(f"Failed to select symbol: {symbol}")
            print(mt5.last_error())
            return False

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        print(f"Symbol info not available after select: {symbol}")
        return False

    _ready_symbols.add(symbol)

    if not quiet:
        print("=" * 50)
        print("SYMBOL READY")
        print(f"Symbol: {symbol}")
        print(f"Digits: {symbol_info.digits}")
        print(f"Point: {symbol_info.point}")
        print(f"Spread: {symbol_info.spread}")
        print(f"Trade mode: {symbol_info.trade_mode}")
        print("=" * 50)

    return True


def get_account_info(auto_reconnect: bool = True):
    account = mt5.account_info()

    if account is not None:
        return account

    if not auto_reconnect:
        return None

    if not reconnect_mt5(quiet=True):
        return None

    return mt5.account_info()


def get_symbol_info(symbol: str):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None

    return mt5.symbol_info(symbol)


def get_tick(symbol: str):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None

    return mt5.symbol_info_tick(symbol)


def get_closed_deals_from_history(date_from, date_to, symbol: str | None = None):
    """
    Возвращает закрытые сделки из истории MT5 за период.

    ВАЖНО:
    - Используется только для Telegram-статистики.
    - Торговую логику не меняет.
    - CSV trades_YYYY_MM.csv продолжит писаться как раньше.
    """
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return []

    deals = mt5.history_deals_get(date_from, date_to)

    if deals is None:
        if not reconnect_mt5(symbol=symbol, quiet=True):
            return []

        deals = mt5.history_deals_get(date_from, date_to)

    if deals is None:
        return []

    # Пытаемся восстановить направление позиции по входящей сделке.
    # У закрывающей сделки type часто противоположный направлению позиции,
    # поэтому для BUY/SELL лучше использовать DEAL_ENTRY_IN.
    position_direction_by_id = {}

    for deal in deals:
        deal_symbol = str(getattr(deal, "symbol", ""))

        if symbol and deal_symbol != symbol:
            continue

        entry = int(getattr(deal, "entry", -1))
        deal_type = int(getattr(deal, "type", -1))
        position_id = int(getattr(deal, "position_id", 0))

        # MT5: DEAL_ENTRY_IN = 0, DEAL_TYPE_BUY = 0, DEAL_TYPE_SELL = 1
        if entry == 0 and position_id:
            if deal_type == 0:
                position_direction_by_id[position_id] = "BUY"
            elif deal_type == 1:
                position_direction_by_id[position_id] = "SELL"

    result = []

    for deal in deals:
        deal_symbol = str(getattr(deal, "symbol", ""))

        if symbol and deal_symbol != symbol:
            continue

        entry = int(getattr(deal, "entry", -1))

        # MT5: DEAL_ENTRY_OUT = 1, это выход/закрытие позиции.
        if entry != 1:
            continue

        profit = float(getattr(deal, "profit", 0.0))
        commission = float(getattr(deal, "commission", 0.0))
        swap = float(getattr(deal, "swap", 0.0))
        position_id = int(getattr(deal, "position_id", 0))
        deal_type = int(getattr(deal, "type", -1))

        direction = position_direction_by_id.get(position_id)

        if direction is None:
            # Fallback: закрывающий BUY обычно закрывает SELL-позицию,
            # закрывающий SELL обычно закрывает BUY-позицию.
            if deal_type == 0:
                direction = "SELL"
            elif deal_type == 1:
                direction = "BUY"
            else:
                direction = ""

        result.append({
            "time": datetime.fromtimestamp(
                int(getattr(deal, "time", 0)),
                tz=timezone.utc,
            ),
            "ticket": int(getattr(deal, "ticket", 0)),
            "position_id": position_id,
            "symbol": deal_symbol,
            "direction": direction,
            "profit": profit,
            "commission": commission,
            "swap": swap,
            "net_profit": profit + commission + swap,
        })

    return result
