import json
import threading
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