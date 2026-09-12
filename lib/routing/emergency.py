"""Atomic project-local accounting for emergency dispatch authorization."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Serialize state updates across threads and processes on the host."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


class EmergencyDispatchState:
    """Persist a bounded dispatch count keyed by an opaque task identity."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @staticmethod
    def _key(task_id: str) -> str:
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest()

    def try_claim(self, task_id: str, limit: int) -> bool:
        if limit <= 0:
            return False
        with _exclusive_lock(self.lock_path):
            state = self._read()
            if state is None:
                # Corrupt or structurally invalid state must not reset a spent
                # emergency budget. Fail closed until an operator repairs it.
                return False
            tasks = state["tasks"]
            key = self._key(task_id)
            current = tasks.get(key, {"dispatches": 0})["dispatches"]
            if current >= limit:
                return False
            tasks[key] = {"dispatches": current + 1}
            self._write(state)
            return True

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return {"version": 1, "tasks": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, Mapping) or value.get("version") != 1:
            return None
        tasks = value.get("tasks")
        if not isinstance(tasks, Mapping):
            return None
        normalized: dict[str, dict[str, int]] = {}
        for key, record in tasks.items():
            if (
                not isinstance(key, str)
                or len(key) != 64
                or any(character not in "0123456789abcdef" for character in key)
                or not isinstance(record, Mapping)
                or set(record) != {"dispatches"}
                or not isinstance(record.get("dispatches"), int)
                or isinstance(record.get("dispatches"), bool)
                or record["dispatches"] < 0
            ):
                return None
            normalized[key] = {"dispatches": record["dispatches"]}
        return {"version": 1, "tasks": normalized}

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent,
        )
        temporary_path = Path(temporary)
        try:
            with open(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


__all__ = ["EmergencyDispatchState"]
