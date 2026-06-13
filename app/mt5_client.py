import json
from pathlib import Path

import MetaTrader5 as mt5


ROOT_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_CONFIG_PATH = ROOT_DIR / "config" / "accounts.json"


def load_accounts_config() -> dict:
    if not ACCOUNTS_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Accounts config not found: {ACCOUNTS_CONFIG_PATH}")

    with open(ACCOUNTS_CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def connect_mt5() -> bool:
    config = load_accounts_config()

    login = int(config["login"])
    password = config["password"]
    server = config["server"]
    mt5_path = config.get("mt5_path")

    if mt5_path:
        initialized = mt5.initialize(path=mt5_path)
    else:
        initialized = mt5.initialize()

    if not initialized:
        print("MT5 initialization failed")
        print(mt5.last_error())
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
        return False

    account_info = mt5.account_info()

    if account_info is None:
        print("Failed to get account info")
        print(mt5.last_error())
        mt5.shutdown()
        return False

    print("=" * 50)
    print("MT5 CONNECTED")
    print(f"Login: {account_info.login}")
    print(f"Server: {account_info.server}")
    print(f"Balance: {account_info.balance}")
    print(f"Equity: {account_info.equity}")
    print(f"Trade allowed: {account_info.trade_allowed}")
    print("=" * 50)

    return True


def shutdown_mt5():
    mt5.shutdown()
    print("MT5 shutdown")


def ensure_symbol(symbol: str) -> bool:
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

    print("=" * 50)
    print("SYMBOL READY")
    print(f"Symbol: {symbol}")
    print(f"Digits: {symbol_info.digits}")
    print(f"Point: {symbol_info.point}")
    print(f"Spread: {symbol_info.spread}")
    print(f"Trade mode: {symbol_info.trade_mode}")
    print("=" * 50)

    return True


def get_account_info():
    return mt5.account_info()


def get_symbol_info(symbol: str):
    return mt5.symbol_info(symbol)


def get_tick(symbol: str):
    return mt5.symbol_info_tick(symbol)