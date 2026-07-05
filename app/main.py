import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.account_guard import is_account_blocked, update_account_guard
from app.daily_guard import (
    get_trading_day_context,
    is_hard_stop_triggered,
    is_trading_blocked,
    update_daily_guard,
)
from app.data_loader import load_market_data
from app.executor import (
    BOT_MAGIC,
    OrderPreparationError,
    OrderRejectedError,
    OrderSendUncertainError,
    PartialExecutionError,
    send_market_order,
)
from app.indicators import add_indicators
from app.logger import log_event
from app.models import CycleResult, CycleStatus, SignalAction
from app.monitoring_config import load_monitoring_config
from app.mt5_client import (
    get_tick,
    get_trade_deals_from_history,
    probe_mt5_health,
)
from app.position_manager import (
    close_position,
    get_any_open_position,
    get_open_position,
    manage_open_position,
    wait_for_open_position,
)
from app.risk import calculate_position_size
from app.state_manager import (
    ensure_runtime_metadata,
    get_current_strategy_config_hash,
    clear_pending_open,
    block_state_safety,
    ensure_account_context,
    get_active_trade,
    get_last_m15_candle,
    get_pending_open,
    get_state_safety,
    load_state,
    save_last_m15_candle,
    save_last_signal,
    save_pending_open,
    save_state,
    save_trade,
    mark_state_safety_notification_sent,
)
from app.state_recovery import reconcile_state_safety
from app.strategy import generate_signal
from app.telegram_notifier import (
    notify_account_guard,
    notify_daily_guard,
    notify_state_warning,
    telegram_notifications_enabled,
)
from app.text_ru import (
    translate_direction,
    translate_runtime_status,
    translate_signal_reasons,
)
from app.trade_reconciler import (
    handle_position_without_state,
    reconcile_active_trade,
    reconcile_pending_open,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT_DIR / "config" / "strategy_settings.json"
STATS_TIMEZONE = ZoneInfo("Europe/Kyiv")


def load_settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def cycle_result(
    status: CycleStatus,
    account=None,
    healthy: bool = True,
    mt5_available: bool = True,
    terminal_available: bool = True,
    account_available: bool = True,
    quotes_available: bool = True,
    trade_allowed: bool = True,
    mt5_issue_code: str | None = None,
    message: str = "",
) -> CycleResult:
    return CycleResult(
        status=status,
        healthy=healthy,
        mt5_available=mt5_available,
        terminal_available=terminal_available,
        account_available=account_available,
        quotes_available=quotes_available,
        trade_allowed=trade_allowed,
        mt5_issue_code=mt5_issue_code,
        message=message,
        account_login=int(account.login) if account is not None else None,
        server=str(account.server) if account is not None else None,
    )


def get_today_realized_trade_result(symbol: str) -> float:
    day = get_trading_day_context()
    deals = get_trade_deals_from_history(
        date_from=day["start_utc"],
        date_to=day["now_utc"] + timedelta(minutes=1),
        symbol=symbol,
    )

    return sum(
        float(deal.get("profit", 0.0))
        + float(deal.get("commission", 0.0))
        + float(deal.get("swap", 0.0))
        + float(deal.get("fee", 0.0))
        for deal in deals
    )


def update_active_trade_cache(symbol: str, account) -> None:
    trade = get_active_trade()
    if not trade:
        return

    position = get_open_position(symbol=symbol, trade=trade)
    if position is None:
        return

    tick = get_tick(symbol)
    direction = str(trade.get("direction", "")).upper()
    current_price = None

    if tick is not None:
        if direction == "BUY":
            current_price = float(tick.bid)
        elif direction == "SELL":
            current_price = float(tick.ask)

    floating_profit = float(getattr(position, "profit", 0.0) or 0.0)
    balance_at_open = float(trade.get("balance_at_open", 0.0) or 0.0)
    floating_percent = (
        floating_profit / balance_at_open * 100 if balance_at_open > 0 else 0.0
    )

    trade["current_price"] = round(current_price, 2) if current_price is not None else None
    trade["floating_profit"] = round(floating_profit, 2)
    trade["floating_percent"] = round(floating_percent, 2)
    trade["cache_updated_at"] = datetime.now(STATS_TIMEZONE).isoformat(timespec="seconds")
    trade["position_ticket"] = int(getattr(position, "ticket", 0) or 0)
    trade["position_id"] = int(
        getattr(position, "identifier", 0) or trade.get("position_id") or position.ticket
    )
    trade["server"] = trade.get("server") or str(account.server)
    trade["account_login"] = trade.get("account_login") or int(account.login)
    save_trade(trade)


def _send_account_guard_notification(account_guard: dict, limit: float) -> None:
    if account_guard.get("notification_sent", False):
        return

    sent = notify_account_guard(
        drawdown_percent=float(account_guard.get("drawdown_percent", 0.0)),
        max_drawdown_percent=float(account_guard.get("max_drawdown_percent", limit)),
        blocked_until=account_guard.get("blocked_until"),
    )

    if sent:
        account_guard["notification_sent"] = True
        state = load_state()
        state["account_guard"] = account_guard
        save_state(state)
        log_event("Уведомление защиты счёта отправлено")


def _send_daily_guard_notification(daily_guard: dict, stop_type: str) -> None:
    if daily_guard.get("notification_sent", False):
        return

    sent = notify_daily_guard(
        drawdown_percent=float(daily_guard.get("drawdown_percent", 0.0)),
        stop_type=stop_type,
    )

    if sent:
        daily_guard["notification_sent"] = True
        daily_guard["notification_type"] = stop_type
        state = load_state()
        state["daily_guard"] = daily_guard
        save_state(state)
        log_event(f"Уведомление дневной защиты отправлено: {stop_type}")


def _activate_execution_safety(
    reason_code: str,
    title: str,
    message: str,
    details: dict,
) -> None:
    changed = block_state_safety(reason_code, message, details=details)
    if not changed:
        return
    sent = notify_state_warning(title, message)
    if sent or not telegram_notifications_enabled():
        mark_state_safety_notification_sent()
    log_event(f"Защита исполнения включена: {reason_code}: {message}")


def _build_pending_open(signal, symbol: str, volume: float, account, candle_time: str) -> dict:
    request_id = uuid.uuid4().hex
    return {
        "request_id": request_id,
        "order_comment": f"mt5bot_{request_id[:8]}",
        "symbol": symbol,
        "direction": signal.action.value,
        "signal_entry_price": float(signal.entry_price),
        "stop_loss": float(signal.stop_loss),
        "tp1": float(signal.tp1),
        "tp2": float(signal.tp2),
        "tp3": float(signal.tp3),
        "volume": float(volume),
        "score": int(signal.score),
        "reasons": list(signal.reasons),
        "signal_candle_time": candle_time,
        "balance_at_open": float(account.balance),
        "account_login": int(account.login),
        "server": str(account.server),
        "magic": BOT_MAGIC,
        "strategy_config_hash": get_current_strategy_config_hash(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared",
        "order_ticket": 0,
        "deal_ticket": 0,
        "warning_sent": False,
    }


def main() -> CycleResult:
    settings = load_settings()
    runtime_metadata = ensure_runtime_metadata(settings)
    symbol = settings["symbol"]
    risk_percent = float(settings.get("risk_percent", 1.0))
    daily_soft_stop_percent = float(settings.get("daily_soft_stop_percent", 3.0))
    daily_hard_stop_percent = float(settings.get("daily_hard_stop_percent", 4.0))
    account_guard_percent = float(settings.get("account_guard_percent", 8.0))
    account_guard_pause_days = int(settings.get("account_guard_pause_days", 14))

    if runtime_metadata.get("changed", False):
        log_event(
            "Изменился хеш настроек стратегии: "
            f"{runtime_metadata.get('previous_hash')} -> {runtime_metadata.get('config_hash')}"
        )

    mt5_health = probe_mt5_health(symbol)
    account = mt5_health.get("account")
    issue_code = mt5_health.get("issue_code")

    if mt5_health.get("status_changed"):
        log_event(
            "Изменилось состояние MT5: "
            f"проблема={issue_code or 'НЕТ'}, сообщение={mt5_health.get('message')}"
        )

    if issue_code == "TERMINAL_UNAVAILABLE":
        return cycle_result(
            CycleStatus.TERMINAL_UNAVAILABLE,
            healthy=False,
            mt5_available=False,
            terminal_available=False,
            account_available=False,
            quotes_available=False,
            trade_allowed=False,
            mt5_issue_code=issue_code,
            message=str(mt5_health.get("message") or "Терминал MT5 недоступен"),
        )

    if issue_code == "ACCOUNT_UNAVAILABLE" or account is None:
        return cycle_result(
            CycleStatus.ACCOUNT_UNAVAILABLE,
            healthy=False,
            mt5_available=True,
            terminal_available=True,
            account_available=False,
            quotes_available=False,
            trade_allowed=False,
            mt5_issue_code="ACCOUNT_UNAVAILABLE",
            message=str(mt5_health.get("message") or "Данные торгового счёта недоступны"),
        )

    account_context = ensure_account_context(
        account_login=int(account.login),
        server=str(account.server),
        account_balance=float(account.balance),
    )
    if not account_context.get("safe", False):
        reason = str(account_context.get("reason", "Несовпадение контекста торгового счёта"))
        log_event(f"НЕСОВПАДЕНИЕ ТОРГОВОГО СЧЁТА: {reason}")
        reconcile_state_safety(symbol=symbol, account=account)
        return cycle_result(
            CycleStatus.ACCOUNT_CONTEXT_MISMATCH,
            account=account,
            healthy=True,
            message=reason,
        )
    if account_context.get("changed", False):
        log_event(f"Контекст торгового счёта обновлён: {account_context.get('reason', '')}")

    # For an uncertain order_send result we must first inspect MT5 position/history.
    # This does not open another trade; it only tries to reconcile the already saved
    # pending_open before the safety block stops the rest of the cycle.
    pre_safety = get_state_safety()
    if (
        get_pending_open() is not None
        and pre_safety.get("reason_code")
        in {"ORDER_SEND_RESULT_UNKNOWN", "PARTIAL_OPEN_EXECUTION"}
    ):
        recovery_status = reconcile_pending_open(account)
        if recovery_status == "ACTIVATED":
            log_event("Ожидающее открытие восстановлено до проверки состояния")

    safety_status = reconcile_state_safety(symbol=symbol, account=account)
    if safety_status == "BLOCKED":
        return cycle_result(
            CycleStatus.STATE_SAFETY_BLOCKED,
            account=account,
            message="Защита состояния заблокировала новые сделки",
        )
    if safety_status == "RECOVERED":
        return cycle_result(
            CycleStatus.STATE_RECOVERED,
            account=account,
            message="Состояние восстановлено; новые сделки разрешатся со следующего цикла",
        )

    pending_status = reconcile_pending_open(account)
    if pending_status == "PENDING":
        return cycle_result(
            CycleStatus.PENDING_OPEN,
            account=account,
            message="Ожидается подтверждение позиции в MT5",
        )
    if pending_status == "BLOCKED":
        return cycle_result(
            CycleStatus.STATE_SAFETY_BLOCKED,
            account=account,
            message="Ожидающее открытие относится к другому счёту или не может быть восстановлено",
        )
    if pending_status == "CLEARED_NO_EVIDENCE":
        return cycle_result(
            CycleStatus.PENDING_OPEN_RECOVERED,
            account=account,
            message="Устаревшее ожидающее открытие удалено: признаков исполнения в MT5 нет",
        )

    active_trade = get_active_trade()
    if active_trade is not None:
        reconciliation = reconcile_active_trade(symbol=symbol, account=account)
        if reconciliation in {
            "POSITION_ANOMALY",
            "CLOSE_HISTORY_ANOMALY",
        }:
            return cycle_result(
                CycleStatus.EXECUTION_ANOMALY,
                account=account,
                message=translate_runtime_status(reconciliation),
            )
        if reconciliation in {"WAITING_HISTORY", "NOTIFICATION_PENDING"}:
            return cycle_result(
                CycleStatus.POSITION_RECONCILING,
                account=account,
                message=translate_runtime_status(reconciliation),
            )
        if reconciliation in {"CLOSED", "CLOSED_ALREADY_PROCESSED"}:
            return cycle_result(
                CycleStatus.POSITION_RECONCILING,
                account=account,
                message=translate_runtime_status(reconciliation),
            )

    active_trade = get_active_trade()
    any_position = (
        get_open_position(symbol=symbol, trade=active_trade)
        if active_trade is not None
        else get_any_open_position(symbol)
    )

    if active_trade is None and any_position is not None:
        handle_position_without_state(any_position)
        return cycle_result(
            CycleStatus.POSITION_WITHOUT_STATE,
            account=account,
            message=f"В MT5 есть позиция {any_position.ticket}, отсутствующая в состоянии робота",
        )

    if issue_code == "QUOTES_UNAVAILABLE":
        return cycle_result(
            CycleStatus.QUOTES_UNAVAILABLE,
            account=account,
            healthy=False,
            mt5_available=True,
            terminal_available=True,
            account_available=True,
            quotes_available=False,
            trade_allowed=bool(mt5_health.get("trade_allowed", False)),
            mt5_issue_code=issue_code,
            message=str(mt5_health.get("message") or "Котировки недоступны"),
        )

    if issue_code == "TRADE_DISABLED":
        return cycle_result(
            CycleStatus.TRADE_DISABLED,
            account=account,
            healthy=False,
            mt5_available=True,
            terminal_available=True,
            account_available=True,
            quotes_available=True,
            trade_allowed=False,
            mt5_issue_code=issue_code,
            message=str(mt5_health.get("message") or "Автоматическая торговля отключена"),
        )

    daily_realized_result = get_today_realized_trade_result(symbol)
    daily_guard = update_daily_guard(
        account_balance=float(account.balance),
        account_equity=float(account.equity),
        account_profit=float(account.profit),
        daily_closed_profit=daily_realized_result,
        soft_stop_percent=daily_soft_stop_percent,
        hard_stop_percent=daily_hard_stop_percent,
        account_login=int(account.login),
        server=str(account.server),
    )
    account_guard = update_account_guard(
        account_balance=float(account.balance),
        account_equity=float(account.equity),
        account_login=int(account.login),
        server=str(account.server),
        max_drawdown_percent=account_guard_percent,
        pause_days=account_guard_pause_days,
    )

    if is_account_blocked(account_guard):
        _send_account_guard_notification(account_guard, account_guard_percent)
        if get_active_trade() is not None and get_open_position(symbol, get_active_trade()) is not None:
            close_position(symbol=symbol, reason="ACCOUNT_GUARD")
        return cycle_result(
            CycleStatus.ACCOUNT_GUARD_BLOCKED,
            account=account,
            message="Защита счёта активна",
        )

    market_data = load_market_data(symbol)
    h4 = add_indicators(market_data["h4"], settings)
    h1 = add_indicators(market_data["h1"], settings)
    m15 = add_indicators(market_data["m15"], settings)

    current_candle_time = str(m15.iloc[-1]["time"])
    last_processed = get_last_m15_candle()
    active_trade = get_active_trade()

    if active_trade is not None:
        update_active_trade_cache(symbol, account)

    if is_hard_stop_triggered(daily_guard):
        _send_daily_guard_notification(daily_guard, "HARD STOP")
        if active_trade is not None and get_open_position(symbol, active_trade) is not None:
            close_position(symbol=symbol, reason="DAILY_HARD_STOP")
        save_last_m15_candle(current_candle_time)
        return cycle_result(
            CycleStatus.DAILY_HARD_STOP,
            account=account,
            message="Активна жёсткая дневная блокировка",
        )

    if active_trade is not None:
        manage_status = manage_open_position(
            symbol=symbol,
            m15=m15,
            settings=settings,
            current_candle_time=current_candle_time,
        )
        save_last_m15_candle(current_candle_time)
        return cycle_result(
            CycleStatus.POSITION_MANAGED,
            account=account,
            message=translate_runtime_status(manage_status),
        )

    if get_pending_open() is not None:
        return cycle_result(
            CycleStatus.PENDING_OPEN,
            account=account,
            message="Ожидающее открытие блокирует новые сделки",
        )

    if current_candle_time == last_processed:
        return cycle_result(CycleStatus.NO_NEW_CANDLE, account=account)

    log_event(f"НОВАЯ СВЕЧА M15: {current_candle_time}")

    if is_trading_blocked(daily_guard):
        save_last_m15_candle(current_candle_time)
        _send_daily_guard_notification(daily_guard, "SOFT STOP")
        return cycle_result(
            CycleStatus.DAILY_SOFT_STOP,
            account=account,
            message="Мягкая дневная защита блокирует новые сделки",
        )

    signal = generate_signal(h4=h4, h1=h1, m15=m15, settings=settings)
    save_last_signal({
        "time": current_candle_time,
        "symbol": symbol,
        "action": signal.action.value,
        "score": signal.score,
        "buy_score": signal.buy_score,
        "sell_score": signal.sell_score,
        "reasons": signal.reasons,
    })

    translated_reasons = translate_signal_reasons(signal.reasons)
    log_event(f"Действие сигнала: {translate_direction(signal.action.value)}")
    log_event(f"Оценка сигнала: {signal.score}")
    log_event(f"Причины сигнала: {translated_reasons}")

    if signal.action == SignalAction.WAIT:
        save_last_m15_candle(current_candle_time)
        return cycle_result(CycleStatus.WAIT_SIGNAL, account=account)

    if signal.entry_price is None or signal.stop_loss is None:
        save_last_m15_candle(current_candle_time)
        log_event("Некорректный сигнал: отсутствует цена входа или стоп-лосс")
        return cycle_result(
            CycleStatus.SIGNAL_REJECTED,
            account=account,
            message="В сигнале отсутствует цена входа или стоп-лосс",
        )

    volume = calculate_position_size(
        balance=float(account.balance),
        risk_percent=risk_percent,
        entry_price=float(signal.entry_price),
        stop_loss=float(signal.stop_loss),
        symbol=symbol,
    )

    if volume <= 0:
        save_last_m15_candle(current_candle_time)
        log_event("Не удалось рассчитать объём: риск слишком мал для минимального лота или данные инструмента недоступны")
        return cycle_result(
            CycleStatus.SIGNAL_REJECTED,
            account=account,
            message="Рассчитанный объём позиции равен нулю",
        )

    pending_open = _build_pending_open(signal, symbol, volume, account, current_candle_time)
    save_pending_open(pending_open)

    try:
        result = send_market_order(
            symbol=symbol,
            direction=signal.action.value,
            volume=volume,
            stop_loss=float(signal.stop_loss),
            comment=str(pending_open.get("order_comment") or "mt5_xau_bot"),
        )
    except OrderRejectedError as error:
        clear_pending_open()
        save_last_m15_candle(current_candle_time)
        log_event(f"Заявка на открытие отклонена: {error}")
        return cycle_result(
            CycleStatus.SIGNAL_REJECTED,
            account=account,
            message=str(error),
        )
    except OrderPreparationError as error:
        clear_pending_open()
        save_last_m15_candle(current_candle_time)
        log_event(f"Заявка не отправлена: {error}")
        return cycle_result(
            CycleStatus.SIGNAL_REJECTED,
            account=account,
            message=str(error),
        )
    except (PartialExecutionError, OrderSendUncertainError) as error:
        result_object = getattr(error, "result", None)
        pending_open["status"] = (
            "partial_execution_anomaly"
            if isinstance(error, PartialExecutionError)
            else "order_send_result_unknown"
        )
        pending_open["execution_error"] = str(error)
        pending_open["execution_retcode"] = int(
            getattr(result_object, "retcode", 0) or 0
        )
        pending_open["order_ticket"] = int(
            getattr(result_object, "order", 0) or 0
        )
        pending_open["deal_ticket"] = int(
            getattr(result_object, "deal", 0) or 0
        )
        pending_open["executed_volume"] = float(
            getattr(result_object, "volume", 0.0) or 0.0
        )
        pending_open["sent_at"] = datetime.now(timezone.utc).isoformat()
        save_pending_open(pending_open)

        reason_code = (
            "PARTIAL_OPEN_EXECUTION"
            if isinstance(error, PartialExecutionError)
            else "ORDER_SEND_RESULT_UNKNOWN"
        )
        title = (
            "Аномалия открытия позиции"
            if isinstance(error, PartialExecutionError)
            else "Неизвестный результат открытия"
        )
        message = (
            f"Открытие {symbol} не может считаться безопасно завершённым: {error}. "
            "Новые сделки заблокированы. Робот проверит MT5, но не будет отправлять "
            "повторный ордер по этой свече."
        )
        _activate_execution_safety(
            reason_code,
            title,
            message,
            {
                "symbol": symbol,
                "direction": signal.action.value,
                "requested_volume": volume,
                "executed_volume": pending_open.get("executed_volume"),
                "order_ticket": pending_open.get("order_ticket"),
                "deal_ticket": pending_open.get("deal_ticket"),
                "retcode": pending_open.get("execution_retcode"),
            },
        )
        save_last_m15_candle(current_candle_time)
        return cycle_result(
            CycleStatus.EXECUTION_ANOMALY,
            account=account,
            message=message,
        )

    pending_open["status"] = "awaiting_position"
    pending_open["sent_at"] = datetime.now(timezone.utc).isoformat()
    pending_open["order_ticket"] = result["order_ticket"]
    pending_open["deal_ticket"] = result["deal_ticket"]
    pending_open["executed_price"] = result["executed_price"]
    pending_open["executed_volume"] = result["executed_volume"]
    save_pending_open(pending_open)

    confirmation_timeout = int(
        load_monitoring_config().get("open_confirmation_timeout_seconds", 10)
    )
    position = wait_for_open_position(pending_open, timeout_seconds=confirmation_timeout)

    if position is not None:
        reconcile_pending_open(account)
        status = CycleStatus.TRADE_OPENED
        message = f"Позиция подтверждена: ticket={position.ticket}"
    else:
        log_event(
            f"Заявка принята, но подтверждение позиции ещё ожидается: "
            f"ордер={result['order_ticket']}, торговая_операция={result['deal_ticket']}"
        )
        status = CycleStatus.PENDING_OPEN
        message = "Заявка принята; ожидается подтверждение позиции"

    save_last_m15_candle(current_candle_time)
    return cycle_result(status, account=account, message=message)


if __name__ == "__main__":
    main()
