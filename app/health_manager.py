import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.json_store import atomic_write_json, load_json_file
from app.models import CycleResult


ROOT_DIR = Path(__file__).resolve().parent.parent
HEARTBEAT_FILE = ROOT_DIR / "state" / "heartbeat.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class HealthManager:
    """Единое потокобезопасное состояние здоровья run_bot.py.

    Один экземпляр используется одновременно торговым циклом и Telegram-потоком.
    Все изменения и запись heartbeat защищены RLock, поэтому один поток не может
    затереть поля, которые только что обновил другой поток.
    """

    def __init__(self, symbol: str):
        now = utc_now_iso()
        self._lock = threading.RLock()
        self.instance_id = str(uuid.uuid4())
        self.data: dict[str, Any] = {
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "started_at": now,
            "last_loop_started_at": None,
            "last_loop_completed_at": None,
            "last_successful_cycle_at": None,
            "last_error_at": None,
            "last_error": None,
            "consecutive_errors": 0,
            "status": "starting",
            "cycle_message": "",
            "symbol": symbol,
            "account_login": None,
            "server": None,
            "mt5_available": None,
            "mt5_unavailable_since": None,
            "terminal_available": None,
            "terminal_unavailable_since": None,
            "account_available": None,
            "account_unavailable_since": None,
            "quotes_available": None,
            "quotes_unavailable_since": None,
            "trade_allowed": None,
            "trade_disabled_since": None,
            "mt5_issue_code": None,
            "telegram_polling_enabled": None,
            "telegram_polling_status": "not_configured",
            "telegram_polling_started_at": None,
            "telegram_last_heartbeat_at": None,
            "telegram_last_update_at": None,
            "telegram_last_state_change_at": now,
            "telegram_last_error_at": None,
            "telegram_last_error": None,
            "telegram_consecutive_errors": 0,
            "telegram_restart_count": 0,
            "telegram_next_restart_at": None,
            "telegram_unavailable_since": None,
        }
        self._write_locked()

    def _write_locked(self) -> None:
        atomic_write_json(HEARTBEAT_FILE, self.data)

    def _update(self, **changes: Any) -> None:
        with self._lock:
            self.data.update(changes)
            self._write_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.data)

    def mark_loop_started(self) -> None:
        self._update(
            last_loop_started_at=utc_now_iso(),
            status="loop_running",
        )

    def mark_cycle_result(self, result: CycleResult) -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["last_loop_completed_at"] = now
            self.data["status"] = result.status_value
            self.data["cycle_message"] = result.message
            self.data["consecutive_errors"] = 0
            self.data["last_error"] = None
            if result.account_login is not None:
                self.data["account_login"] = result.account_login
            if result.server is not None:
                self.data["server"] = result.server
            self.data["mt5_available"] = result.mt5_available
            self.data["terminal_available"] = result.terminal_available
            self.data["account_available"] = result.account_available
            self.data["quotes_available"] = result.quotes_available
            self.data["trade_allowed"] = result.trade_allowed
            self.data["mt5_issue_code"] = result.mt5_issue_code

            if result.healthy:
                self.data["last_successful_cycle_at"] = now

            if result.mt5_available:
                self.data["mt5_unavailable_since"] = None
            elif self.data.get("mt5_unavailable_since") is None:
                self.data["mt5_unavailable_since"] = now

            if result.terminal_available:
                self.data["terminal_unavailable_since"] = None
            elif self.data.get("terminal_unavailable_since") is None:
                self.data["terminal_unavailable_since"] = now

            if result.account_available:
                self.data["account_unavailable_since"] = None
            elif self.data.get("account_unavailable_since") is None:
                self.data["account_unavailable_since"] = now

            if result.quotes_available:
                self.data["quotes_unavailable_since"] = None
            elif self.data.get("quotes_unavailable_since") is None:
                self.data["quotes_unavailable_since"] = now

            if result.trade_allowed:
                self.data["trade_disabled_since"] = None
            elif self.data.get("trade_disabled_since") is None:
                self.data["trade_disabled_since"] = now

            self._write_locked()

    def mark_cycle_error(self, error: BaseException) -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["last_loop_completed_at"] = now
            self.data["last_error_at"] = now
            self.data["last_error"] = f"{type(error).__name__}: {error}"[:1500]
            self.data["consecutive_errors"] = (
                int(self.data.get("consecutive_errors", 0) or 0) + 1
            )
            self.data["status"] = "cycle_error"
            self.data["cycle_message"] = self.data["last_error"]
            self._write_locked()

    def configure_telegram(self, enabled: bool) -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["telegram_polling_enabled"] = bool(enabled)
            self.data["telegram_last_state_change_at"] = now
            if enabled:
                self.data["telegram_polling_status"] = "configured"
            else:
                self.data["telegram_polling_status"] = "disabled"
                self.data["telegram_unavailable_since"] = None
                self.data["telegram_next_restart_at"] = None
            self._write_locked()

    def mark_telegram_starting(self) -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["telegram_polling_status"] = "starting"
            self.data["telegram_last_state_change_at"] = now
            if self.data.get("telegram_unavailable_since") is None:
                self.data["telegram_unavailable_since"] = now
            self.data["telegram_next_restart_at"] = None
            self._write_locked()

    def mark_telegram_running(self) -> None:
        now = utc_now_iso()
        with self._lock:
            # getMe уже прошёл, но start_polling ещё может сразу завершиться
            # конфликтом или сетевой ошибкой. Поэтому счётчик ошибок и
            # unavailable_since сбрасываются только после стабильного pulse.
            self.data["telegram_polling_status"] = "running"
            self.data["telegram_polling_started_at"] = now
            self.data["telegram_last_state_change_at"] = now
            self.data["telegram_next_restart_at"] = None
            self._write_locked()

    def mark_telegram_heartbeat(self) -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["telegram_last_heartbeat_at"] = now
            self.data["telegram_polling_status"] = "running"
            self.data["telegram_last_state_change_at"] = now
            self.data["telegram_last_error"] = None
            self.data["telegram_consecutive_errors"] = 0
            self.data["telegram_next_restart_at"] = None
            self.data["telegram_unavailable_since"] = None
            self._write_locked()

    def mark_telegram_update(self) -> None:
        self._update(telegram_last_update_at=utc_now_iso())

    def mark_telegram_error(self, error: BaseException) -> int:
        now = utc_now_iso()
        with self._lock:
            count = int(self.data.get("telegram_consecutive_errors", 0) or 0) + 1
            self.data["telegram_polling_status"] = "error"
            self.data["telegram_last_state_change_at"] = now
            self.data["telegram_last_error_at"] = now
            self.data["telegram_last_error"] = (
                f"{type(error).__name__}: {error}"[:1500]
            )
            self.data["telegram_consecutive_errors"] = count
            if self.data.get("telegram_unavailable_since") is None:
                self.data["telegram_unavailable_since"] = now
            self._write_locked()
            return count

    def mark_telegram_restarting(self, delay_seconds: int) -> None:
        now_dt = utc_now()
        now = now_dt.isoformat()
        next_restart = (now_dt + timedelta(seconds=max(0, delay_seconds))).isoformat()
        with self._lock:
            self.data["telegram_polling_status"] = "restarting"
            self.data["telegram_last_state_change_at"] = now
            self.data["telegram_restart_count"] = (
                int(self.data.get("telegram_restart_count", 0) or 0) + 1
            )
            self.data["telegram_next_restart_at"] = next_restart
            if self.data.get("telegram_unavailable_since") is None:
                self.data["telegram_unavailable_since"] = now
            self._write_locked()

    def mark_telegram_stopped(self, reason: str = "") -> None:
        now = utc_now_iso()
        with self._lock:
            self.data["telegram_polling_status"] = "stopped"
            self.data["telegram_last_state_change_at"] = now
            if reason:
                self.data["telegram_last_error"] = str(reason)[:1500]
            if self.data.get("telegram_unavailable_since") is None:
                self.data["telegram_unavailable_since"] = now
            self._write_locked()


def load_heartbeat() -> dict:
    data = load_json_file(HEARTBEAT_FILE, {})
    return data if isinstance(data, dict) else {}
