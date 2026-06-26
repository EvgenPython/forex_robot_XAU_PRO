import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.mt5_client import (
    ensure_mt5_connection,
    get_account_info,
    get_symbol_info,
    get_tick,
    get_closed_deals_from_history,
)

from app.state_manager import load_state
from app.telegram_notifier import load_telegram_config


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"

BUTTONS = {"status", "trade", "last_signal", "server", "robot", "stats", "efficiency"}
_callback_lock = asyncio.Lock()


def load_settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def get_allowed_chat_ids(config: dict) -> list[str]:
    allowed = config.get("allowed_chat_ids")

    if allowed:
        return [str(chat_id) for chat_id in allowed]

    chat_id = config.get("chat_id", "")

    if chat_id:
        return [str(chat_id)]

    return []


def is_allowed(chat_id: int, config: dict) -> bool:
    return str(chat_id) in get_allowed_chat_ids(config)


def main_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(text="📊 Статус", callback_data="status")
    kb.button(text="💼 Сделка", callback_data="trade")
    kb.button(text="📈 Статистика", callback_data="stats")
    kb.button(text="📊 Эффективность", callback_data="efficiency")
    kb.button(text="📜 Последний сигнал", callback_data="last_signal")
    kb.button(text="🖥 Сервер", callback_data="server")
    kb.button(text="⚙️ Робот", callback_data="robot")

    kb.adjust(1)
    return kb.as_markup()


def format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}$"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_bool(value: bool) -> str:
    return "да" if value else "нет"


def format_price(value: float) -> str:
    return f"{float(value):.2f}"


def format_direction(direction: str) -> str:
    direction = str(direction).upper()
    if direction == "BUY":
        return "🔺 BUY"
    if direction == "SELL":
        return "🔻 SELL"
    return direction or "нет данных"


def format_mode(dry_run: bool, account_data: dict | None = None) -> str:
    if dry_run:
        return "⚪ DRY RUN"

    server = ""
    if account_data is not None:
        server = str(account_data.get("server", ""))

    if "demo" in server.lower():
        return "🟡 DEMO"

    return "🟢 REAL"


def format_short_time(value: Any) -> str:
    if not value:
        return "нет данных"

    text = str(value)

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        pass

    if " " in text:
        time_part = text.split(" ", 1)[1]
        return time_part[:5]

    if "T" in text:
        time_part = text.split("T", 1)[1]
        return time_part[:5]

    return text


def parse_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default

    text = str(value).strip()
    if text == "":
        return default

    try:
        return float(text.replace(",", "."))
    except Exception:
        return default


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def get_account_data() -> dict | None:
    settings = load_settings()
    symbol = settings.get("symbol", "XAUUSD")

    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return None

    account = get_account_info(auto_reconnect=True)

    if account is None:
        return None

    return {
        "balance": float(account.balance),
        "equity": float(account.equity),
        "profit": float(account.profit),
        "trade_allowed": bool(account.trade_allowed),
        "server": str(account.server),
        "login": int(account.login),
    }


def get_daily_info(state: dict, account_data: dict) -> dict:
    daily_guard = state.get("daily_guard", {})

    start_balance = float(daily_guard.get("start_balance", account_data["balance"]))
    equity = float(account_data["equity"])
    money_result = equity - start_balance

    if start_balance <= 0:
        daily_result_percent = 0.0
    else:
        daily_result_percent = (money_result / start_balance) * 100

    drawdown_percent = float(daily_guard.get("drawdown_percent", 0.0))

    return {
        "start_balance": start_balance,
        "daily_result_money": money_result,
        "daily_result_percent": daily_result_percent,
        "drawdown_percent": drawdown_percent,
        "trading_blocked": bool(daily_guard.get("trading_blocked", False)),
        "hard_stop_triggered": bool(daily_guard.get("hard_stop_triggered", False)),
    }


def calculate_trade_result(trade: dict) -> dict:
    symbol = trade.get("symbol", "XAUUSD")
    direction = str(trade.get("direction", "")).upper()
    entry_price = float(trade.get("entry_price", 0))
    volume = float(trade.get("volume", 0))

    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        return {"price": 0.0, "money": 0.0, "percent": 0.0, "available": False}

    tick = get_tick(symbol)
    info = get_symbol_info(symbol)

    if tick is None or info is None or entry_price <= 0 or volume <= 0:
        return {"price": 0.0, "money": 0.0, "percent": 0.0, "available": False}

    if direction == "BUY":
        current_price = float(tick.bid)
        price_diff = current_price - entry_price
    elif direction == "SELL":
        current_price = float(tick.ask)
        price_diff = entry_price - current_price
    else:
        return {"price": 0.0, "money": 0.0, "percent": 0.0, "available": False}

    tick_size = float(info.trade_tick_size)
    tick_value = float(info.trade_tick_value)

    if tick_size <= 0 or tick_value <= 0:
        return {"price": current_price, "money": 0.0, "percent": 0.0, "available": False}

    money = (price_diff / tick_size) * tick_value * volume
    account = get_account_data()
    balance = account["balance"] if account else 0.0
    percent = (money / balance) * 100 if balance > 0 else 0.0

    return {"price": current_price, "money": money, "percent": percent, "available": True}


def build_status_text() -> str:
    settings = load_settings()
    state = load_state()
    account_data = get_account_data()

    symbol = settings.get("symbol", "XAUUSD")
    dry_run = bool(settings.get("dry_run", True))

    if account_data is None:
        return "❌ Не удалось получить данные счёта MT5."

    mode = format_mode(dry_run=dry_run, account_data=account_data)
    daily_info = get_daily_info(state, account_data)
    active_trade = state.get("active_trade")

    trade_status = "есть" if active_trade else "нет"
    last_m15 = format_short_time(state.get("last_m15_candle", "нет данных"))

    day_icon = "🟢" if daily_info["daily_result_money"] > 0 else "🔴" if daily_info["daily_result_money"] < 0 else "⚪"

    return (
        "📊 <b>Статус бота</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Последняя M15: <b>{last_m15}</b>\n\n"
        f"Баланс: <b>{account_data['balance']:.2f}$</b>\n"
        f"Средства: <b>{account_data['equity']:.2f}$</b>\n"
        f"Результат открытой сделки: <b>{format_money(account_data['profit'])}</b>\n\n"
        f"{day_icon} Дневной результат: <b>{format_money(daily_info['daily_result_money'])} / {format_percent(daily_info['daily_result_percent'])}</b>\n"
        f"Дневная просадка: <b>{daily_info['drawdown_percent']:.2f}%</b>\n\n"
        f"Открытая сделка: <b>{trade_status}</b>\n"
        f"Новые сделки запрещены: <b>{format_bool(daily_info['trading_blocked'])}</b>"
    )


def build_trade_text() -> str:
    state = load_state()
    trade = state.get("active_trade")

    if trade is None:
        return "💼 <b>Открытая сделка</b>\n\nСейчас открытой сделки нет."

    symbol = trade.get("symbol", "XAUUSD")
    direction = str(trade.get("direction", "")).upper()
    entry_price = float(trade.get("entry_price", 0))
    stop_loss = float(trade.get("stop_loss", 0))
    tp1 = float(trade.get("tp1", 0))
    tp2 = float(trade.get("tp2", 0))
    tp3 = float(trade.get("tp3", 0))
    volume = float(trade.get("volume", 0))
    score = trade.get("score", "")
    dry_run = bool(trade.get("dry_run", True))

    tp1_hit = bool(trade.get("tp1_hit", False))
    tp2_hit = bool(trade.get("tp2_hit", False))
    tp3_hit = bool(trade.get("tp3_hit", False))
    breakeven_active = bool(trade.get("breakeven_active", False))
    candles_in_trade = int(trade.get("candles_in_trade", 0))

    account_data = get_account_data()
    mode = format_mode(dry_run=dry_run, account_data=account_data)
    result = calculate_trade_result(trade)

    if result["available"]:
        result_icon = "🟢" if result["money"] > 0 else "🔴" if result["money"] < 0 else "⚪"
        result_text = (
            f"Текущая цена: <b>{result['price']:.2f}</b>\n"
            f"{result_icon} Результат: <b>{format_money(result['money'])} / {format_percent(result['percent'])}</b>"
        )
    else:
        result_text = "Текущий результат: <b>недоступно</b>"

    return (
        "💼 <b>Открытая сделка</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{format_direction(direction)}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Вход: <b>{entry_price:.2f}</b>\n"
        f"Стоп: <b>{stop_loss:.2f}</b>\n\n"
        f"{'✅' if tp1_hit else '❌'} TP1: <b>{tp1:.2f}</b>\n"
        f"{'✅' if tp2_hit else '❌'} TP2: <b>{tp2:.2f}</b>\n"
        f"{'✅' if tp3_hit else '❌'} TP3: <b>{tp3:.2f}</b>\n\n"
        f"Объём: <b>{volume}</b>\n"
        f"Свечей в сделке: <b>{candles_in_trade}</b>\n"
        f"BE+ активен: <b>{format_bool(breakeven_active)}</b>\n\n"
        f"{result_text}"
    )



def get_stats_periods() -> dict:
    """
    Периоды считаем по локальному времени VPS/MT5.
    Это ближе к тому, что пользователь видит во вкладке History в терминале.
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    return {
        "today": today_start,
        "week": week_start,
        "month": month_start,
        "to": now + timedelta(minutes=1),
    }


def load_closed_deals_from_mt5() -> list[dict]:
    settings = load_settings()
    symbol = settings.get("symbol", "XAUUSD")
    periods = get_stats_periods()

    return get_closed_deals_from_history(
        date_from=periods["month"],
        date_to=periods["to"],
        symbol=symbol,
    )


def build_period_stats(closed_deals: list[dict], start_time: datetime) -> dict:
    selected = []

    for deal in closed_deals:
        deal_time = deal.get("time")

        if deal_time is None:
            continue

        # mt5_client возвращает UTC-aware datetime.
        # Для сравнения с локальным периодом убираем timezone и сравниваем как локальную дату/время.
        if getattr(deal_time, "tzinfo", None) is not None:
            deal_time = deal_time.astimezone().replace(tzinfo=None)

        if deal_time >= start_time:
            selected.append(deal)

    total = len(selected)
    wins = sum(1 for deal in selected if float(deal.get("net_profit", 0.0)) > 0)
    losses = sum(1 for deal in selected if float(deal.get("net_profit", 0.0)) < 0)
    breakevens = sum(1 for deal in selected if float(deal.get("net_profit", 0.0)) == 0)
    money = sum(float(deal.get("net_profit", 0.0)) for deal in selected)

    gross_profit = sum(
        float(deal.get("net_profit", 0.0))
        for deal in selected
        if float(deal.get("net_profit", 0.0)) > 0
    )
    gross_loss = abs(
        sum(
            float(deal.get("net_profit", 0.0))
            for deal in selected
            if float(deal.get("net_profit", 0.0)) < 0
        )
    )

    winrate = (wins / total * 100) if total > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "money": money,
        "winrate": winrate,
        "profit_factor": profit_factor,
    }


def format_stats_block(title: str, stats: dict, base_balance: float) -> str:
    money = float(stats["money"])
    percent = (money / base_balance * 100) if base_balance > 0 else 0.0
    icon = "🟢" if money > 0 else "🔴" if money < 0 else "⚪"

    pf_text = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] > 0 else "0.00"

    return (
        f"{title}\n\n"
        f"Сделок: <b>{stats['total']}</b>\n"
        f"🟢 Прибыльных: <b>{stats['wins']}</b>\n"
        f"🔴 Убыточных: <b>{stats['losses']}</b>\n"
        f"⚪ Безубыток: <b>{stats['breakevens']}</b>\n"
        f"Winrate: <b>{stats['winrate']:.2f}%</b>\n"
        f"Profit Factor: <b>{pf_text}</b>\n"
        f"{icon} P/L: <b>{format_money(money)} / {format_percent(percent)}</b>"
    )


def build_stats_text() -> str:
    settings = load_settings()
    symbol = settings.get("symbol", "XAUUSD")

    closed_deals = load_closed_deals_from_mt5()

    account_data = get_account_data()
    base_balance = account_data["balance"] if account_data else 0.0

    periods = get_stats_periods()

    today_stats = build_period_stats(closed_deals, periods["today"])
    week_stats = build_period_stats(closed_deals, periods["week"])
    month_stats = build_period_stats(closed_deals, periods["month"])

    return (
        "📈 <b>Статистика торговли</b>\n"
        "Источник: <b>история MT5</b>\n"
        f"Инструмент: <b>{symbol}</b>\n"
        "Учитываются только закрытые сделки.\n\n"
        f"{format_stats_block('📅 Сегодня', today_stats, base_balance)}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{format_stats_block('📆 За 7 дней', week_stats, base_balance)}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{format_stats_block('🗓 За 30 дней', month_stats, base_balance)}"
    )




def calculate_deals_metrics(closed_deals: list[dict]) -> dict:
    total = len(closed_deals)
    profits = [float(deal.get("net_profit", 0.0)) for deal in closed_deals]

    wins_values = [value for value in profits if value > 0]
    loss_values = [value for value in profits if value < 0]
    breakevens = sum(1 for value in profits if value == 0)

    wins = len(wins_values)
    losses = len(loss_values)

    gross_profit = sum(wins_values)
    gross_loss = abs(sum(loss_values))
    money = sum(profits)

    winrate = (wins / total * 100) if total > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    avg_win = (gross_profit / wins) if wins > 0 else 0.0
    avg_loss = (sum(loss_values) / losses) if losses > 0 else 0.0

    best_trade = max(profits) if profits else 0.0
    worst_trade = min(profits) if profits else 0.0

    buy_count = 0
    sell_count = 0

    for deal in closed_deals:
        direction = str(deal.get("direction", "")).upper()
        if direction == "BUY":
            buy_count += 1
        elif direction == "SELL":
            sell_count += 1

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "money": money,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "buy_count": buy_count,
        "sell_count": sell_count,
    }


def format_profit_factor(value: float | None) -> str:
    if value is None:
        return "—"

    return f"{value:.2f}"


def build_efficiency_text() -> str:
    settings = load_settings()
    symbol = settings.get("symbol", "XAUUSD")

    closed_deals = load_closed_deals_from_mt5()
    periods = get_stats_periods()

    deals_30d = []
    for deal in closed_deals:
        deal_time = deal.get("time")
        if deal_time is None:
            continue

        if getattr(deal_time, "tzinfo", None) is not None:
            deal_time = deal_time.astimezone().replace(tzinfo=None)

        if deal_time >= periods["month"]:
            deals_30d.append(deal)

    metrics = calculate_deals_metrics(deals_30d)

    pf_text = format_profit_factor(metrics["profit_factor"])

    if metrics["buy_count"] == 0 and metrics["sell_count"] == 0 and metrics["total"] > 0:
        direction_text = "BUY / SELL: <b>недоступно из истории MT5</b>"
    else:
        direction_text = (
            f"BUY: <b>{metrics['buy_count']}</b>\n"
            f"SELL: <b>{metrics['sell_count']}</b>"
        )

    icon = "🟢" if metrics["money"] > 0 else "🔴" if metrics["money"] < 0 else "⚪"

    return (
        "📊 <b>Эффективность стратегии</b>\n"
        "Период: <b>30 дней</b>\n"
        "Источник: <b>история MT5</b>\n"
        f"Инструмент: <b>{symbol}</b>\n\n"
        f"Всего сделок: <b>{metrics['total']}</b>\n"
        f"🟢 Прибыльных: <b>{metrics['wins']}</b>\n"
        f"🔴 Убыточных: <b>{metrics['losses']}</b>\n"
        f"⚪ Безубыток: <b>{metrics['breakevens']}</b>\n\n"
        f"Winrate: <b>{metrics['winrate']:.2f}%</b>\n"
        f"Profit Factor: <b>{pf_text}</b>\n"
        f"{icon} P/L: <b>{format_money(metrics['money'])}</b>\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"Средняя прибыль: <b>{format_money(metrics['avg_win'])}</b>\n"
        f"Средний убыток: <b>{format_money(metrics['avg_loss'])}</b>\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"Лучший трейд: <b>{format_money(metrics['best_trade'])}</b>\n"
        f"Худший трейд: <b>{format_money(metrics['worst_trade'])}</b>\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{direction_text}"
    )


def read_last_signal_from_state() -> str | None:
    state = load_state()
    last_signal = state.get("last_signal")

    if not last_signal:
        return None

    action = last_signal.get("action", "нет данных")
    score = last_signal.get("score", "нет данных")
    reasons = last_signal.get("reasons", [])
    time_value = format_short_time(last_signal.get("time", "нет данных"))

    if isinstance(reasons, list):
        reasons_text = "\n".join([f"- {reason}" for reason in reasons])
    else:
        reasons_text = str(reasons)

    return (
        "📜 <b>Последний сигнал</b>\n\n"
        f"Время: <b>{time_value}</b>\n"
        f"Решение: <b>{action}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Причины:\n<code>{reasons_text}</code>"
    )


def read_last_signal_from_log() -> str:
    state_text = read_last_signal_from_state()

    if state_text is not None:
        return state_text

    return (
        "📜 <b>Последний сигнал</b>\n\n"
        "Последний сигнал пока не сохранён в state.json."
    )


def get_server_info() -> dict:
    try:
        import shutil
        import platform

        try:
            import psutil
        except ImportError:
            psutil = None

        disk = shutil.disk_usage(str(ROOT_DIR))

        result = {
            "platform": platform.platform(),
            "disk_total_gb": disk.total / 1024 / 1024 / 1024,
            "disk_used_gb": disk.used / 1024 / 1024 / 1024,
            "disk_free_gb": disk.free / 1024 / 1024 / 1024,
            "cpu_percent": None,
            "ram_total_gb": None,
            "ram_used_gb": None,
            "ram_percent": None,
            "available": True,
        }

        if psutil is not None:
            result["cpu_percent"] = psutil.cpu_percent(interval=0.3)

            memory = psutil.virtual_memory()
            result["ram_total_gb"] = memory.total / 1024 / 1024 / 1024
            result["ram_used_gb"] = memory.used / 1024 / 1024 / 1024
            result["ram_percent"] = memory.percent

        return result

    except Exception as error:
        return {"available": False, "error": str(error)}


def build_server_text() -> str:
    info = get_server_info()

    if not info.get("available", False):
        return (
            "🖥 <b>Сервер</b>\n\n"
            f"❌ Не удалось получить данные сервера.\n"
            f"<code>{info.get('error', 'unknown error')}</code>"
        )

    cpu_text = f"{info['cpu_percent']:.1f}%" if info.get("cpu_percent") is not None else "недоступно"

    if info.get("ram_percent") is not None:
        ram_text = (
            f"{info['ram_used_gb']:.2f} / "
            f"{info['ram_total_gb']:.2f} GB "
            f"({info['ram_percent']:.1f}%)"
        )
    else:
        ram_text = "недоступно, установи psutil"

    return (
        "🖥 <b>Сервер</b>\n\n"
        f"Система: <b>{info['platform']}</b>\n\n"
        f"CPU: <b>{cpu_text}</b>\n"
        f"RAM: <b>{ram_text}</b>\n\n"
        f"Диск всего: <b>{info['disk_total_gb']:.2f} GB</b>\n"
        f"Диск занято: <b>{info['disk_used_gb']:.2f} GB</b>\n"
        f"Диск свободно: <b>{info['disk_free_gb']:.2f} GB</b>"
    )


def build_robot_text() -> str:
    settings = load_settings()
    state = load_state()
    account_data = get_account_data()

    symbol = settings.get("symbol", "XAUUSD")
    dry_run = bool(settings.get("dry_run", True))
    mode = format_mode(dry_run=dry_run, account_data=account_data)

    min_score = settings.get("min_score", 75)
    max_score = settings.get("max_score", 90)
    risk_percent = settings.get("risk_percent", 1.0)

    soft_stop = settings.get("daily_soft_stop_percent", 3.0)
    hard_stop = settings.get("daily_hard_stop_percent", 4.0)

    last_m15 = format_short_time(state.get("last_m15_candle", "нет данных"))

    if account_data is None:
        account_text = "Счёт MT5: <b>недоступен</b>"
    else:
        account_text = (
            f"Счёт: <b>{account_data['login']}</b>\n"
            f"Сервер: <b>{account_data['server']}</b>\n"
            f"Торговля разрешена: <b>{format_bool(account_data['trade_allowed'])}</b>"
        )

    return (
        "⚙️ <b>Робот</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Последняя M15: <b>{last_m15}</b>\n\n"
        f"Риск на сделку: <b>{risk_percent}%</b>\n"
        f"Оценка для входа: <b>{min_score}–{max_score}</b>\n\n"
        f"Soft Stop дня: <b>{soft_stop}%</b>\n"
        f"Hard Stop дня: <b>{hard_stop}%</b>\n\n"
        f"{account_text}"
    )


async def build_text_async(kind: str) -> str:
    if kind == "status":
        return await asyncio.to_thread(build_status_text)
    if kind == "trade":
        return await asyncio.to_thread(build_trade_text)
    if kind == "stats":
        return await asyncio.to_thread(build_stats_text)
    if kind == "efficiency":
        return await asyncio.to_thread(build_efficiency_text)
    if kind == "last_signal":
        return await asyncio.to_thread(read_last_signal_from_log)
    if kind == "server":
        return await asyncio.to_thread(build_server_text)
    if kind == "robot":
        return await asyncio.to_thread(build_robot_text)

    return "Неизвестная команда."


async def safe_edit_message(callback: CallbackQuery, text: str):
    try:
        await callback.message.edit_text(text, reply_markup=main_keyboard())
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return
        raise


async def send_menu(message: Message):
    await message.answer(
        "🤖 <b>MT5 торговый бот</b>\n\nВыбери действие:",
        reply_markup=main_keyboard(),
    )


async def check_access_message(message: Message) -> bool:
    config = load_telegram_config()

    if not is_allowed(message.chat.id, config):
        await message.answer("⛔ Доступ запрещён.")
        return False

    return True


async def check_access_callback(callback: CallbackQuery) -> bool:
    config = load_telegram_config()

    if not is_allowed(callback.message.chat.id, config):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return False

    return True


async def cmd_start(message: Message):
    if not await check_access_message(message):
        return

    await send_menu(message)


async def cmd_status(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("status")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_trade(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("trade")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_stats(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("stats")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_efficiency(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("efficiency")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_signal(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("last_signal")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_robot(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("robot")
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_server(message: Message):
    if not await check_access_message(message):
        return

    text = await build_text_async("server")
    await message.answer(text, reply_markup=main_keyboard())


async def handle_callback(callback: CallbackQuery):
    if not await check_access_callback(callback):
        return

    await callback.answer()

    if callback.data not in BUTTONS:
        await safe_edit_message(callback, "Неизвестная команда.")
        return

    if _callback_lock.locked():
        await callback.answer("Подожди, предыдущий запрос еще обрабатывается.", show_alert=False)
        return

    async with _callback_lock:
        try:
            text = await build_text_async(callback.data)
            await safe_edit_message(callback, text)
        except Exception as error:
            error_text = (
                "❌ <b>Ошибка обработки кнопки.</b>\n\n"
                f"<code>{str(error)}</code>"
            )
            await callback.message.answer(error_text, reply_markup=main_keyboard())


async def main():
    config = load_telegram_config()
    settings = load_settings()

    if not config.get("enabled", False):
        print("Telegram bot disabled in config/telegram.json")
        return

    bot_token = config.get("bot_token", "")

    if not bot_token:
        print("Telegram bot token is empty")
        return

    symbol = settings.get("symbol", "XAUUSD")

    if not ensure_mt5_connection(symbol=symbol, quiet=True):
        print("MT5 connection failed or symbol check failed")
        return

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_trade, Command("trade"))
    dp.message.register(cmd_stats, Command("stats"))
    dp.message.register(cmd_efficiency, Command("efficiency"))
    dp.message.register(cmd_signal, Command("signal"))
    dp.message.register(cmd_robot, Command("robot"))
    dp.message.register(cmd_server, Command("server"))

    dp.callback_query.register(
        handle_callback,
        F.data.in_(BUTTONS),
    )

    print("Telegram bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
