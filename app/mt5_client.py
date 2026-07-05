import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import MetaTrader5 as mt5


ROOT_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_CONFIG_PATH = ROOT_DIR / "config" / "accounts.json"


_mt5_lock = threading.RLock()
_connected = False
_ready_symbols: set[str] = set()
_connection_failures = 0
_last_connection_error: str | None = None
_last_connection_failure_at: str | None = None
_last_connection_success_at: str | None = None
_next_retry_monotonic = 0.0
_next_retry_at: str | None = None
_reconnect_count = 0
_last_health_code: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_accounts_config() -> dict:
    if not ACCOUNTS_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Файл конфигурации счетов не найден: {ACCOUNTS_CONFIG_PATH}")

    with open(ACCOUNTS_CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def _load_backoff_settings() -> tuple[int, int]:
    try:
        from app.monitoring_config import load_monitoring_config

        config = load_monitoring_config()
        initial = max(1, int(config.get("mt5_reconnect_initial_seconds", 5)))
        maximum = max(initial, int(config.get("mt5_reconnect_max_seconds", 60)))
        return initial, maximum
    except Exception:
        return 5, 60


def _calculate_backoff(failures: int) -> int:
    initial, maximum = _load_backoff_settings()
    exponent = min(max(0, int(failures) - 1), 10)
    return min(maximum, initial * (2 ** exponent))


def _record_connection_failure(stage: str, error: Any) -> None:
    global _connected
    global _connection_failures
    global _last_connection_error
    global _last_connection_failure_at
    global _next_retry_monotonic
    global _next_retry_at

    now = datetime.now(timezone.utc)
    _connected = False
    _ready_symbols.clear()
    _connection_failures += 1
    _last_connection_error = f"{stage}: {error}"[:1500]
    _last_connection_failure_at = now.isoformat()
    delay = _calculate_backoff(_connection_failures)
    _next_retry_monotonic = time.monotonic() + delay
    _next_retry_at = datetime.fromtimestamp(now.timestamp() + delay, tz=timezone.utc).isoformat()


def _record_connection_success(*, reconnected: bool = False) -> None:
    global _connected
    global _connection_failures
    global _last_connection_error
    global _last_connection_success_at
    global _next_retry_monotonic
    global _next_retry_at
    global _reconnect_count

    was_failed = _connection_failures > 0 or not _connected
    _connected = True
    _connection_failures = 0
    _last_connection_error = None
    _last_connection_success_at = utc_now_iso()
    _next_retry_monotonic = 0.0
    _next_retry_at = None
    if reconnected or was_failed:
        _reconnect_count += 1


def _retry_allowed() -> bool:
    return time.monotonic() >= float(_next_retry_monotonic or 0.0)


def mt5_call(function: Callable, *args, **kwargs):
    with _mt5_lock:
        return function(*args, **kwargs)


def get_last_error():
    return mt5_call(mt5.last_error)


def get_connection_diagnostics() -> dict:
    with _mt5_lock:
        return {
            "connected": bool(_connected),
            "consecutive_failures": int(_connection_failures),
            "last_error": _last_connection_error,
            "last_failure_at": _last_connection_failure_at,
            "last_success_at": _last_connection_success_at,
            "next_retry_at": _next_retry_at,
            "retry_blocked": not _retry_allowed(),
            "reconnect_count": int(_reconnect_count),
        }


def is_mt5_connected() -> bool:
    with _mt5_lock:
        if not _connected:
            return False
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if terminal is None or account is None:
            _record_connection_failure("connection_check", mt5.last_error())
            return False
        return True


def connect_mt5(
    force_reconnect: bool = False,
    quiet: bool = False,
    ignore_backoff: bool = False,
) -> bool:
    global _connected

    with _mt5_lock:
        if not force_reconnect and _connected:
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            if terminal is not None and account is not None:
                _record_connection_success()
                return True
            _record_connection_failure("stale_connection", mt5.last_error())

        if not ignore_backoff and not _retry_allowed():
            return False

        config = load_accounts_config()
        login = int(config["login"])
        password = config["password"]
        server = config["server"]
        mt5_path = config.get("mt5_path")

        if force_reconnect or _connected or _connection_failures > 0:
            try:
                mt5.shutdown()
            finally:
                _connected = False
                _ready_symbols.clear()

        initialized = mt5.initialize(path=mt5_path) if mt5_path else mt5.initialize()
        if not initialized:
            error = mt5.last_error()
            _record_connection_failure("initialize", error)
            if not quiet:
                print("Не удалось инициализировать MT5")
                print(error)
            return False

        authorized = mt5.login(login=login, password=password, server=server)
        if not authorized:
            error = mt5.last_error()
            mt5.shutdown()
            _record_connection_failure("login", error)
            if not quiet:
                print("Не удалось войти в торговый счёт MT5")
                print(error)
            return False

        terminal_info = mt5.terminal_info()
        account_info = mt5.account_info()
        if terminal_info is None or account_info is None:
            error = mt5.last_error()
            mt5.shutdown()
            _record_connection_failure("post_login_probe", error)
            if not quiet:
                print("Не удалось получить данные терминала или счёта MT5")
                print(error)
            return False

        _ready_symbols.clear()
        _record_connection_success(reconnected=True)

        if not quiet:
            print("=" * 50)
            print("MT5 ПОДКЛЮЧЁН")
            print(f"Счёт: {account_info.login}")
            print(f"Сервер: {account_info.server}")
            print(f"Баланс: {account_info.balance}")
            print(f"Средства: {account_info.equity}")
            print(f"Торговля разрешена: {account_info.trade_allowed}")
            print("=" * 50)

        return True


def ensure_mt5_connection(symbol: str | None = None, quiet: bool = False) -> bool:
    if not connect_mt5(quiet=quiet):
        return False

    if symbol is not None:
        return ensure_symbol(symbol=symbol, quiet=quiet)

    return True


def reconnect_mt5(
    symbol: str | None = None,
    quiet: bool = False,
    ignore_backoff: bool = False,
) -> bool:
    if not connect_mt5(
        force_reconnect=True,
        quiet=quiet,
        ignore_backoff=ignore_backoff,
    ):
        return False

    if symbol is not None:
        return ensure_symbol(symbol=symbol, quiet=quiet, force_check=True)

    return True


def shutdown_mt5() -> None:
    global _connected

    with _mt5_lock:
        mt5.shutdown()
        _connected = False
        _ready_symbols.clear()
        print("Соединение с MT5 закрыто")


def ensure_symbol(symbol: str, quiet: bool = False, force_check: bool = False) -> bool:
    with _mt5_lock:
        if not force_check and symbol in _ready_symbols:
            return True

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            if not quiet:
                print(f"Инструмент не найден: {symbol}")
            return False

        if not symbol_info.visible:
            selected = mt5.symbol_select(symbol, True)
            if not selected:
                if not quiet:
                    print(f"Не удалось выбрать инструмент: {symbol}")
                    print(mt5.last_error())
                return False

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            if not quiet:
                print(f"Данные инструмента недоступны после выбора: {symbol}")
            return False

        _ready_symbols.add(symbol)

        if not quiet:
            print("=" * 50)
            print("ИНСТРУМЕНТ ГОТОВ")
            print(f"Инструмент: {symbol}")
            print(f"Знаков после запятой: {symbol_info.digits}")
            print(f"Размер пункта: {symbol_info.point}")
            print(f"Спред: {symbol_info.spread}")
            print(f"Режим торговли: {symbol_info.trade_mode}")
            print("=" * 50)

        return True


def get_account_info(auto_reconnect: bool = True):
    global _connected

    with _mt5_lock:
        account = mt5.account_info()
        if account is not None:
            return account
        _record_connection_failure("account_info", mt5.last_error())

    if not auto_reconnect:
        return None

    if not reconnect_mt5(quiet=True):
        return None

    return mt5_call(mt5.account_info)


def get_terminal_info(auto_reconnect: bool = True):
    global _connected

    with _mt5_lock:
        terminal = mt5.terminal_info()
        if terminal is not None:
            return terminal
        _record_connection_failure("terminal_info", mt5.last_error())

    if not auto_reconnect:
        return None

    if not reconnect_mt5(quiet=True):
        return None

    return mt5_call(mt5.terminal_info)


def get_symbol_info(symbol: str):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None
    return mt5_call(mt5.symbol_info, symbol)


def get_tick(symbol: str):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None

    tick = mt5_call(mt5.symbol_info_tick, symbol)
    if tick is not None:
        return tick

    # После переподключения инструмент может временно потерять готовность.
    # Повторно выбираем его один раз, не создавая цикл переподключений.
    if ensure_symbol(symbol=symbol, quiet=True, force_check=True):
        return mt5_call(mt5.symbol_info_tick, symbol)
    return None


def get_tick_age_seconds(tick, now: datetime | None = None) -> float | None:
    """Возвращает возраст котировки относительно UTC в секундах."""
    if tick is None:
        return None

    time_msc = int(getattr(tick, "time_msc", 0) or 0)
    if time_msc > 0:
        tick_timestamp = time_msc / 1000.0
    else:
        tick_timestamp = float(getattr(tick, "time", 0) or 0)

    if tick_timestamp <= 0:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_timestamp = current.astimezone(timezone.utc).timestamp()
    return current_timestamp - tick_timestamp


def get_positions(symbol: str | None = None, ticket: int | None = None):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None

    if ticket is not None:
        return mt5_call(mt5.positions_get, ticket=int(ticket))
    if symbol is not None:
        return mt5_call(mt5.positions_get, symbol=symbol)
    return mt5_call(mt5.positions_get)


def send_order(request: dict):
    if not ensure_mt5_connection(symbol=request.get("symbol"), quiet=True):
        return None
    return mt5_call(mt5.order_send, request)


def get_rates_from_pos(symbol: str, timeframe: int, start_pos: int, count: int):
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None
    return mt5_call(mt5.copy_rates_from_pos, symbol, timeframe, start_pos, count)


def probe_mt5_health(symbol: str) -> dict:
    global _last_health_code

    connected = connect_mt5(quiet=True)
    diagnostics = get_connection_diagnostics()
    if not connected:
        result = {
            "healthy": False,
            "issue_code": "TERMINAL_UNAVAILABLE",
            "message": diagnostics.get("last_error") or "Соединение с MT5 недоступно",
            "mt5_available": False,
            "terminal_available": False,
            "account_available": False,
            "quotes_available": False,
            "trade_allowed": False,
            "terminal": None,
            "account": None,
            "tick": None,
            "connection": diagnostics,
        }
        result["status_changed"] = _last_health_code != result["issue_code"]
        _last_health_code = result["issue_code"]
        return result

    terminal = get_terminal_info(auto_reconnect=False)
    if terminal is None:
        result = {
            "healthy": False,
            "issue_code": "TERMINAL_UNAVAILABLE",
            "message": f"Данные терминала недоступны: {get_last_error()}",
            "mt5_available": False,
            "terminal_available": False,
            "account_available": False,
            "quotes_available": False,
            "trade_allowed": False,
            "terminal": None,
            "account": None,
            "tick": None,
            "connection": get_connection_diagnostics(),
        }
        result["status_changed"] = _last_health_code != result["issue_code"]
        _last_health_code = result["issue_code"]
        return result

    account = get_account_info(auto_reconnect=False)
    if account is None:
        result = {
            "healthy": False,
            "issue_code": "ACCOUNT_UNAVAILABLE",
            "message": f"Данные торгового счёта недоступны: {get_last_error()}",
            "mt5_available": True,
            "terminal_available": True,
            "account_available": False,
            "quotes_available": False,
            "trade_allowed": False,
            "terminal": terminal,
            "account": None,
            "tick": None,
            "connection": get_connection_diagnostics(),
        }
        result["status_changed"] = _last_health_code != result["issue_code"]
        _last_health_code = result["issue_code"]
        return result

    symbol_ready = ensure_symbol(symbol=symbol, quiet=True)
    tick = mt5_call(mt5.symbol_info_tick, symbol) if symbol_ready else None
    quotes_available = bool(
        tick is not None
        and float(getattr(tick, "bid", 0.0) or 0.0) > 0
        and float(getattr(tick, "ask", 0.0) or 0.0) > 0
    )
    terminal_trade_allowed = bool(getattr(terminal, "trade_allowed", True))
    account_trade_allowed = bool(getattr(account, "trade_allowed", False))
    trade_allowed = terminal_trade_allowed and account_trade_allowed

    if not quotes_available:
        issue_code = "QUOTES_UNAVAILABLE"
        message = (
            f"Инструмент {symbol} не готов к торговле"
            if not symbol_ready
            else f"Нет корректной котировки для {symbol}"
        )
    elif not trade_allowed:
        issue_code = "TRADE_DISABLED"
        message = (
            "Автоматическая торговля или разрешение счёта отключено: "
            f"терминал={terminal_trade_allowed}, счёт={account_trade_allowed}"
        )
    else:
        issue_code = None
        message = "MT5 работает нормально"

    result = {
        "healthy": issue_code is None,
        "issue_code": issue_code,
        "message": message,
        "mt5_available": True,
        "terminal_available": True,
        "account_available": True,
        "quotes_available": quotes_available,
        "trade_allowed": trade_allowed,
        "terminal_trade_allowed": terminal_trade_allowed,
        "account_trade_allowed": account_trade_allowed,
        "terminal": terminal,
        "account": account,
        "tick": tick,
        "connection": get_connection_diagnostics(),
    }
    result["status_changed"] = _last_health_code != issue_code
    _last_health_code = issue_code
    return result


def _constant(name: str, default: int) -> int:
    return int(getattr(mt5, name, default))


def _deal_reason_name(reason: int) -> str:
    mapping = {
        _constant("DEAL_REASON_CLIENT", 0): "CLIENT",
        _constant("DEAL_REASON_MOBILE", 1): "MOBILE",
        _constant("DEAL_REASON_WEB", 2): "WEB",
        _constant("DEAL_REASON_EXPERT", 3): "EXPERT",
        _constant("DEAL_REASON_SL", 4): "SL",
        _constant("DEAL_REASON_TP", 5): "TP",
        _constant("DEAL_REASON_SO", 6): "STOP_OUT",
        _constant("DEAL_REASON_ROLLOVER", 7): "ROLLOVER",
        _constant("DEAL_REASON_VMARGIN", 8): "VARIATION_MARGIN",
        _constant("DEAL_REASON_SPLIT", 9): "SPLIT",
        _constant("DEAL_REASON_CORPORATE_ACTION", 10): "CORPORATE_ACTION",
    }
    return mapping.get(int(reason), f"UNKNOWN_{reason}")


def normalize_deal(deal: Any) -> dict:
    timestamp = int(getattr(deal, "time", 0) or 0)
    time_msc = int(getattr(deal, "time_msc", 0) or 0)
    reason = int(getattr(deal, "reason", -1))

    return {
        "time": datetime.fromtimestamp(timestamp, tz=timezone.utc),
        "time_msc": time_msc,
        "ticket": int(getattr(deal, "ticket", 0) or 0),
        "order": int(getattr(deal, "order", 0) or 0),
        "position_id": int(getattr(deal, "position_id", 0) or 0),
        "symbol": str(getattr(deal, "symbol", "") or ""),
        "type": int(getattr(deal, "type", -1)),
        "entry": int(getattr(deal, "entry", -1)),
        "reason": reason,
        "reason_name": _deal_reason_name(reason),
        "magic": int(getattr(deal, "magic", 0) or 0),
        "volume": float(getattr(deal, "volume", 0.0) or 0.0),
        "price": float(getattr(deal, "price", 0.0) or 0.0),
        "profit": float(getattr(deal, "profit", 0.0) or 0.0),
        "commission": float(getattr(deal, "commission", 0.0) or 0.0),
        "swap": float(getattr(deal, "swap", 0.0) or 0.0),
        "fee": float(getattr(deal, "fee", 0.0) or 0.0),
        "comment": str(getattr(deal, "comment", "") or ""),
        "external_id": str(getattr(deal, "external_id", "") or ""),
    }


def get_history_deals(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    symbol: str | None = None,
    position_id: int | None = None,
) -> list[dict]:
    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return []

    with _mt5_lock:
        def fetch_deals():
            if position_id:
                return mt5.history_deals_get(position=int(position_id))
            if date_from is not None and date_to is not None:
                return mt5.history_deals_get(date_from, date_to)
            raise ValueError("Необходимо указать диапазон дат или position_id")

        deals = fetch_deals()
        if deals is None and reconnect_mt5(symbol=symbol, quiet=True):
            deals = fetch_deals()
        if deals is None:
            return []

        normalized = [normalize_deal(deal) for deal in deals]

    if symbol:
        normalized = [deal for deal in normalized if deal["symbol"] == symbol]

    normalized.sort(key=lambda item: (item["time_msc"], item["ticket"]))
    return normalized


def get_deals_for_position(position_id: int, symbol: str | None = None) -> list[dict]:
    return get_history_deals(position_id=position_id, symbol=symbol)


def _is_entry_in(entry: int) -> bool:
    return entry == _constant("DEAL_ENTRY_IN", 0)


def _is_entry_out(entry: int) -> bool:
    return entry in {
        _constant("DEAL_ENTRY_OUT", 1),
        _constant("DEAL_ENTRY_INOUT", 2),
        _constant("DEAL_ENTRY_OUT_BY", 3),
    }


def _direction_from_type(deal_type: int, closing: bool = False) -> str:
    buy_type = _constant("DEAL_TYPE_BUY", 0)
    sell_type = _constant("DEAL_TYPE_SELL", 1)

    if not closing:
        if deal_type == buy_type:
            return "BUY"
        if deal_type == sell_type:
            return "SELL"
    else:
        if deal_type == buy_type:
            return "SELL"
        if deal_type == sell_type:
            return "BUY"

    return ""


def get_deal_direction(deal: dict, closing: bool = False) -> str:
    return _direction_from_type(int(deal.get("type", -1)), closing=closing)


def get_closed_deals_from_history(
    date_from: datetime,
    date_to: datetime,
    symbol: str | None = None,
) -> list[dict]:
    deals = get_history_deals(date_from=date_from, date_to=date_to, symbol=symbol)
    position_direction_by_id: dict[int, str] = {}

    for deal in deals:
        if _is_entry_in(int(deal["entry"])) and deal["position_id"]:
            direction = _direction_from_type(int(deal["type"]), closing=False)
            if direction:
                position_direction_by_id[int(deal["position_id"])] = direction

    result: list[dict] = []
    for deal in deals:
        if not _is_entry_out(int(deal["entry"])):
            continue

        position_id = int(deal["position_id"])
        direction = position_direction_by_id.get(position_id)
        if not direction:
            direction = _direction_from_type(int(deal["type"]), closing=True)

        item = dict(deal)
        item["direction"] = direction
        item["net_profit"] = (
            float(deal["profit"])
            + float(deal["commission"])
            + float(deal["swap"])
            + float(deal["fee"])
        )
        result.append(item)

    return result


def get_trade_deals_from_history(
    date_from: datetime,
    date_to: datetime,
    symbol: str | None = None,
) -> list[dict]:
    return get_history_deals(date_from=date_from, date_to=date_to, symbol=symbol)


def is_closing_deal(deal: dict) -> bool:
    return _is_entry_out(int(deal.get("entry", -1)))


def is_opening_deal(deal: dict) -> bool:
    return _is_entry_in(int(deal.get("entry", -1)))
