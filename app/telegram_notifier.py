import json
from pathlib import Path

import requests


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "telegram.json"


def load_telegram_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
        }

    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def send_telegram_message(text: str) -> bool:
    cfg = load_telegram_config()

    if not cfg.get("enabled", False):
        return False

    bot_token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")

    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)

        if not response.ok:
            print("Telegram error:", response.text)
            return False

        return True

    except Exception as error:
        print("Telegram send failed:", error)
        return False


def format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}$"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def notify_open_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    volume: float,
    score: int,
    dry_run: bool,
):
    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    text = (
        "🟢 <b>Открыт сигнал</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Вход: <b>{entry_price}</b>\n"
        f"Стоп: <b>{stop_loss}</b>\n\n"
        f"TP1: <b>{tp1}</b>\n"
        f"TP2: <b>{tp2}</b>\n\n"
        f"Объем: <b>{volume}</b>"
    )

    send_telegram_message(text)


def notify_sl_moved(
    symbol: str,
    direction: str,
    new_sl: float,
    reason: str,
    dry_run: bool,
):
    mode = "DRY RUN" if dry_run else "DEMO / REAL"

    text = (
        "🛡 <b>Стоп перенесён</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction}</b>\n\n"
        f"Новый стоп: <b>{new_sl}</b>\n"
        f"Причина: <b>{reason}</b>"
    )

    send_telegram_message(text)


def notify_trade_closed(
    symbol: str,
    direction: str,
    reason: str,
    close_price: float,
    money_result: float,
    percent_result: float,
    daily_percent: float,
    dry_run: bool,
):
    mode = "DRY RUN" if dry_run else "DEMO / REAL"
    icon = "✅" if money_result >= 0 else "🔴"

    text = (
        f"{icon} <b>Сделка закрыта</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction}</b>\n\n"
        f"Цена закрытия: <b>{close_price}</b>\n"
        f"Причина: <b>{reason}</b>\n\n"
        f"Результат: <b>{format_money(money_result)}</b>\n"
        f"Результат: <b>{format_percent(percent_result)}</b>\n"
        f"Дневной результат: <b>{format_percent(daily_percent)}</b>"
    )

    send_telegram_message(text)


def notify_daily_guard(
    drawdown_percent: float,
    stop_type: str,
):
    text = (
        "⚠️ <b>Дневная защита</b>\n\n"
        f"Тип: <b>{stop_type}</b>\n"
        f"Дневная просадка: <b>{drawdown_percent:.2f}%</b>"
    )

    send_telegram_message(text)


def notify_error(error_text: str):
    text = (
        "❌ <b>Ошибка торгового бота</b>\n\n"
        f"<code>{error_text}</code>"
    )

    send_telegram_message(text)