import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class Job:
    job_id: str
    kind: str
    args: dict[str, Any]
    status: JobStatus = JobStatus.pending
    events: list[ProgressEvent] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append(ProgressEvent(kind=kind, data=data or {}))
            self._wake.set()

    def set_running(self) -> None:
        with self._lock:
            self.status = JobStatus.running
            self.started_at = time.time()
            self._wake.set()

    def set_done(self) -> None:
        with self._lock:
            self.status = JobStatus.done
            self.ended_at = time.time()
            self._wake.set()

    def set_failed(self, error: str) -> None:
        with self._lock:
            self.status = JobStatus.failed
            self.error = error
            self.ended_at = time.time()
            self._wake.set()

    def snapshot_events(self, after_index: int = 0) -> list[ProgressEvent]:
        """Return events after the given index (atomic snapshot)."""
        with self._lock:
            return list(self.events[after_index:])


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]
