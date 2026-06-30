import json
from pathlib import Path
from typing import Optional

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
    return f"{sign}{float(value):.2f}$"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{float(value):.2f}%"


def format_price(value: float) -> str:
    return f"{float(value):.2f}"


def format_mode(dry_run: bool, server: Optional[str] = None) -> str:
    if dry_run:
        return "🟣 DRY RUN"

    server_text = (server or "").lower()

    if "demo" in server_text:
        return "🟡 DEMO"

    return "🟢 REAL"


def format_direction(direction: str) -> str:
    direction = str(direction).upper()

    if direction == "BUY":
        return "🔺 BUY"

    if direction == "SELL":
        return "🔻 SELL"

    return direction


def format_result_icon(value: float) -> str:
    if value > 0:
        return "🟢"

    if value < 0:
        return "🔴"

    return "⚪"


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
    tp3: Optional[float] = None,
    server: Optional[str] = None,
):
    mode = format_mode(dry_run=dry_run, server=server)
    direction_text = format_direction(direction)

    tp3_text = ""
    if tp3 is not None:
        tp3_text = f"❌ TP3: <b>{format_price(tp3)}</b>\n"

    text = (
        "🟢 <b>Открыт сигнал</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n"
        f"Оценка: <b>{score}</b>\n\n"
        f"Вход: <b>{format_price(entry_price)}</b>\n"
        f"Стоп: <b>{format_price(stop_loss)}</b>\n\n"
        f"❌ TP1: <b>{format_price(tp1)}</b>\n"
        f"❌ TP2: <b>{format_price(tp2)}</b>\n"
        f"{tp3_text}\n"
        f"Объём: <b>{volume}</b>"
    )

    send_telegram_message(text)


def notify_sl_moved(
    symbol: str,
    direction: str,
    new_sl: float,
    reason: str,
    dry_run: bool,
    server: Optional[str] = None,
):
    mode = format_mode(dry_run=dry_run, server=server)
    direction_text = format_direction(direction)

    reason_text = str(reason).replace("TP1", "✅ TP1").replace("TP2", "✅ TP2").replace("TP3", "✅ TP3")

    text = (
        "🛡 <b>Стоп перенесён</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n\n"
        f"Новый стоп: <b>{format_price(new_sl)}</b>\n"
        f"Причина: <b>{reason_text}</b>"
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
    daily_money: Optional[float] = None,
    server: Optional[str] = None,
):
    mode = format_mode(dry_run=dry_run, server=server)
    direction_text = format_direction(direction)
    icon = format_result_icon(float(money_result))
    daily_icon = format_result_icon(float(daily_percent))

    if daily_money is None:
        daily_text = f"{daily_icon} {format_percent(daily_percent)}"
    else:
        daily_text = f"{daily_icon} {format_money(daily_money)} / {format_percent(daily_percent)}"

    text = (
        f"{icon} <b>Сделка закрыта</b>\n\n"
        f"Инструмент: <b>{symbol}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n\n"
        f"Цена закрытия: <b>{format_price(close_price)}</b>\n"
        f"Причина: <b>{reason}</b>\n\n"
        f"Результат: <b>{icon} {format_money(money_result)} / {format_percent(percent_result)}</b>\n"
        f"Дневной результат: <b>{daily_text}</b>"
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

def notify_account_guard(
    drawdown_percent: float,
    max_drawdown_percent: float,
    blocked_until: str | None,
):
    until_text = blocked_until or "неизвестно"

    text = (
        "🛑 <b>ACCOUNT GUARD</b>\n\n"
        "Общая просадка счёта достигла лимита.\n\n"
        f"Просадка: <b>{drawdown_percent:.2f}%</b>\n"
        f"Лимит: <b>{max_drawdown_percent:.2f}%</b>\n\n"
        "Торговля остановлена.\n"
        f"Блокировка до: <b>{until_text}</b>"
    )

    send_telegram_message(text)
