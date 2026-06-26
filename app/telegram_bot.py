import asyncio
import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
)

from app.state_manager import load_state
from app.telegram_notifier import load_telegram_config


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"
LOGS_DIR = ROOT_DIR / "logs"

_callback_locks: dict[str, asyncio.Lock] = {}


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


def format_time_short(value) -> str:
    if not value:
        return "нет данных"

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        text = str(value)
        if " " in text and ":" in text:
            try:
                return text.split(" ", 1)[1][:5]
            except Exception:
                return text
        return text


def direction_icon(direction: str) -> str:
    direction = str(direction).upper()
    if direction == "BUY":
        return "🔺 BUY"
    if direction == "SELL":
        return "🔻 SELL"
    return direction


def result_icon(value: float) -> str:
    if value > 0:
        return "🟢"
    if value < 0:
        return "🔴"
    return "⚪"


def tp_icon(value: bool) -> str:
    return "✅" if value else "❌"


def detect_mode(settings: dict, account_data: dict | None = None) -> str:
    dry_run = bool(settings.get("dry_run", True))

    if dry_run:
        return "⚪ DRY RUN"

    server = ""
    if account_data:
        server = str(account_data.get("server", ""))

    if "demo" in server.lower():
        return "🟡 DEMO"

    return "🟢 REAL"


def parse_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def parse_trade_time(value: str):
    if not value:
        return None

    text = str(value).strip()

    # Поддержка формата MT5: 2026.06.26 07:54:45
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_account_data():
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

    if start_balance <= 0:
        daily_result_percent = 0.0
        daily_result_money = 0.0
    else:
        daily_result_money = equity - start_balance
        daily_result_percent = (daily_result_money / start_balance) * 100

    drawdown_percent = float(daily_guard.get("drawdown_percent", 0.0))

    return {
        "start_balance": start_balance,
        "daily_result_money": daily_result_money,
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

    return {
        "price": current_price,
        "money": money,
        "percent": percent,
        "available": True,
    }


def get_latest_trades_file():
    if not LOGS_DIR.exists():
        return None

    files = sorted(
        LOGS_DIR.glob("trades_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    return files[0] if files else None


def load_trades_from_csv() -> list[dict]:
    file_path = get_latest_trades_file()

    if file_path is None:
        return []

    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))
    except Exception:
        return []


def get_trade_close_time(trade: dict):
    for key in ("close_time", "time", "close_datetime", "datetime", "date"):
        value = trade.get(key)
        parsed = parse_trade_time(value)
        if parsed is not None:
            return parsed
    return None


def get_trade_money_result(trade: dict) -> float:
    # Поддерживаем несколько возможных названий колонок.
    for key in (
        "money_result",
        "profit",
        "profit_money",
        "result_money",
        "pnl",
        "p_l",
        "P/L",
    ):
        if key in trade and trade.get(key) not in (None, ""):
            return parse_float(trade.get(key))
    return 0.0


def get_trade_percent_result(trade: dict) -> float:
    for key in (
        "percent_result",
        "result_percent",
        "profit_percent",
        "change",
        "Change",
    ):
        if key in trade and trade.get(key) not in (None, ""):
            return parse_float(str(trade.get(key)).replace("%", ""))
    return 0.0


def build_period_stats(trades: list[dict], start_time: datetime) -> dict:
    selected = []

    for trade in trades:
        close_time = get_trade_close_time(trade)
        if close_time is None:
            continue

        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=timezone.utc)

        if close_time >= start_time:
            selected.append(trade)

    total = len(selected)
    wins = 0
    losses = 0
    breakevens = 0
    money = 0.0
    percent = 0.0

    for trade in selected:
        result_money = get_trade_money_result(trade)
        result_percent = get_trade_percent_result(trade)

        money += result_money
        percent += result_percent

        if result_money > 0:
            wins += 1
        elif result_money < 0:
            losses += 1
        else:
            breakevens += 1

    winrate = (wins / total * 100) if total > 0 else 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "money": money,
        "percent": percent,
        "winrate": winrate,
    }


def format_stats_block(title: str, stats: dict) -> str:
    icon = result_icon(stats["money"])

    return (
        f"{title}\n\n"
        f"Сделок: <b>{stats['total']}</b>\n"
        f"🟢 Прибыльных: <b>{stats['wins']}</b>\n"
        f"🔴 Убыточных: <b>{stats['losses']}</b>\n"
        f"⚪ Безубыток: <b>{stats['breakevens']}</b>\n"
        f"Winrate: <b>{stats['winrate']:.2f}%</b>\n"
        f"{icon} P/L: <b>{format_money(stats['money'])} / {format_percent(stats['percent'])}</b>"
    )


def build_stats_text() -> str:
    trades = load_trades_from_csv()
    file_path = get_latest_trades_file()

    if not trades:
        return "📈 <b>Статистика</b>\n\nИстория сделок пока не найдена."

    now = datetime.now(timezone.utc)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    today_stats = build_period_stats(trades, today_start)
    week_stats = build_period_stats(trades, week_start)
    month_stats = build_period_stats(trades, month_start)

    file_name = file_path.name if file_path is not None else "не найден"

    return (
        "📈 <b>Статистика торговли</b>\n"
        f"Файл: <code>{file_name}</code>\n\n"
        f"{format_stats_block('📅 Сегодня', today_stats)}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{format_stats_block('📆 За 7 дней', week_stats)}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{format_stats_block('🗓 За 30 дней', month_stats)}"
    )


def build_status_text() -> str:
    settings = load_settings()
    state = load_state()
    account_data = get_account_data()

    symbol = settings.get("symbol", "XAUUSD")

    if account_data is None:
        return "❌ Не удалось получить данные счёта MT5."

    mode = detect_mode(settings, account_data)
    daily_info = get_daily_info(state, account_data)
    active_trade = state.get("active_trade")

    trade_status = "есть" if active_trade else "нет"
    last_m15 = format_time_short(state.get("last_m15_candle", "нет данных"))

    daily_icon = result_icon(daily_info["daily_result_money"])

    return (
        "📊 <b>Статус бота</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Последняя M15: <b>{last_m15}</b>\n\n"
        f"Баланс: <b>{account_data['balance']:.2f}$</b>\n"
        f"Средства: <b>{account_data['equity']:.2f}$</b>\n"
        f"Результат открытой сделки: <b>{format_money(account_data['profit'])}</b>\n\n"
        f"{daily_icon} Дневной результат: "
        f"<b>{format_money(daily_info['daily_result_money'])} / "
        f"{format_percent(daily_info['daily_result_percent'])}</b>\n"
        f"Дневная просадка: <b>{daily_info['drawdown_percent']:.2f}%</b>\n\n"
        f"Открытая сделка: <b>{trade_status}</b>\n"
        f"Новые сделки запрещены: <b>{format_bool(daily_info['trading_blocked'])}</b>"
    )


def build_trade_text() -> str:
    state = load_state()
    trade = state.get("active_trade")

    if trade is None:
        return "💼 <b>Открытая сделка</b>\n\nСейчас открытой сделки нет."

    settings = load_settings()
    account_data = get_account_data()

    symbol = trade.get("symbol", "XAUUSD")
    direction = trade.get("direction", "")
    entry_price = float(trade.get("entry_price", 0))
    stop_loss = float(trade.get("stop_loss", 0))
    tp1 = float(trade.get("tp1", 0))
    tp2 = float(trade.get("tp2", 0))
    tp3 = float(trade.get("tp3", 0))
    volume = float(trade.get("volume", 0))
    score = trade.get("score", "")

    tp1_hit = bool(trade.get("tp1_hit", False))
    tp2_hit = bool(trade.get("tp2_hit", False))
    tp3_hit = bool(trade.get("tp3_hit", False))
    breakeven_active = bool(trade.get("breakeven_active", False))
    candles_in_trade = int(trade.get("candles_in_trade", 0))

    mode = detect_mode(settings, account_data)
    result = calculate_trade_result(trade)

    if result["available"]:
        icon = result_icon(result["money"])
        result_text = (
            f"Текущая цена: <b>{result['price']:.2f}</b>\n"
            f"{icon} Результат: <b>{format_money(result['money'])} / "
            f"{format_percent(result['percent'])}</b>"
        )
    else:
        result_text = "Текущий результат: <b>недоступно</b>"

    return (
        "💼 <b>Открытая сделка</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_icon(direction)}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Вход: <b>{entry_price:.2f}</b>\n"
        f"Стоп: <b>{stop_loss:.2f}</b>\n\n"
        f"{tp_icon(tp1_hit)} TP1: <b>{tp1:.2f}</b>\n"
        f"{tp_icon(tp2_hit)} TP2: <b>{tp2:.2f}</b>\n"
        f"{tp_icon(tp3_hit)} TP3: <b>{tp3:.2f}</b>\n\n"
        f"Объём: <b>{volume}</b>\n"
        f"Свечей в сделке: <b>{candles_in_trade}</b>\n"
        f"BE+ активен: <b>{format_bool(breakeven_active)}</b>\n\n"
        f"{result_text}"
    )


def read_last_signal_from_state() -> str | None:
    state = load_state()
    last_signal = state.get("last_signal")

    if not last_signal:
        return None

    action = last_signal.get("action", "нет данных")
    score = last_signal.get("score", "нет данных")
    reasons = last_signal.get("reasons", [])
    time_value = format_time_short(last_signal.get("time", "нет данных"))

    if isinstance(reasons, list):
        reasons_text = "\n".join([f"• {reason}" for reason in reasons])
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

    cpu_text = (
        f"{info['cpu_percent']:.1f}%"
        if info.get("cpu_percent") is not None
        else "недоступно"
    )

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
    mode = detect_mode(settings, account_data)

    min_score = settings.get("min_score", 75)
    max_score = settings.get("max_score", 90)
    risk_percent = settings.get("risk_percent", 1.0)

    soft_stop = settings.get("daily_soft_stop_percent", 3.0)
    hard_stop = settings.get("daily_hard_stop_percent", 4.0)

    last_m15 = format_time_short(state.get("last_m15_candle", "нет данных"))

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

    if callback.message is None:
        await callback.answer("Ошибка сообщения.", show_alert=True)
        return False

    if not is_allowed(callback.message.chat.id, config):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return False

    return True


async def safe_edit_message(callback: CallbackQuery, text: str):
    try:
        await callback.message.edit_text(text, reply_markup=main_keyboard())
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if "message is not modified" in error_text:
            return
        raise


async def cmd_start(message: Message):
    if not await check_access_message(message):
        return

    await send_menu(message)


async def cmd_status(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(build_status_text)
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_trade(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(build_trade_text)
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_stats(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(build_stats_text)
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_signal(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(read_last_signal_from_log)
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_robot(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(build_robot_text)
    await message.answer(text, reply_markup=main_keyboard())


async def cmd_server(message: Message):
    if not await check_access_message(message):
        return

    text = await asyncio.to_thread(build_server_text)
    await message.answer(text, reply_markup=main_keyboard())


async def handle_callback(callback: CallbackQuery):
    if not await check_access_callback(callback):
        return

    await callback.answer()

    chat_id = str(callback.message.chat.id)
    lock = _callback_locks.setdefault(chat_id, asyncio.Lock())

    if lock.locked():
        return

    async with lock:
        try:
            if callback.data == "status":
                text = await asyncio.to_thread(build_status_text)
            elif callback.data == "trade":
                text = await asyncio.to_thread(build_trade_text)
            elif callback.data == "stats":
                text = await asyncio.to_thread(build_stats_text)
            elif callback.data == "last_signal":
                text = await asyncio.to_thread(read_last_signal_from_log)
            elif callback.data == "server":
                text = await asyncio.to_thread(build_server_text)
            elif callback.data == "robot":
                text = await asyncio.to_thread(build_robot_text)
            else:
                text = "Неизвестная команда."

            await safe_edit_message(callback, text)

        except Exception as error:
            error_text = (
                "❌ <b>Ошибка обработки кнопки.</b>\n\n"
                f"<code>{error}</code>"
            )
            try:
                await safe_edit_message(callback, error_text)
            except Exception:
                pass


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
    dp.message.register(cmd_signal, Command("signal"))
    dp.message.register(cmd_robot, Command("robot"))
    dp.message.register(cmd_server, Command("server"))

    dp.callback_query.register(
        handle_callback,
        F.data.in_({"status", "trade", "stats", "last_signal", "server", "robot"}),
    )

    print("Telegram bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
