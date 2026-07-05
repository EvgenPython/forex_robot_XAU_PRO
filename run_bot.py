import asyncio
import json
import threading
import time
import traceback
from pathlib import Path

from app.health_manager import HealthManager
from app.logger import log_event
from app.main import main as trade_main
from app.monitoring_config import load_monitoring_config
from app.process_lock import ProcessAlreadyRunningError, ProcessLock
from app.telegram_bot import main as telegram_main
from app.telegram_notifier import load_telegram_config


ROOT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"
LOOP_DELAY_SECONDS = 10


def load_symbol() -> str:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        settings = json.load(file)
    return str(settings.get("symbol", "XAUUSD"))


def run_trading_loop(health: HealthManager) -> None:
    print("=" * 60)
    print("ТОРГОВЫЙ РОБОТ MT5 ЗАПУЩЕН")
    print("=" * 60)

    while True:
        health.mark_loop_started()

        try:
            print()
            print("=" * 60)
            print("НОВЫЙ ЦИКЛ")
            print("=" * 60)

            result = trade_main()
            health.mark_cycle_result(result)

        except Exception as error:
            health.mark_cycle_error(error)
            print()
            print("=" * 60)
            print("ОШИБКА ТОРГОВОГО РОБОТА")
            print("=" * 60)
            traceback.print_exc()

        time.sleep(LOOP_DELAY_SECONDS)


def calculate_restart_delay(
    consecutive_errors: int,
    initial_seconds: int,
    maximum_seconds: int,
) -> int:
    initial = max(1, int(initial_seconds))
    maximum = max(initial, int(maximum_seconds))
    exponent = min(max(0, int(consecutive_errors) - 1), 10)
    return min(maximum, initial * (2 ** exponent))


def run_telegram_bot_forever(health: HealthManager, monitoring: dict) -> None:
    config = load_telegram_config()
    telegram_enabled = bool(config.get("enabled", False))
    bot_token = str(config.get("bot_token", "") or "").strip()

    health.configure_telegram(enabled=telegram_enabled)

    if not telegram_enabled:
        print("Telegram-бот отключён в config/telegram.json")
        return

    if not bot_token:
        error = RuntimeError("Токен Telegram-бота не указан")
        health.mark_telegram_error(error)
        print(str(error))
        return

    pulse_seconds = max(
        5,
        int(monitoring.get("telegram_health_pulse_seconds", 15)),
    )
    restart_initial = max(
        1,
        int(monitoring.get("telegram_restart_initial_seconds", 5)),
    )
    restart_max = max(
        restart_initial,
        int(monitoring.get("telegram_restart_max_seconds", 60)),
    )

    while True:
        health.mark_telegram_starting()

        try:
            print("=" * 60)
            print("ЗАПУСК TELEGRAM-БОТА")
            print("=" * 60)

            asyncio.run(
                telegram_main(
                    on_polling_started=health.mark_telegram_running,
                    on_polling_heartbeat=health.mark_telegram_heartbeat,
                    on_update=health.mark_telegram_update,
                    heartbeat_interval_seconds=pulse_seconds,
                )
            )

            # start_polling не должен завершаться сам по себе при обычной работе.
            raise RuntimeError("Получение команд Telegram неожиданно остановилось")

        except Exception as error:
            consecutive_errors = health.mark_telegram_error(error)
            print()
            print("=" * 60)
            print("ОШИБКА TELEGRAM-БОТА")
            print("=" * 60)
            traceback.print_exc()

        delay = calculate_restart_delay(
            consecutive_errors=consecutive_errors,
            initial_seconds=restart_initial,
            maximum_seconds=restart_max,
        )
        health.mark_telegram_restarting(delay)
        log_event(
            "Запланирован перезапуск Telegram: "
            f"попытка={consecutive_errors}, задержка={delay}s"
        )
        print(f"Telegram-бот будет перезапущен через {delay} секунд...")
        time.sleep(delay)


def run_forever() -> None:
    monitoring = load_monitoring_config()
    health = HealthManager(symbol=load_symbol())

    telegram_thread = threading.Thread(
        target=run_telegram_bot_forever,
        args=(health, monitoring),
        daemon=True,
        name="telegram-polling",
    )
    telegram_thread.start()
    run_trading_loop(health)


def main() -> None:
    try:
        with ProcessLock("run_bot"):
            log_event("Блокировка процесса run_bot получена")
            run_forever()
    except ProcessAlreadyRunningError as error:
        print("=" * 60)
        print("ТОРГОВЫЙ РОБОТ УЖЕ ЗАПУЩЕН")
        print("=" * 60)
        print(str(error))
        log_event(f"Повторный запуск run_bot заблокирован: {error}")
    except KeyboardInterrupt:
        print("\nrun_bot остановлен пользователем")
        log_event("run_bot остановлен пользователем")


if __name__ == "__main__":
    main()
