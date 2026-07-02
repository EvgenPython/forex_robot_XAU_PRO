import asyncio
import threading
import time
import traceback

from app.main import main as trade_main
from app.telegram_bot import main as telegram_main


LOOP_DELAY_SECONDS = 10
TELEGRAM_RESTART_DELAY_SECONDS = 30


def run_trading_loop():
    print("=" * 60)
    print("MT5 XAU BOT STARTED")
    print("=" * 60)

    while True:
        try:
            print()
            print("=" * 60)
            print("NEW LOOP")
            print("=" * 60)

            trade_main()

        except Exception:
            print()
            print("=" * 60)
            print("TRADING BOT ERROR")
            print("=" * 60)
            traceback.print_exc()

        time.sleep(LOOP_DELAY_SECONDS)


def run_telegram_bot_forever():
    while True:
        try:
            print("=" * 60)
            print("TELEGRAM BOT STARTING")
            print("=" * 60)

            asyncio.run(telegram_main())

            print()
            print("=" * 60)
            print("TELEGRAM BOT STOPPED")
            print("=" * 60)

        except Exception:
            print()
            print("=" * 60)
            print("TELEGRAM BOT ERROR")
            print("=" * 60)
            traceback.print_exc()

        print(
            f"Telegram bot will restart in "
            f"{TELEGRAM_RESTART_DELAY_SECONDS} seconds..."
        )
        time.sleep(TELEGRAM_RESTART_DELAY_SECONDS)


def run_forever():
    telegram_thread = threading.Thread(
        target=run_telegram_bot_forever,
        daemon=True,
    )
    telegram_thread.start()

    run_trading_loop()


if __name__ == "__main__":
    run_forever()
