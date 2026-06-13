import asyncio
import json
from pathlib import Path
from aiogram.client.default import DefaultBotProperties
import MetaTrader5 as mt5
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.mt5_client import connect_mt5, ensure_symbol
from app.state_manager import load_state
from app.telegram_notifier import load_telegram_config


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"


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
    kb.button(text="💰 Баланс", callback_data="balance")

    kb.adjust(1)

    return kb.as_markup()


def format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}$"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def get_account_data():
    account = mt5.account_info()

    if account is None:
        return None

    return {
        "balance": float(account.balance),
        "equity": float(account.equity),
        "profit": float(account.profit),
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

    trade_status = "да" if active_trade else "нет"

    return (
        "📊 <b>Статус бота</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n\n"
        f"Баланс: <b>{account_data['balance']:.2f}$</b>\n"
        f"Средства: <b>{account_data['equity']:.2f}$</b>\n"
        f"Текущий результат: <b>{format_money(account_data['profit'])}</b>\n\n"
        f"Дневной результат: <b>{format_percent(daily_info['daily_result_percent'])}</b>\n"
        f"Дневная просадка: <b>{daily_info['drawdown_percent']:.2f}%</b>\n\n"
        f"Открытая сделка: <b>{trade_status}</b>"
    )


def build_balance_text() -> str:
    state = load_state()
    account_data = get_account_data()

    if account_data is None:
        return "❌ Не удалось получить данные счёта MT5."

    daily_info = get_daily_info(state, account_data)

    return (
        "💰 <b>Баланс</b>\n\n"
        f"Баланс: <b>{account_data['balance']:.2f}$</b>\n"
        f"Средства: <b>{account_data['equity']:.2f}$</b>\n"
        f"Плавающий результат: <b>{format_money(account_data['profit'])}</b>\n\n"
        f"Старт дня: <b>{daily_info['start_balance']:.2f}$</b>\n"
        f"Дневной результат: <b>{format_percent(daily_info['daily_result_percent'])}</b>\n"
        f"Дневная просадка: <b>{daily_info['drawdown_percent']:.2f}%</b>"
    )


def build_trade_text() -> str:
    state = load_state()
    trade = state.get("active_trade")
    account_data = get_account_data()

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

    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    current_profit_text = "недоступно"

    if account_data is not None:
        current_profit_text = format_money(account_data["profit"])

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
        f"Текущий результат: <b>{current_profit_text}</b>"
    )


async def send_menu(message: Message):
    await message.answer(
        "🤖 <b>MT5 XAU BOT</b>\n\nВыбери действие:",
        reply_markup=main_keyboard(),
    )


async def cmd_start(message: Message):
    config = load_telegram_config()

    if not is_allowed(message.chat.id, config):
        await message.answer("⛔ Доступ запрещён.")
        return

    await send_menu(message)


async def cmd_status(message: Message):
    config = load_telegram_config()

    if not is_allowed(message.chat.id, config):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(build_status_text(), reply_markup=main_keyboard())


async def cmd_trade(message: Message):
    config = load_telegram_config()

    if not is_allowed(message.chat.id, config):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(build_trade_text(), reply_markup=main_keyboard())


async def cmd_balance(message: Message):
    config = load_telegram_config()

    if not is_allowed(message.chat.id, config):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(build_balance_text(), reply_markup=main_keyboard())


async def handle_callback(callback: CallbackQuery):
    config = load_telegram_config()

    if not is_allowed(callback.message.chat.id, config):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    if callback.data == "status":
        text = build_status_text()
    elif callback.data == "trade":
        text = build_trade_text()
    elif callback.data == "balance":
        text = build_balance_text()
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
    dp.message.register(cmd_balance, Command("balance"))
    dp.callback_query.register(handle_callback, F.data.in_({"status", "trade", "balance"}))

    print("Telegram bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())