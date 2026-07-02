import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from app.mt5_client import (
    ensure_mt5_connection,
    get_account_info,
    get_closed_deals_from_history,
    get_tick,
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
    load_state,
    save_state,
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

from app.account_guard import (
    update_account_guard,
    is_account_blocked,
)

from app.logger import (
    log_event,
    log_trade_event,
)

from app.telegram_notifier import (
    notify_open_trade,
    notify_daily_guard,
    notify_error,
    notify_account_guard,
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


STATS_TIMEZONE = ZoneInfo("Europe/Kyiv")


def get_today_closed_profit(symbol: str) -> float:
    now = datetime.now(STATS_TIMEZONE)
    today = now.date()

    # Берем историю с запасом, чтобы покрыть сделки, открытые перед выходными.
    # Потом все равно фильтруем только закрытия сегодняшнего дня в Europe/Kyiv.
    date_from = (now - timedelta(days=5)).replace(tzinfo=None)
    date_to = (now + timedelta(minutes=1)).replace(tzinfo=None)

    closed_deals = get_closed_deals_from_history(
        date_from=date_from,
        date_to=date_to,
        symbol=symbol,
    )

    total = 0.0

    for deal in closed_deals:
        deal_time = deal.get("time")
        if deal_time is None:
            continue

        deal_time = deal_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(STATS_TIMEZONE)

        if deal_time.date() == today:
            total += float(deal.get("net_profit", 0.0))

    return total

def update_active_trade_cache(symbol: str, account):
    state = load_state()
    trade = state.get("active_trade")

    if not trade:
        return

    tick = get_tick(symbol)
    direction = str(trade.get("direction", "")).upper()
    current_price = None

    if tick is not None:
        if direction == "BUY":
            current_price = float(tick.bid)
        elif direction == "SELL":
            current_price = float(tick.ask)

    balance = float(account.balance) if account is not None else 0.0
    floating_profit = float(account.profit) if account is not None else 0.0
    floating_percent = (floating_profit / balance * 100) if balance > 0 else 0.0

    trade["current_price"] = round(current_price, 2) if current_price is not None else None
    trade["floating_profit"] = round(floating_profit, 2)
    trade["floating_percent"] = round(floating_percent, 2)
    trade["cache_updated_at"] = datetime.now(STATS_TIMEZONE).isoformat(timespec="seconds")

    state["active_trade"] = trade
    save_state(state)



def main():
    try:
        settings = load_settings()

        symbol = settings["symbol"]
        risk_percent = float(settings.get("risk_percent", 1.0))
        dry_run = bool(settings.get("dry_run", True))

        daily_soft_stop_percent = float(settings.get("daily_soft_stop_percent", 3.0))
        daily_hard_stop_percent = float(settings.get("daily_hard_stop_percent", 4.0))
        account_guard_percent = float(settings.get("account_guard_percent", 8.0))
        account_guard_pause_days = int(settings.get("account_guard_pause_days", 14))

        if not ensure_mt5_connection(symbol=symbol, quiet=True):
            log_event("MT5 connection failed or symbol check failed")
            safe_notify_error("MT5 connection failed or symbol check failed")
            return

        account = get_account_info()

        if account is None:
            log_event("Account info not available")
            safe_notify_error("Account info not available")
            return

        daily_closed_profit = get_today_closed_profit(symbol)

        daily_guard = update_daily_guard(
            account_balance=float(account.balance),
            account_equity=float(account.equity),
            account_profit=float(account.profit),
            daily_closed_profit=daily_closed_profit,
            soft_stop_percent=daily_soft_stop_percent,
            hard_stop_percent=daily_hard_stop_percent,
        )

        account_guard = update_account_guard(
            account_balance=float(account.balance),
            account_equity=float(account.equity),
            max_drawdown_percent=account_guard_percent,
            pause_days=account_guard_pause_days,
        )

        if is_account_blocked(account_guard):
            log_event("Account guard is active. Trading is blocked.")

            if not account_guard.get("notification_sent", False):
                try:
                    notify_account_guard(
                        drawdown_percent=float(account_guard.get("drawdown_percent", 0.0)),
                        max_drawdown_percent=float(
                            account_guard.get("max_drawdown_percent", account_guard_percent)
                        ),
                        blocked_until=account_guard.get("blocked_until"),
                    )

                    account_guard["notification_sent"] = True

                    state = load_state()
                    state["account_guard"] = account_guard
                    save_state(state)

                except Exception as error:
                    log_event(f"Telegram account guard notification failed: {error}")

            active_trade = get_active_trade()
            real_position_exists = has_open_position(symbol)

            if dry_run:
                if active_trade is not None:
                    log_event("DRY RUN: clearing active trade due to account guard")
                    clear_trade()
            else:
                if real_position_exists:
                    close_position(symbol=symbol, reason="ACCOUNT_GUARD")

            return

        market_data = load_market_data(symbol)

        h4 = add_indicators(market_data["h4"], settings)
        h1 = add_indicators(market_data["h1"], settings)
        m15 = add_indicators(market_data["m15"], settings)

        current_candle_time = str(m15.iloc[-1]["time"])
        last_processed = get_last_m15_candle()
        active_trade = get_active_trade()

        real_position_exists = has_open_position(symbol)

        if active_trade is not None or real_position_exists:
            update_active_trade_cache(symbol, account)

        if is_hard_stop_triggered(daily_guard):
            log_event("Daily hard stop is active")

            if not daily_guard.get("notification_sent", False):
                try:
                    notify_daily_guard(
                        drawdown_percent=float(daily_guard.get("drawdown_percent", 0.0)),
                        stop_type="HARD STOP",
                    )

                    daily_guard["notification_sent"] = True

                    state = load_state()
                    state["daily_guard"] = daily_guard
                    save_state(state)

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

            if not daily_guard.get("notification_sent", False):
                try:
                    notify_daily_guard(
                        drawdown_percent=float(daily_guard.get("drawdown_percent", 0.0)),
                        stop_type="SOFT STOP",
                    )

                    daily_guard["notification_sent"] = True

                    state = load_state()
                    state["daily_guard"] = daily_guard
                    save_state(state)

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
            "current_price": None,
            "floating_profit": 0.0,
            "floating_percent": 0.0,
            "cache_updated_at": None,
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