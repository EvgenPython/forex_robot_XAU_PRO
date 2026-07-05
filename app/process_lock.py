import atexit
import json
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


ROOT_DIR = Path(__file__).resolve().parent.parent
LOCKS_DIR = ROOT_DIR / "state"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessAlreadyRunningError(RuntimeError):
    def __init__(self, lock_name: str, record: dict[str, Any]):
        self.lock_name = lock_name
        self.record = record
        pid = record.get("pid", "неизвестен")
        started_at = record.get("acquired_at", "неизвестно")
        super().__init__(
            f"Процесс '{lock_name}' уже запущен. PID: {pid}; "
            f"lock создан: {started_at}"
        )


class ProcessLock:
    """Межпроцессная защита от двойного запуска.

    Lock создаётся атомарно через O_EXCL. После аварийного завершения оставшийся
    файл считается активным только тогда, когда PID существует и время создания
    процесса совпадает с сохранённым значением. Это защищает от повторного
    использования PID операционной системой.
    """

    def __init__(self, lock_name: str):
        safe_name = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_"
            for char in str(lock_name)
        )
        if not safe_name:
            raise ValueError("Имя process lock не может быть пустым")

        self.lock_name = safe_name
        self.path = LOCKS_DIR / f"{safe_name}.lock"
        self.token = str(uuid.uuid4())
        self.acquired = False
        self.record: dict[str, Any] = {}
        self._atexit_registered = False

    @staticmethod
    def _get_process_create_time(pid: int) -> float | None:
        try:
            return float(psutil.Process(pid).create_time())
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied:
            # На Windows доступ к некоторым данным процесса может быть ограничен.
            # Сам факт существования PID в этом случае считаем достаточным.
            return -1.0 if psutil.pid_exists(pid) else None

    @classmethod
    def _record_is_alive(cls, record: dict[str, Any]) -> bool:
        try:
            pid = int(record.get("pid", 0))
        except (TypeError, ValueError):
            return False

        if pid <= 0 or not psutil.pid_exists(pid):
            return False

        actual_create_time = cls._get_process_create_time(pid)
        if actual_create_time is None:
            return False
        if actual_create_time == -1.0:
            return True

        saved_create_time = record.get("process_create_time")
        if saved_create_time is None:
            # Старый lock без create_time: безопаснее считать живым существующий PID.
            return True

        try:
            return abs(actual_create_time - float(saved_create_time)) < 2.0
        except (TypeError, ValueError):
            return True

    def _read_existing_record(self) -> dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _remove_stale_lock(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise RuntimeError(
                f"Не удалось удалить устаревший lock-файл {self.path}: {error}"
            ) from error

    def acquire(self) -> "ProcessLock":
        if self.acquired:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)

        for _ in range(3):
            try:
                fd = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                existing = self._read_existing_record()
                if existing and self._record_is_alive(existing):
                    raise ProcessAlreadyRunningError(self.lock_name, existing)

                self._remove_stale_lock()
                continue

            pid = os.getpid()
            process_create_time = self._get_process_create_time(pid)
            self.record = {
                "lock_name": self.lock_name,
                "token": self.token,
                "pid": pid,
                "process_create_time": process_create_time,
                "acquired_at": utc_now_iso(),
                "hostname": socket.gethostname(),
                "python_executable": sys.executable,
                "command": sys.argv,
            }

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(self.record, file, indent=4, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
            except Exception:
                self._remove_stale_lock()
                raise

            self.acquired = True
            if not self._atexit_registered:
                atexit.register(self.release)
                self._atexit_registered = True
            return self

        raise RuntimeError(f"Не удалось получить process lock: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return

        existing = self._read_existing_record()
        if existing.get("token") == self.token:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

        self.acquired = False

    def __enter__(self) -> "ProcessLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
