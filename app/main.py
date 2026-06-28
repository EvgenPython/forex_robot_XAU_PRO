import json
from pathlib import Path

from app.mt5_client import (
    ensure_mt5_connection,
    get_account_info,
)

from app.data_loader import load_market_data
from app.indicators import add_indicators

from app.strategy import generate_signal
from app.models import SignalAction

from app.risk import calculate_position_size
from app.executor import send_market_order

from app.position_manager import (
    has_open_position,
    manage_open_position,
    close_position,
)

from app.state_manager import (
    get_last_m15_candle,
    save_last_m15_candle,
    get_active_trade,
    save_trade,
    clear_trade,
    save_last_signal,
)

from app.daily_guard import (
    update_daily_guard,
    is_trading_blocked,
    is_hard_stop_triggered,
)

from app.logger import (
    log_event,
    log_trade_event,
)

from app.telegram_notifier import (
    notify_open_trade,
    notify_daily_guard,
    notify_error,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"


def load_settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def safe_notify_error(error_text: str):
    try:
        notify_error(error_text)
    except Exception as error:
        log_event(f"Telegram error notification failed: {error}")


def main():
    try:
        settings = load_settings()

        symbol = settings["symbol"]
        risk_percent = float(settings.get("risk_percent", 1.0))
        dry_run = bool(settings.get("dry_run", True))

        daily_soft_stop_percent = float(settings.get("daily_soft_stop_percent", 3.0))
        daily_hard_stop_percent = float(settings.get("daily_hard_stop_percent", 4.0))


        if not ensure_mt5_connection(symbol=symbol, quiet=True):
            log_event("MT5 connection failed or symbol check failed")
            safe_notify_error("MT5 connection failed or symbol check failed")
            return

        account = get_account_info()

        if account is None:
            log_event("Account info not available")
            safe_notify_error("Account info not available")
            return

        daily_guard = update_daily_guard(
            account_balance=float(account.balance),
            account_equity=float(account.equity),
            soft_stop_percent=daily_soft_stop_percent,
            hard_stop_percent=daily_hard_stop_percent,
        )

        market_data = load_market_data(symbol)

        h4 = add_indicators(market_data["h4"], settings)
        h1 = add_indicators(market_data["h1"], settings)
        m15 = add_indicators(market_data["m15"], settings)

        current_candle_time = str(m15.iloc[-1]["time"])
        last_processed = get_last_m15_candle()
        active_trade = get_active_trade()

        real_position_exists = has_open_position(symbol)

        if is_hard_stop_triggered(daily_guard):
            log_event("Daily hard stop is active")

            try:
                notify_daily_guard(
                    drawdown_percent=float(daily_guard.get("drawdown_percent", 0.0)),
                    stop_type="HARD STOP",
                )
            except Exception as error:
                log_event(f"Telegram daily hard stop notification failed: {error}")

            if dry_run:
                if active_trade is not None:
                    log_event("DRY RUN: clearing active trade due to daily hard stop")
                    clear_trade()
            else:
                if real_position_exists:
                    close_position(symbol=symbol, reason="DAILY_HARD_STOP")

            save_last_m15_candle(current_candle_time)
            return

        if active_trade is not None or real_position_exists:
            log_event(f"Active trade exists for {symbol}. Managing position.")

            manage_open_position(
                symbol=symbol,
                m15=m15,
                settings=settings,
                current_candle_time=current_candle_time,
            )

            save_last_m15_candle(current_candle_time)
            return

        if current_candle_time == last_processed:
            return
        log_event(f"NEW M15 CANDLE: {current_candle_time}")

        if is_trading_blocked(daily_guard):
            save_last_m15_candle(current_candle_time)
            log_event("Daily soft stop is active. New trades are blocked.")

            try:
                notify_daily_guard(
                    drawdown_percent=float(daily_guard.get("drawdown_percent", 0.0)),
                    stop_type="SOFT STOP",
                )
            except Exception as error:
                log_event(f"Telegram daily soft stop notification failed: {error}")

            return

        signal = generate_signal(
            h4=h4,
            h1=h1,
            m15=m15,
            settings=settings,
        )

        save_last_signal({
            "time": current_candle_time,
            "symbol": symbol,
            "action": signal.action.value,
            "score": signal.score,
            "buy_score": signal.buy_score,
            "sell_score": signal.sell_score,
            "reasons": signal.reasons,
        })

        log_event(f"Signal action: {signal.action.value}")
        log_event(f"Signal score: {signal.score}")
        log_event(f"Signal reasons: {signal.reasons}")

        if signal.action == SignalAction.WAIT:
            save_last_m15_candle(current_candle_time)
            log_event("WAIT - no trade")
            return

        if signal.entry_price is None or signal.stop_loss is None:
            save_last_m15_candle(current_candle_time)
            log_event("Invalid signal: entry_price or stop_loss is missing")
            return

        volume = calculate_position_size(
            balance=float(account.balance),
            risk_percent=risk_percent,
            entry_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            symbol=symbol,
        )

        if volume <= 0:
            save_last_m15_candle(current_candle_time)
            log_event("Volume calculation failed or risk too high for minimum lot")
            return

        result = send_market_order(
            symbol=symbol,
            direction=signal.action.value,
            volume=volume,
            stop_loss=float(signal.stop_loss),
            dry_run=dry_run,
        )

        ticket = "DRY_RUN"

        if not dry_run and result["result"] is not None:
            ticket = getattr(result["result"], "order", "")

        trade_data = {
            "symbol": symbol,
            "direction": signal.action.value,
            "entry_price": round(float(signal.entry_price), 2),
            "stop_loss": round(float(signal.stop_loss), 2),
            "tp1": round(float(signal.tp1), 2),
            "tp2": round(float(signal.tp2), 2),
            "tp3": round(float(signal.tp3), 2),
            "volume": volume,
            "score": signal.score,
            "reasons": signal.reasons,
            "ticket": ticket,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "breakeven_active": False,
            "candles_in_trade": 0,
            "open_time": current_candle_time,
            "last_managed_m15_candle": current_candle_time,
            "dry_run": dry_run,
        }

        save_trade(trade_data)

        try:
            notify_open_trade(
                symbol=symbol,
                direction=signal.action.value,
                entry_price=trade_data["entry_price"],
                stop_loss=trade_data["stop_loss"],
                tp1=trade_data["tp1"],
                tp2=trade_data["tp2"],
                volume=volume,
                score=signal.score,
                dry_run=dry_run,
            )
        except Exception as error:
            log_event(f"Telegram open trade notification failed: {error}")

        log_trade_event(
            event="OPEN_SIGNAL",
            symbol=symbol,
            direction=signal.action.value,
            entry_price=trade_data["entry_price"],
            stop_loss=trade_data["stop_loss"],
            tp1=trade_data["tp1"],
            tp2=trade_data["tp2"],
            tp3=trade_data["tp3"],
            volume=volume,
            score=signal.score,
            reasons=" | ".join(signal.reasons),
            ticket=ticket,
            balance=account.balance,
            equity=account.equity,
            comment=f"dry_run={dry_run}",
        )

        save_last_m15_candle(current_candle_time)

        log_event("Trade signal processed")

    except Exception as error:
        log_event(f"CRITICAL ERROR in main.py: {error}")
        safe_notify_error(f"CRITICAL ERROR in main.py: {error}")
        raise


if __name__ == "__main__":
    main()