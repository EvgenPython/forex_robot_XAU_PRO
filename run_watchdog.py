import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.health_manager import load_heartbeat
from app.logger import log_event
from app.monitoring_config import load_monitoring_config
from app.process_lock import ProcessAlreadyRunningError, ProcessLock
from app.telegram_notifier import (
    notify_robot_started,
    notify_telegram_polling_failure,
    notify_telegram_polling_recovery,
    notify_watchdog_failure,
    notify_watchdog_recovery,
)
from app.watchdog_state_manager import load_watchdog_state, save_watchdog_state


DISPLAY_TIMEZONE = ZoneInfo("Europe/Kyiv")

ROBOT_FAILURE_PRIORITIES = {
    "NO_HEARTBEAT": 100,
    "STALE_HEARTBEAT": 100,
    "REPEATED_CYCLE_ERRORS": 90,
    "MT5_UNAVAILABLE": 85,
    "TERMINAL_UNAVAILABLE": 85,
    "ACCOUNT_UNAVAILABLE": 80,
    "QUOTES_UNAVAILABLE": 75,
    "TRADE_DISABLED": 70,
}

TELEGRAM_FAILURE_PRIORITIES = {
    "TELEGRAM_HEARTBEAT_STALE": 70,
    "TELEGRAM_REPEATED_ERRORS": 60,
    "TELEGRAM_UNAVAILABLE": 50,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value) -> datetime | None:
    if not value:
        return None

    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def format_local_time(value) -> str:
    dt = parse_datetime(value)
    if dt is None:
        return "нет данных"
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S")


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} ч. {minutes} мин. {secs} сек."
    if minutes:
        return f"{minutes} мин. {secs} сек."
    return f"{secs} сек."


def determine_robot_failure(
    heartbeat: dict,
    config: dict,
    watchdog_started_at: datetime,
) -> dict | None:
    now = utc_now()

    if not heartbeat:
        elapsed = (now - watchdog_started_at).total_seconds()
        if elapsed >= int(config["startup_grace_seconds"]):
            return {
                "code": "NO_HEARTBEAT",
                "title": "Робот не отвечает",
                "reason": "файл контроля работы отсутствует",
                "details": None,
                "priority": ROBOT_FAILURE_PRIORITIES["NO_HEARTBEAT"],
            }
        return None

    activity_values = [
        parse_datetime(heartbeat.get("last_loop_started_at")),
        parse_datetime(heartbeat.get("last_loop_completed_at")),
        parse_datetime(heartbeat.get("started_at")),
    ]
    activity_values = [value for value in activity_values if value is not None]
    last_activity = max(activity_values) if activity_values else None

    if last_activity is not None:
        stale_seconds = (now - last_activity).total_seconds()
        if stale_seconds >= int(config["stale_after_seconds"]):
            return {
                "code": "STALE_HEARTBEAT",
                "title": "Робот не отвечает",
                "reason": (
                    f"торговый цикл не обновлялся более "
                    f"{int(config['stale_after_seconds'])} секунд"
                ),
                "details": f"данные контроля работы устарели на {int(stale_seconds)} сек.",
                "priority": ROBOT_FAILURE_PRIORITIES["STALE_HEARTBEAT"],
            }

    consecutive_errors = int(heartbeat.get("consecutive_errors", 0) or 0)
    if consecutive_errors >= int(config["max_consecutive_errors"]):
        return {
            "code": "REPEATED_CYCLE_ERRORS",
            "title": "Ошибка торгового цикла",
            "reason": f"цикл завершился ошибкой {consecutive_errors} раз подряд",
            "details": str(heartbeat.get("last_error") or "ошибка не указана"),
            "priority": ROBOT_FAILURE_PRIORITIES["REPEATED_CYCLE_ERRORS"],
        }

    terminal_unavailable_since = parse_datetime(
        heartbeat.get("terminal_unavailable_since")
    )
    if (
        heartbeat.get("terminal_available") is False
        and terminal_unavailable_since is not None
    ):
        elapsed = (now - terminal_unavailable_since).total_seconds()
        threshold = int(
            config.get(
                "terminal_unavailable_after_seconds",
                config.get("mt5_unavailable_after_seconds", 180),
            )
        )
        if elapsed >= threshold:
            return {
                "code": "TERMINAL_UNAVAILABLE",
                "title": "Терминал MT5 недоступен",
                "reason": f"данные терминала недоступны {int(elapsed)} секунд",
                "details": str(heartbeat.get("cycle_message") or ""),
                "priority": ROBOT_FAILURE_PRIORITIES["TERMINAL_UNAVAILABLE"],
            }

    mt5_unavailable_since = parse_datetime(heartbeat.get("mt5_unavailable_since"))
    if heartbeat.get("mt5_available") is False and mt5_unavailable_since is not None:
        elapsed = (now - mt5_unavailable_since).total_seconds()
        if elapsed >= int(config["mt5_unavailable_after_seconds"]):
            return {
                "code": "MT5_UNAVAILABLE",
                "title": "MT5 недоступен",
                "reason": f"соединение с MT5 отсутствует {int(elapsed)} секунд",
                "details": str(heartbeat.get("cycle_message") or ""),
                "priority": ROBOT_FAILURE_PRIORITIES["MT5_UNAVAILABLE"],
            }

    account_unavailable_since = parse_datetime(
        heartbeat.get("account_unavailable_since")
    )
    if heartbeat.get("account_available") is False and account_unavailable_since is not None:
        elapsed = (now - account_unavailable_since).total_seconds()
        if elapsed >= int(config["account_unavailable_after_seconds"]):
            return {
                "code": "ACCOUNT_UNAVAILABLE",
                "title": "Данные торгового счёта недоступны",
                "reason": f"данные торгового счёта недоступны {int(elapsed)} секунд",
                "details": str(heartbeat.get("cycle_message") or ""),
                "priority": ROBOT_FAILURE_PRIORITIES["ACCOUNT_UNAVAILABLE"],
            }

    quotes_unavailable_since = parse_datetime(
        heartbeat.get("quotes_unavailable_since")
    )
    if (
        heartbeat.get("quotes_available") is False
        and quotes_unavailable_since is not None
    ):
        elapsed = (now - quotes_unavailable_since).total_seconds()
        threshold = int(config.get("quotes_unavailable_after_seconds", 180))
        if elapsed >= threshold:
            return {
                "code": "QUOTES_UNAVAILABLE",
                "title": "Котировки MT5 недоступны",
                "reason": f"актуальная котировка отсутствует {int(elapsed)} секунд",
                "details": str(heartbeat.get("cycle_message") or ""),
                "priority": ROBOT_FAILURE_PRIORITIES["QUOTES_UNAVAILABLE"],
            }

    trade_disabled_since = parse_datetime(heartbeat.get("trade_disabled_since"))
    if heartbeat.get("trade_allowed") is False and trade_disabled_since is not None:
        elapsed = (now - trade_disabled_since).total_seconds()
        threshold = int(config.get("trade_disabled_after_seconds", 30))
        if elapsed >= threshold:
            return {
                "code": "TRADE_DISABLED",
                "title": "Торговля в MT5 отключена",
                "reason": (
                    "Автоматическая торговля или разрешение счёта отключено "
                    f"{int(elapsed)} секунд"
                ),
                "details": str(heartbeat.get("cycle_message") or ""),
                "priority": ROBOT_FAILURE_PRIORITIES["TRADE_DISABLED"],
            }

    return None


def determine_telegram_failure(heartbeat: dict, config: dict) -> dict | None:
    if not heartbeat or heartbeat.get("telegram_polling_enabled") is not True:
        return None

    now = utc_now()
    status = str(heartbeat.get("telegram_polling_status") or "unknown")
    status_text = {
        "configured": "настроено",
        "starting": "запускается",
        "running": "работает",
        "error": "ошибка",
        "restarting": "перезапускается",
        "stopped": "остановлено",
        "disabled": "отключено",
        "not_configured": "не настроено",
        "unknown": "неизвестно",
    }.get(status, status)
    consecutive_errors = int(heartbeat.get("telegram_consecutive_errors", 0) or 0)

    last_heartbeat = parse_datetime(heartbeat.get("telegram_last_heartbeat_at"))
    if last_heartbeat is not None:
        stale_seconds = (now - last_heartbeat).total_seconds()
        if stale_seconds >= int(config["telegram_stale_after_seconds"]):
            return {
                "code": "TELEGRAM_HEARTBEAT_STALE",
                "reason": (
                    "состояние получения команд Telegram не обновлялось более "
                    f"{int(config['telegram_stale_after_seconds'])} секунд"
                ),
                "details": f"данные контроля работы устарели на {int(stale_seconds)} сек.",
                "priority": TELEGRAM_FAILURE_PRIORITIES["TELEGRAM_HEARTBEAT_STALE"],
            }

    if consecutive_errors >= int(config["telegram_max_consecutive_errors"]):
        return {
            "code": "TELEGRAM_REPEATED_ERRORS",
            "reason": (
                f"Получение команд Telegram завершилось ошибкой "
                f"{consecutive_errors} раз подряд"
            ),
            "details": str(
                heartbeat.get("telegram_last_error") or "ошибка не указана"
            ),
            "priority": TELEGRAM_FAILURE_PRIORITIES["TELEGRAM_REPEATED_ERRORS"],
        }

    unavailable_since = parse_datetime(heartbeat.get("telegram_unavailable_since"))
    if status in {"configured", "starting", "error", "restarting", "stopped"}:
        if unavailable_since is not None:
            elapsed = (now - unavailable_since).total_seconds()
            if elapsed >= int(config["telegram_unavailable_after_seconds"]):
                return {
                    "code": "TELEGRAM_UNAVAILABLE",
                    "reason": (
                        f"Получение команд Telegram: состояние «{status_text}» "
                        f"уже {int(elapsed)} секунд"
                    ),
                    "details": str(heartbeat.get("telegram_last_error") or ""),
                    "priority": TELEGRAM_FAILURE_PRIORITIES["TELEGRAM_UNAVAILABLE"],
                }

    return None


def should_send_new_alert(
    active_code: str | None,
    active_priority: int,
    failure: dict,
) -> bool:
    if active_code is None:
        return True
    if active_code == failure["code"]:
        return False
    return int(failure.get("priority", 0)) > int(active_priority or 0)


def process_robot_alert(
    heartbeat: dict,
    state: dict,
    failure: dict | None,
    config: dict,
) -> bool:
    changed = False
    instance_id = str(heartbeat.get("instance_id") or "")

    if failure is not None:
        if (
            bool(config.get("notify_on_failure", True))
            and should_send_new_alert(
                state.get("active_robot_alert"),
                int(state.get("active_robot_alert_priority", 0) or 0),
                failure,
            )
        ):
            sent = notify_watchdog_failure(
                title=failure["title"],
                reason=failure["reason"],
                last_activity=format_local_time(
                    heartbeat.get("last_loop_completed_at")
                    or heartbeat.get("last_loop_started_at")
                ),
                last_success=format_local_time(
                    heartbeat.get("last_successful_cycle_at")
                ),
                symbol=str(heartbeat.get("symbol") or "XAUUSD"),
                details=failure.get("details"),
            )
            if sent:
                state["active_robot_alert"] = failure["code"]
                state["active_robot_alert_reason"] = failure["reason"]
                state["active_robot_alert_priority"] = int(
                    failure.get("priority", 0)
                )
                state["robot_alert_started_at"] = utc_now().isoformat()
                state["robot_alert_instance_id"] = instance_id or None
                changed = True
                log_event(
                    f"Уведомление watchdog о сбое робота отправлено: {failure['code']}"
                )
        return changed

    if state.get("active_robot_alert") is not None and bool(
        config.get("notify_on_recovery", True)
    ):
        alert_started = parse_datetime(state.get("robot_alert_started_at")) or utc_now()
        now = utc_now()
        sent = notify_watchdog_recovery(
            symbol=str(heartbeat.get("symbol") or "XAUUSD"),
            recovered_at=now.astimezone(DISPLAY_TIMEZONE).strftime(
                "%d.%m.%Y %H:%M:%S"
            ),
            downtime_text=format_duration((now - alert_started).total_seconds()),
            previous_reason=str(
                state.get("active_robot_alert_reason")
                or state.get("active_robot_alert")
            ),
        )
        if sent:
            log_event(
                "Уведомление watchdog о восстановлении робота отправлено: "
                f"предыдущая_ошибка={state.get('active_robot_alert')}"
            )
            state["active_robot_alert"] = None
            state["active_robot_alert_reason"] = None
            state["active_robot_alert_priority"] = 0
            state["robot_alert_started_at"] = None
            state["robot_alert_instance_id"] = None
            state["last_robot_recovery_at"] = now.isoformat()
            changed = True

    return changed


def process_telegram_alert(
    heartbeat: dict,
    state: dict,
    failure: dict | None,
    config: dict,
) -> bool:
    changed = False
    instance_id = str(heartbeat.get("instance_id") or "")

    if failure is not None:
        if (
            bool(config.get("notify_on_telegram_failure", True))
            and should_send_new_alert(
                state.get("active_telegram_alert"),
                int(state.get("active_telegram_alert_priority", 0) or 0),
                failure,
            )
        ):
            sent = notify_telegram_polling_failure(
                reason=failure["reason"],
                last_heartbeat=format_local_time(
                    heartbeat.get("telegram_last_heartbeat_at")
                ),
                last_error=str(
                    failure.get("details")
                    or heartbeat.get("telegram_last_error")
                    or ""
                ),
                symbol=str(heartbeat.get("symbol") or "XAUUSD"),
            )
            if sent:
                state["active_telegram_alert"] = failure["code"]
                state["active_telegram_alert_reason"] = failure["reason"]
                state["active_telegram_alert_priority"] = int(
                    failure.get("priority", 0)
                )
                state["telegram_alert_started_at"] = utc_now().isoformat()
                state["telegram_alert_instance_id"] = instance_id or None
                changed = True
                log_event(
                    f"Уведомление watchdog о сбое Telegram отправлено: {failure['code']}"
                )
        return changed

    if state.get("active_telegram_alert") is not None and bool(
        config.get("notify_on_telegram_recovery", True)
    ):
        alert_started = parse_datetime(
            state.get("telegram_alert_started_at")
        ) or utc_now()
        now = utc_now()
        sent = notify_telegram_polling_recovery(
            symbol=str(heartbeat.get("symbol") or "XAUUSD"),
            recovered_at=now.astimezone(DISPLAY_TIMEZONE).strftime(
                "%d.%m.%Y %H:%M:%S"
            ),
            downtime_text=format_duration((now - alert_started).total_seconds()),
            previous_reason=str(
                state.get("active_telegram_alert_reason")
                or state.get("active_telegram_alert")
            ),
        )
        if sent:
            log_event(
                "Уведомление watchdog о восстановлении Telegram отправлено: "
                f"предыдущая_ошибка={state.get('active_telegram_alert')}"
            )
            state["active_telegram_alert"] = None
            state["active_telegram_alert_reason"] = None
            state["active_telegram_alert_priority"] = 0
            state["telegram_alert_started_at"] = None
            state["telegram_alert_instance_id"] = None
            state["last_telegram_recovery_at"] = now.isoformat()
            changed = True

    return changed


def run_watchdog_loop() -> None:
    config = load_monitoring_config()
    if not bool(config.get("enabled", True)):
        print("Watchdog отключён в config/monitoring.json")
        return

    watchdog_started_at = utc_now()
    log_event("Watchdog запущен")

    while True:
        heartbeat = load_heartbeat()
        state = load_watchdog_state()
        robot_failure = determine_robot_failure(
            heartbeat,
            config,
            watchdog_started_at,
        )

        # Telegram polling контролируем только когда сам run_bot жив. При полном
        # падении процесса основное уведомление о роботе имеет приоритет.
        telegram_failure = None
        if robot_failure is None:
            telegram_failure = determine_telegram_failure(heartbeat, config)

        instance_id = str(heartbeat.get("instance_id") or "")

        if (
            heartbeat
            and instance_id
            and robot_failure is None
            and heartbeat.get("last_loop_completed_at")
            and state.get("active_robot_alert") is None
            and bool(config.get("notify_on_robot_start", True))
            and state.get("last_start_notified_instance_id") != instance_id
        ):
            sent = notify_robot_started(
                symbol=str(heartbeat.get("symbol") or "XAUUSD"),
                instance_id=instance_id,
                pid=heartbeat.get("pid"),
                server=heartbeat.get("server"),
            )
            if sent:
                state["last_start_notified_instance_id"] = instance_id
                save_watchdog_state(state)
                log_event(f"Уведомление о запуске робота отправлено: экземпляр={instance_id}")

        changed = process_robot_alert(
            heartbeat=heartbeat,
            state=state,
            failure=robot_failure,
            config=config,
        )

        # Не создаём новый Telegram-alert, пока весь робот недоступен. Уже
        # существующий Telegram-alert сохраняется и будет закрыт после возврата.
        if robot_failure is None:
            changed = process_telegram_alert(
                heartbeat=heartbeat,
                state=state,
                failure=telegram_failure,
                config=config,
            ) or changed

        if changed:
            save_watchdog_state(state)

        time.sleep(max(1, int(config.get("check_interval_seconds", 30))))


def main() -> None:
    try:
        with ProcessLock("run_watchdog"):
            log_event("Блокировка процесса run_watchdog получена")
            run_watchdog_loop()
    except ProcessAlreadyRunningError as error:
        print("=" * 60)
        print("WATCHDOG УЖЕ ЗАПУЩЕН")
        print("=" * 60)
        print(str(error))
        log_event(f"Повторный запуск run_watchdog заблокирован: {error}")
    except KeyboardInterrupt:
        print("\nrun_watchdog остановлен пользователем")
        log_event("run_watchdog остановлен пользователем")


if __name__ == "__main__":
    main()
