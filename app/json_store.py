import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


_json_lock = threading.RLock()


def clone_default(default: Any) -> Any:
    return copy.deepcopy(default)


def load_json_file_detailed(path: Path) -> dict:
    """Read JSON and preserve the reason when it cannot be loaded.

    Returned mapping:
      status: "ok" | "missing" | "error"
      data: parsed object or None
      error: diagnostic string or None
    """
    if not path.exists():
        return {"status": "missing", "data": None, "error": None}

    try:
        with _json_lock:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
        return {
            "status": "error",
            "data": None,
            "error": f"{type(error).__name__}: {error}",
        }

    return {"status": "ok", "data": data, "error": None}


def load_json_file(path: Path, default: Any) -> Any:
    result = load_json_file_detailed(path)
    if result["status"] != "ok":
        return clone_default(default)
    return result["data"]


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with _json_lock:
        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
                temp_path = Path(file.name)

            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
