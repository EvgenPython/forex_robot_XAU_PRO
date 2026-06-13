import time
import traceback

from app.main import main


LOOP_DELAY_SECONDS = 10


def run_forever():
    print("=" * 60)
    print("MT5 XAU BOT STARTED")
    print("=" * 60)

    while True:
        try:
            print()
            print("=" * 60)
            print("NEW LOOP")
            print("=" * 60)

            main()

        except Exception:
            print()
            print("=" * 60)
            print("ERROR")
            print("=" * 60)

            traceback.print_exc()

        time.sleep(LOOP_DELAY_SECONDS)


if __name__ == "__main__":
    run_forever()