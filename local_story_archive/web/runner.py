import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# REL-02 / REL-03 / D-11: module-level caps. Tests monkeypatch these on the
# `runner` module object (e.g. monkeypatch.setattr(runner, "_MAX_JOBS", 5))
# but cannot affect already-instantiated Job objects' deque maxlen — see
# the Job dataclass note below.
_MAX_EVENTS_PER_JOB = 1000
_MAX_JOBS = 50


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, Any]
    seq: int = 0  # REL-02 / D-07: assigned by Job.emit() under Job._lock
    timestamp: float = field(default_factory=time.time)


@dataclass
class Job:
    job_id: str
    kind: str
    args: dict[str, Any]
    status: JobStatus = JobStatus.pending
    # REL-02 / D-06: bounded ring buffer. deque.append is atomic under GIL
    # and evicts from the left when maxlen is reached. The default_factory
    # captures _MAX_EVENTS_PER_JOB at lambda-call time (each Job
    # construction), so monkeypatching _MAX_EVENTS_PER_JOB before
    # constructing a fresh Job picks up the new cap.
    events: deque[ProgressEvent] = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB))
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    # REL-02 / D-07: monotonic per-Job sequence counter. Public name (no
    # underscore) so Jinja2 templates can render it as `job.next_seq`
    # without reaching for a leading-underscore "private" attr.
    next_seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.next_seq += 1
            self.events.append(ProgressEvent(kind=kind, data=data or {}, seq=self.next_seq))
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

    def snapshot_events(self, after_seq: int = 0) -> list[ProgressEvent]:
        """Return events whose seq > after_seq (atomic snapshot).

        D-08: index-based semantics are abandoned because deque eviction
        shifts indices invisibly. seq survives eviction — clients pass
        the highest seq they've already consumed.
        """
        with self._lock:
            return [e for e in self.events if e.seq > after_seq]

    def oldest_seq(self) -> int:
        """Return the seq of the leftmost event still in the deque.

        Returns 0 if the deque is empty. Used by the SSE handler
        (Plan 05) to detect eviction gaps: if after_seq + 1 < oldest_seq,
        the client has missed events and the handler emits a synthetic
        `events.evicted` event ahead of the snapshot (D-10).
        """
        with self._lock:
            return self.events[0].seq if self.events else 0


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
            # REL-03 / D-13: insert first so the new job is never a prune
            # candidate (its status is pending; the predicate below skips
            # it). Then prune oldest done/failed until at-or-under cap.
            # Running jobs are pinned — better to overshoot the cap than
            # to orphan a JobRunner thread that's still emitting events.
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            if len(self._jobs) > _MAX_JOBS:
                survivors: list[str] = []
                for jid in self._order:
                    j = self._jobs.get(jid)
                    if j is None:
                        continue
                    # Evict only if (a) we still need to shrink AND
                    # (b) the job is in a terminal state.
                    if len(self._jobs) > _MAX_JOBS and j.status in (
                        JobStatus.done,
                        JobStatus.failed,
                    ):
                        del self._jobs[jid]
                    else:
                        survivors.append(jid)
                self._order = survivors
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
