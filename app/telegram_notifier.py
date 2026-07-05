import html
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from app.text_ru import (
    translate_direction,
    translate_mode,
    translate_stop_type,
)


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


def telegram_notifications_enabled() -> bool:
    cfg = load_telegram_config()
    return bool(
        cfg.get("enabled", False)
        and cfg.get("bot_token")
        and cfg.get("chat_id")
    )


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
            print("Ошибка Telegram:", response.text)
            return False

        return True
    except Exception as error:
        print("Не удалось отправить сообщение в Telegram:", error)
        return False


def format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{float(value):.2f}$"


def format_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{float(value):.2f}%"


def format_price(value: float) -> str:
    return f"{float(value):.2f}"


def format_mode(server: Optional[str] = None) -> str:
    return translate_mode(server)


def format_direction(direction: str) -> str:
    translated = translate_direction(direction)
    if str(direction).upper() == "BUY":
        return f"🔺 {translated}"
    if str(direction).upper() == "SELL":
        return f"🔻 {translated}"
    return html.escape(translated)


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
    tp3: Optional[float] = None,
    server: Optional[str] = None,
) -> bool:
    mode = format_mode(server=server)
    direction_text = format_direction(direction)
    tp3_text = f"❌ Цель 3: <b>{format_price(tp3)}</b>\n" if tp3 is not None else ""

    text = (
        "📈 <b>Сделка открыта</b>\n\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n"
        f"Оценка: <b>{int(score)}</b>\n\n"
        f"Цена открытия: <b>{format_price(entry_price)}</b>\n"
        f"Стоп-лосс: <b>{format_price(stop_loss)}</b>\n\n"
        f"Цель 1: <b>{format_price(tp1)}</b>\n"
        f"Цель 2: <b>{format_price(tp2)}</b>\n"
        f"{tp3_text}"
        f"Объём: <b>{float(volume):.2f}</b>"
    )
    return send_telegram_message(text)


def notify_sl_moved(
    symbol: str,
    direction: str,
    new_sl: float,
    reason: str,
    server: Optional[str] = None,
) -> bool:
    mode = format_mode(server=server)
    direction_text = format_direction(direction)
    reason_text = html.escape(str(reason))

    text = (
        "🛡 <b>Стоп-лосс перенесён</b>\n\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n\n"
        f"Новый Стоп-лосс: <b>{format_price(new_sl)}</b>\n"
        f"Причина: <b>{reason_text}</b>"
    )
    return send_telegram_message(text)


def notify_trade_closed(
    symbol: str,
    direction: str,
    reason: str,
    close_price: float,
    gross_profit: float,
    commission: float,
    swap: float,
    fee: float,
    net_profit: float,
    percent_result: float,
    balance: float,
    daily_money: float,
    daily_percent: float,
    close_time: str,
    server: Optional[str] = None,
) -> bool:
    mode = format_mode(server=server)
    direction_text = format_direction(direction)
    icon = format_result_icon(float(net_profit))
    daily_icon = format_result_icon(float(daily_money))

    text = (
        f"{icon} <b>Сделка закрыта</b>\n\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>\n"
        f"Режим: <b>{mode}</b>\n"
        f"Направление: <b>{direction_text}</b>\n"
        f"Причина: <b>{html.escape(reason)}</b>\n"
        f"Цена закрытия: <b>{format_price(close_price)}</b>\n"
        f"Время закрытия: <b>{html.escape(close_time)}</b>\n\n"
        f"Торговый результат: <b>{format_money(gross_profit)}</b>\n"
        f"Комиссия: <b>{format_money(commission)}</b>\n"
        f"Своп: <b>{format_money(swap)}</b>\n"
        f"Сбор: <b>{format_money(fee)}</b>\n"
        f"Итог: <b>{icon} {format_money(net_profit)} / {format_percent(percent_result)}</b>\n\n"
        f"Баланс: <b>{float(balance):.2f}$</b>\n"
        f"Дневной результат: <b>{daily_icon} {format_money(daily_money)} / "
        f"{format_percent(daily_percent)}</b>"
    )
    return send_telegram_message(text)


def notify_daily_guard(drawdown_percent: float, stop_type: str) -> bool:
    text = (
        "⚠️ <b>Дневная защита</b>\n\n"
        f"Тип: <b>{html.escape(translate_stop_type(stop_type))}</b>\n"
        f"Дневная просадка: <b>{drawdown_percent:.2f}%</b>"
    )
    return send_telegram_message(text)


def notify_error(error_text: str) -> bool:
    text = (
        "❌ <b>Ошибка торгового бота</b>\n\n"
        f"<code>{html.escape(error_text)}</code>"
    )
    return send_telegram_message(text)


def notify_account_guard(
    drawdown_percent: float,
    max_drawdown_percent: float,
    blocked_until: str | None,
) -> bool:
    until_text = blocked_until or "неизвестно"
    text = (
        "🛑 <b>ЗАЩИТА СЧЁТА</b>\n\n"
        "Общая просадка счёта достигла лимита.\n\n"
        f"Просадка: <b>{drawdown_percent:.2f}%</b>\n"
        f"Лимит: <b>{max_drawdown_percent:.2f}%</b>\n\n"
        "Торговля остановлена.\n"
        f"Блокировка до: <b>{html.escape(until_text)}</b>"
    )
    return send_telegram_message(text)


def notify_robot_started(
    symbol: str,
    instance_id: str,
    pid: int | None,
    server: str | None,
) -> bool:
    text = (
        "✅ <b>Робот запущен</b>\n\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>\n"
        f"Режим: <b>{format_mode(server)}</b>\n"
        f"PID: <b>{pid if pid is not None else '—'}</b>\n"
        f"Экземпляр: <code>{html.escape(instance_id[:12])}</code>"
    )
    return send_telegram_message(text)


def notify_watchdog_failure(
    title: str,
    reason: str,
    last_activity: str | None,
    last_success: str | None,
    symbol: str,
    details: str | None = None,
) -> bool:
    details_text = ""
    if details:
        details_text = f"\nДетали: <code>{html.escape(details[:1200])}</code>"

    text = (
        f"🔴 <b>{html.escape(title)}</b>\n\n"
        f"Причина: <b>{html.escape(reason)}</b>\n"
        f"Последняя активность: <b>{html.escape(last_activity or 'нет данных')}</b>\n"
        f"Последний успешный цикл: <b>{html.escape(last_success or 'нет данных')}</b>\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>"
        f"{details_text}"
    )
    return send_telegram_message(text)


def notify_watchdog_recovery(
    symbol: str,
    recovered_at: str,
    downtime_text: str,
    previous_reason: str,
) -> bool:
    text = (
        "🟢 <b>Работа робота восстановлена</b>\n\n"
        "Торговый цикл снова выполняется нормально.\n\n"
        f"Время восстановления: <b>{html.escape(recovered_at)}</b>\n"
        f"Время простоя: <b>{html.escape(downtime_text)}</b>\n"
        f"Предыдущая проблема: <b>{html.escape(previous_reason)}</b>\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>"
    )
    return send_telegram_message(text)


def notify_state_warning(title: str, message: str) -> bool:
    text = (
        f"⚠️ <b>{html.escape(title)}</b>\n\n"
        f"{html.escape(message)}"
    )
    return send_telegram_message(text)


def notify_telegram_polling_failure(
    reason: str,
    last_heartbeat: str | None,
    last_error: str | None,
    symbol: str,
) -> bool:
    error_text = ""
    if last_error:
        error_text = f"\nПоследняя ошибка: <code>{html.escape(last_error[:1200])}</code>"

    text = (
        "🟠 <b>Telegram-бот не отвечает</b>\n\n"
        "Торговый цикл продолжает контролироваться отдельно.\n\n"
        f"Причина: <b>{html.escape(reason)}</b>\n"
        f"Последняя проверка Telegram: "
        f"<b>{html.escape(last_heartbeat or 'нет данных')}</b>\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>"
        f"{error_text}"
    )
    return send_telegram_message(text)


def notify_telegram_polling_recovery(
    symbol: str,
    recovered_at: str,
    downtime_text: str,
    previous_reason: str,
) -> bool:
    text = (
        "🟢 <b>Telegram-бот восстановлен</b>\n\n"
        "Получение команд снова работает, кнопки и команды доступны.\n\n"
        f"Время восстановления: <b>{html.escape(recovered_at)}</b>\n"
        f"Время сбоя: <b>{html.escape(downtime_text)}</b>\n"
        f"Предыдущая проблема: <b>{html.escape(previous_reason)}</b>\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>"
    )
    return send_telegram_message(text)


def notify_state_recovery(
    symbol: str,
    message: str,
    previous_reason: str,
    recovered_at: str,
) -> bool:
    text = (
        "🟢 <b>Состояние робота восстановлено</b>\n\n"
        f"{html.escape(message)}\n\n"
        f"Предыдущая блокировка: <b>{html.escape(previous_reason)}</b>\n"
        f"Инструмент: <b>{html.escape(symbol)}</b>\n"
        f"Время: <b>{html.escape(recovered_at)}</b>"
    )
    return send_telegram_message(text)
