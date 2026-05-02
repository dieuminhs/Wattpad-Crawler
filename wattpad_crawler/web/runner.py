import logging
import threading
import time
import uuid
from collections.abc import Callable
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


class JobManager:
    """In-memory registry of jobs. Thread-safe."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order, oldest first
        self._lock = threading.Lock()

    def create(self, kind: str, args: dict[str, Any]) -> Job:
        job = Job(job_id=new_job_id(), kind=kind, args=args)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        """Return jobs newest-first."""
        with self._lock:
            return [self._jobs[jid] for jid in reversed(self._order)]


logger = logging.getLogger(__name__)


JobWork = Callable[[Callable[[str, dict], None]], None]
"""A unit of work for JobRunner.submit. Receives an `emit(kind, data)` callable."""


class JobRunner:
    """Runs Job functions in background threads. One thread per job."""

    def __init__(self, manager: JobManager) -> None:
        self.manager = manager
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job: Job, work: JobWork) -> None:
        thread = threading.Thread(
            target=self._run, args=(job, work), name=f"job-{job.job_id}", daemon=True
        )
        with self._lock:
            self._running.add(job.job_id)
        thread.start()

    def _run(self, job: Job, work: JobWork) -> None:
        try:
            job.set_running()
            work(job.emit)
            job.set_done()
        except Exception as e:
            logger.exception("Job %s failed", job.job_id)
            job.set_failed(str(e))
        finally:
            with self._lock:
                self._running.discard(job.job_id)

    def running_count(self) -> int:
        """Return the number of currently running jobs."""
        with self._lock:
            return len(self._running)
