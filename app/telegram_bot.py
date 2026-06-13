import asyncio
import json
from pathlib import Path

import MetaTrader5 as mt5
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.mt5_client import connect_mt5, ensure_symbol
from app.state_manager import load_state
from app.telegram_notifier import load_telegram_config


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"
EVENTS_LOG_FILE = ROOT_DIR / "logs" / "events.log"


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
    kb.button(text="📜 Последний сигнал", callback_data="last_signal")
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


def get_account_data():
    account = mt5.account_info()

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
    else:
        daily_result_percent = ((equity - start_balance) / start_balance) * 100

    drawdown_percent = float(daily_guard.get("drawdown_percent", 0.0))

    return {
        "start_balance": start_balance,
        "daily_result_percent": daily_result_percent,
        "drawdown_percent": drawdown_percent,
        "trading_blocked": bool(daily_guard.get("trading_blocked", False)),
        "hard_stop_triggered": bool(daily_guard.get("hard_stop_triggered", False)),
    }


def calculate_trade_result(trade: dict) -> dict:
    symbol = trade.get("symbol", "XAUUSD")
    direction = trade.get("direction", "")
    entry_price = float(trade.get("entry_price", 0))
    volume = float(trade.get("volume", 0))

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)

    if tick is None or info is None or entry_price <= 0 or volume <= 0:
        return {
            "price": 0.0,
            "money": 0.0,
            "percent": 0.0,
            "available": False,
        }

    if direction == "BUY":
        current_price = float(tick.bid)
        price_diff = current_price - entry_price
    elif direction == "SELL":
        current_price = float(tick.ask)
        price_diff = entry_price - current_price
    else:
        return {
            "price": 0.0,
            "money": 0.0,
            "percent": 0.0,
            "available": False,
        }

    tick_size = float(info.trade_tick_size)
    tick_value = float(info.trade_tick_value)

    if tick_size <= 0 or tick_value <= 0:
        return {
            "price": current_price,
            "money": 0.0,
            "percent": 0.0,
            "available": False,
        }

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


def build_status_text() -> str:
    settings = load_settings()
    state = load_state()
    account_data = get_account_data()

    symbol = settings.get("symbol", "XAUUSD")
    dry_run = bool(settings.get("dry_run", True))
    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    if account_data is None:
        return "❌ Не удалось получить данные счёта MT5."

    daily_info = get_daily_info(state, account_data)
    active_trade = state.get("active_trade")

    trade_status = "есть" if active_trade else "нет"
    last_m15 = state.get("last_m15_candle", "нет данных")

    return (
        "📊 <b>Статус бота</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Последняя M15: <b>{last_m15}</b>\n\n"
        f"Баланс: <b>{account_data['balance']:.2f}$</b>\n"
        f"Средства: <b>{account_data['equity']:.2f}$</b>\n"
        f"Плавающий результат: <b>{format_money(account_data['profit'])}</b>\n\n"
        f"Дневной результат: <b>{format_percent(daily_info['daily_result_percent'])}</b>\n"
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
    direction = trade.get("direction", "")
    entry_price = float(trade.get("entry_price", 0))
    stop_loss = float(trade.get("stop_loss", 0))
    tp1 = float(trade.get("tp1", 0))
    tp2 = float(trade.get("tp2", 0))
    volume = float(trade.get("volume", 0))
    score = trade.get("score", "")
    dry_run = bool(trade.get("dry_run", True))

    tp1_hit = bool(trade.get("tp1_hit", False))
    tp2_hit = bool(trade.get("tp2_hit", False))
    tp3_hit = bool(trade.get("tp3_hit", False))
    breakeven_active = bool(trade.get("breakeven_active", False))
    candles_in_trade = int(trade.get("candles_in_trade", 0))

    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    result = calculate_trade_result(trade)

    if result["available"]:
        result_text = (
            f"Текущая цена: <b>{result['price']:.2f}</b>\n"
            f"Результат: <b>{format_money(result['money'])}</b>\n"
            f"Результат: <b>{format_percent(result['percent'])}</b>"
        )
    else:
        result_text = "Текущий результат: <b>недоступно</b>"

    return (
        "💼 <b>Открытая сделка</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Вход: <b>{entry_price}</b>\n"
        f"Стоп: <b>{stop_loss}</b>\n"
        f"TP1: <b>{tp1}</b>\n"
        f"TP2: <b>{tp2}</b>\n\n"
        f"Объем: <b>{volume}</b>\n"
        f"Свечей в сделке: <b>{candles_in_trade}</b>\n\n"
        f"TP1 выполнен: <b>{format_bool(tp1_hit)}</b>\n"
        f"TP2 выполнен: <b>{format_bool(tp2_hit)}</b>\n"
        f"TP3 выполнен: <b>{format_bool(tp3_hit)}</b>\n"
        f"BE+ активен: <b>{format_bool(breakeven_active)}</b>\n\n"
        f"{result_text}"
    )


def read_last_signal_from_log() -> str:
    if not EVENTS_LOG_FILE.exists():
        return "📜 <b>Последний сигнал</b>\n\nЛог сигналов пока не найден."

    with open(EVENTS_LOG_FILE, "r", encoding="utf-8") as file:
        lines = file.readlines()

    signal_action = None
    signal_score = None
    signal_reasons = None

    for line in reversed(lines):
        if signal_reasons is None and "Signal reasons:" in line:
            signal_reasons = line.split("Signal reasons:", 1)[1].strip()

        if signal_score is None and "Signal score:" in line:
            signal_score = line.split("Signal score:", 1)[1].strip()

        if signal_action is None and "Signal action:" in line:
            signal_action = line.split("Signal action:", 1)[1].strip()

        if signal_action and signal_score and signal_reasons:
            break

    if not signal_action:
        return "📜 <b>Последний сигнал</b>\n\nСигналов пока не было."

    return (
        "📜 <b>Последний сигнал</b>\n\n"
        f"Решение: <b>{signal_action}</b>\n"
        f"Оценка: <b>{signal_score}</b>\n\n"
        f"Причины:\n<code>{signal_reasons}</code>"
    )


def build_robot_text() -> str:
    settings = load_settings()
    state = load_state()
    account_data = get_account_data()

    symbol = settings.get("symbol", "XAUUSD")
    dry_run = bool(settings.get("dry_run", True))
    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    min_score = settings.get("min_score", 75)
    max_score = settings.get("max_score", 90)
    risk_percent = settings.get("risk_percent", 1.0)

    soft_stop = settings.get("daily_soft_stop_percent", 3.0)
    hard_stop = settings.get("daily_hard_stop_percent", 4.0)

    last_m15 = state.get("last_m15_candle", "нет данных")

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

    await message.answer(build_status_text(), reply_markup=main_keyboard())


async def cmd_trade(message: Message):
    if not await check_access_message(message):
        return

    await message.answer(build_trade_text(), reply_markup=main_keyboard())


async def cmd_signal(message: Message):
    if not await check_access_message(message):
        return

    await message.answer(read_last_signal_from_log(), reply_markup=main_keyboard())


async def cmd_robot(message: Message):
    if not await check_access_message(message):
        return

    await message.answer(build_robot_text(), reply_markup=main_keyboard())


async def handle_callback(callback: CallbackQuery):
    if not await check_access_callback(callback):
        return

    if callback.data == "status":
        text = build_status_text()
    elif callback.data == "trade":
        text = build_trade_text()
    elif callback.data == "last_signal":
        text = read_last_signal_from_log()
    elif callback.data == "robot":
        text = build_robot_text()
    else:
        text = "Неизвестная команда."

    await callback.message.edit_text(text, reply_markup=main_keyboard())
    await callback.answer()


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

    if not connect_mt5():
        print("MT5 connection failed")
        return

    if not ensure_symbol(symbol):
        print(f"Symbol check failed: {symbol}")
        return

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    dp = Dispatcher()

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_trade, Command("trade"))
    dp.message.register(cmd_signal, Command("signal"))
    dp.message.register(cmd_robot, Command("robot"))

    dp.callback_query.register(
        handle_callback,
        F.data.in_({"status", "trade", "last_signal", "robot"}),
    )

    print("Telegram bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())