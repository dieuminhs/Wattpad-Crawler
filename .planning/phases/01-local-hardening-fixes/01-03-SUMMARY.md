---
phase: 01-local-hardening-fixes
plan: 03
subsystem: web/runner + web/routes (job lifecycle and SSE progress)
tags: [memory, jobs, deque, pruning, sse, seq-cursor]
requirements_completed: [REL-02, REL-03]
dependency_graph:
  requires:
    - "Existing local_story_archive.web.runner module (Job/ProgressEvent/JobManager/JobRunner classes)"
    - "Existing local_story_archive.web.routes:job_stream SSE handler (the only consumer of snapshot_events)"
  provides:
    - "Bounded Job.events deque (maxlen=_MAX_EVENTS_PER_JOB=1000) — REL-02"
    - "Monotonic Job.next_seq counter and ProgressEvent.seq field — REL-02 (D-07)"
    - "Job.snapshot_events(after_seq=N) — returns events with seq > N — REL-02 (D-08)"
    - "Job.oldest_seq() — returns leftmost surviving seq, or 0 if deque empty — REL-02"
    - "JobManager._MAX_JOBS=50 cap with insert-then-prune ordering; running/pending pinned — REL-03 (D-13)"
    - "/jobs/{id}/stream?after_seq=N SSE endpoint with synthetic events.evicted on gap — D-09, D-10"
    - "events.evicted payload shape: {dropped_count, requested_after_seq, oldest_available_seq}"
    - "Per-stream gap_announced latch so events.evicted fires at most once per SSE connection"
    - "Each real SSE event JSON includes seq alongside kind, data, ts"
  affects:
    - "local_story_archive/web/templates/job.html — still emits ?after={{ job.events|length }}; Plan 05 fixes (graceful: FastAPI ignores unknown param, replays from seq 0)"
tech_stack:
  added:
    - "collections.deque (stdlib) — ring buffer for Job.events"
  patterns:
    - "Insert-then-prune ordering inside JobManager._lock (RESEARCH Pitfall 5)"
    - "default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB) — captures module constant at Job-construction time so monkeypatch works"
    - "Per-job monotonic seq counter incremented under Job._lock alongside deque.append"
    - "Per-stream eviction-warning latch (function-local bool inside async generator)"
key_files:
  created:
    - ".planning/phases/01-local-hardening-fixes/01-03-SUMMARY.md"
  modified:
    - "local_story_archive/web/runner.py (61 insertions, 5 deletions — commit 608d640)"
    - "local_story_archive/web/routes.py (61 insertions, 12 deletions — commit edbad03)"
    - "tests/unit/test_runner.py (210 insertions, 1 deletion — commit 63b58c4)"
decisions:
  - "Job.next_seq is PUBLIC (no leading underscore) — Jinja2 templates in Plan 05 access it as job.next_seq; RESEARCH Open Question #3 RESOLVED"
  - "snapshot_events keyword renamed in-place: after_index removed entirely, replaced by after_seq (clean break, not additive — D-08)"
  - "Plan 03 ships routes.py:job_stream alongside runner.py to avoid an interim wave where /jobs/{id}/stream returns 500 (TypeError on after_index keyword); merged from former Plan 05 Task 1 per checker recommendation"
  - "events.evicted gap announcement is per-stream (function-local gap_announced bool), NOT persisted in Job — reconnection re-evaluates by design"
  - "Cap overshoot is accepted when all jobs are pending/running (T-03-05) — better than orphaning a JobRunner thread that's still emitting events"
metrics:
  tasks_completed: 3
  tasks_total: 3
  duration_minutes: ~10
  completed_date: "2026-05-03T06:12:00Z"
  files_changed: 3
  tests_added: 19
  tests_passing: 31
---

# Phase 01 Plan 03: Bound Job.events deque, prune JobManager, wire SSE cursor to seq Summary

Bounded `Job.events` to a 1000-element `collections.deque` and `JobManager._jobs` to 50 entries, added a monotonic `Job.next_seq` counter so SSE consumers survive eviction via a stable cursor, and updated `/jobs/{id}/stream` to accept `after_seq` and announce evicted-event gaps via a synthetic `events.evicted` payload — all in a single atomic plan so the SSE endpoint is never broken between waves.

## What Shipped

### `local_story_archive/web/runner.py` — final dataclass shape

```python
_MAX_EVENTS_PER_JOB = 1000   # module-level cap (REL-02 / D-11)
_MAX_JOBS = 50               # module-level cap (REL-03 / D-11)


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, Any]
    seq: int = 0                                       # NEW (D-07); assigned by Job.emit
    timestamp: float = field(default_factory=time.time)


@dataclass
class Job:
    job_id: str
    kind: str
    args: dict[str, Any]
    status: JobStatus = JobStatus.pending
    events: deque[ProgressEvent] = field(              # CHANGED: list -> deque (D-06)
        default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB)
    )
    error: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    next_seq: int = 0                                  # NEW: PUBLIC (no underscore) for Jinja2
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, repr=False)

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.next_seq += 1
            self.events.append(
                ProgressEvent(kind=kind, data=data or {}, seq=self.next_seq)
            )
            self._wake.set()

    def snapshot_events(self, after_seq: int = 0) -> list[ProgressEvent]:
        """Return events whose seq > after_seq (atomic snapshot)."""
        with self._lock:
            return [e for e in self.events if e.seq > after_seq]

    def oldest_seq(self) -> int:
        """Return seq of leftmost event still in deque, or 0 if empty."""
        with self._lock:
            return self.events[0].seq if self.events else 0
```

### `JobManager.create` — exact pruning loop body

```python
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
```

Key invariants verified by tests:
- New (pending) job is never a prune candidate (insert-then-prune ordering)
- Pending and running jobs are pinned; only `done` and `failed` are evictable
- `_order` list stays consistent with `_jobs` dict after pruning
- 60 jobs all done -> `_jobs` caps at exactly 50
- Submitting 5 pending jobs with cap=3 keeps all 5 (overshoot is accepted)

### `local_story_archive/web/routes.py:job_stream` — exact final body

```python
@router.get("/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str, after_seq: int = 0):
    """Server-Sent Events stream of job progress.

    D-09: query parameter is `after_seq` (the highest seq the client has
    already consumed). D-10: if events between after_seq and the oldest
    surviving seq have been evicted from the deque (REL-02 cap), emit a
    synthetic `events.evicted` event ahead of the snapshot so the UI
    knows older events were dropped to save memory.
    """
    mgr = request.app.state.job_manager
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        import asyncio
        import time as _time

        last_seq = after_seq
        # Per-stream eviction-warning latch (RESEARCH Open Question #2 — RESOLVED).
        # Reconnection creates a fresh event_gen and re-evaluates the gap by
        # design; within one SSE connection we announce at most once.
        gap_announced = False

        while True:
            if await request.is_disconnected():
                break

            # On first poll only, check whether the client's cursor has
            # been evicted from the deque. If so, emit a synthetic
            # events.evicted event ahead of the regular snapshot.
            if not gap_announced:
                oldest = job.oldest_seq()
                if oldest and last_seq + 1 < oldest:
                    dropped = oldest - 1 - last_seq
                    yield {
                        "data": json.dumps(
                            {
                                "kind": "events.evicted",
                                "data": {
                                    "dropped_count": dropped,
                                    "requested_after_seq": after_seq,
                                    "oldest_available_seq": oldest,
                                },
                                "ts": _time.time(),
                            }
                        )
                    }
                gap_announced = True

            new_events = job.snapshot_events(last_seq)
            for ev in new_events:
                last_seq = ev.seq
                yield {
                    "data": json.dumps(
                        {
                            "kind": ev.kind,
                            "data": ev.data,
                            "seq": ev.seq,
                            "ts": ev.timestamp,
                        }
                    )
                }

            if job.status.value in ("done", "failed"):
                yield {
                    "data": json.dumps(
                        {
                            "kind": "__status__",
                            "data": {"status": job.status.value, "error": job.error},
                        }
                    )
                }
                return
            # 250ms polling — fine for personal-use UI; threading.Event-to-asyncio
            # bridge is fiddly and not worth the complexity for this scope.
            await asyncio.sleep(0.25)

    return EventSourceResponse(event_gen())
```

Behavior verified by smoke test:
- After client requests `?after_seq=0` against a job with events 1..10 evicted to oldest=6: first SSE message is `{"kind": "events.evicted", "data": {"dropped_count": 5, "requested_after_seq": 0, "oldest_available_seq": 6}, "ts": ...}`, followed by real events with `seq` 6..10 in payload, then `__status__` terminator
- Status code is 200; no TypeError raised; renamed parameter accepted cleanly

## Test Coverage

19 new tests in `tests/unit/test_runner.py`. All 31 tests in the file pass (12 pre-existing + 19 new). Test list:

REL-02 (event-cap, 11 tests):
- `test_progress_event_has_seq_field`
- `test_job_emit_assigns_monotonic_seq_starting_at_one`
- `test_job_events_is_a_deque_with_default_maxlen`
- `test_job_events_deque_evicts_oldest_at_maxlen` (1100-event ROADMAP literal)
- `test_job_events_deque_with_patched_maxlen`
- `test_job_snapshot_events_filters_by_seq`
- `test_job_snapshot_events_default_returns_all`
- `test_job_snapshot_events_after_high_seq_returns_empty`
- `test_job_oldest_seq_empty_returns_zero`
- `test_job_oldest_seq_returns_leftmost_seq`
- `test_job_oldest_seq_after_eviction`

REL-03 (JobManager prune, 8 tests):
- `test_jobmanager_under_cap_does_not_prune`
- `test_jobmanager_prunes_oldest_done_jobs_at_cap`
- `test_jobmanager_pruning_preserves_running_jobs` (D-13)
- `test_jobmanager_evicts_failed_as_well_as_done`
- `test_jobmanager_overshoots_when_all_running` (D-13 explicit)
- `test_jobmanager_60_jobs_caps_at_50_when_done` (REL-03 ROADMAP literal)
- `test_jobmanager_just_created_job_is_never_pruned`
- `test_jobmanager_order_list_consistent_after_prune`

Pre-existing test migration: `tests/unit/test_runner.py:17` `assert job.events == []` migrated to `assert len(job.events) == 0` (deque() != []).

The 24 tests in `tests/unit/test_web_routes.py` continue to pass — no regressions in the unchanged route handlers.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Lint] Ruff E402 module-level imports**
- **Found during:** Task 2 (test_runner.py append)
- **Issue:** New tests required `from collections import deque` and `from local_story_archive.web import runner` placed AFTER the existing function bodies (mid-file imports). `ruff check` raised E402 for both.
- **Fix:** Moved both imports to the top of `tests/unit/test_runner.py` alongside the existing imports, removed the duplicates from below the test bodies. Functionality unchanged; just satisfies project ruff rules.
- **Files modified:** `tests/unit/test_runner.py`
- **Commit:** Folded into 63b58c4 before commit.

**2. [Rule 1 - Format] Ruff format auto-reformat**
- **Found during:** Task 1 + Task 3
- **Issue:** Both `runner.py` and `routes.py` failed `ruff format --check` after my Write/Edit (line wrapping differences from the plan's literal text).
- **Fix:** Ran `ruff format` on both files. The reformat is cosmetic only — same logic, fewer line breaks. Routes.py picked up one extra blank line after `from local_story_archive.config import load_config` inside `setup_post` (unchanged function, ruff just normalized whitespace).
- **Files modified:** `local_story_archive/web/runner.py`, `local_story_archive/web/routes.py`
- **Commit:** Folded into 608d640 and edbad03 respectively.

No architectural deviations. No checkpoint or auth gates encountered. No deferred items.

## Pre-existing Behavior Preserved

- `JobStatus`, `JobRunner`, `new_job_id`, `JobManager.get`, `JobManager.list_jobs` — byte-identical
- 14 of 15 functions in `routes.py` — functionally identical (one whitespace-only ruff change in `setup_post`)
- All 12 pre-existing tests in `test_runner.py` continue to pass after the one-line `assert job.events == []` migration
- All 24 tests in `test_web_routes.py` continue to pass (SSE replay test still works because the test client passes `?after_seq=0` semantics implicitly via the default)

## Threat Mitigations Applied

| Threat ID | Mitigation Verified |
|-----------|---------------------|
| T-03-01 (memory exhaustion via Job.events growth) | `test_job_events_deque_evicts_oldest_at_maxlen` — 1100 emits leave exactly 1000 events |
| T-03-02 (memory exhaustion via JobManager._jobs growth) | `test_jobmanager_60_jobs_caps_at_50_when_done` — 60 done jobs leave exactly 50 |
| T-03-03 (deque mutation race) | `test_job_emit_is_thread_safe` (pre-existing, still passes); deque.append + iteration both wrapped in `Job._lock` |
| T-03-04 (just-created job evicted by its own create) | `test_jobmanager_just_created_job_is_never_pruned` — cap=1, j2.create evicts j1(done) but j2(pending) survives |
| T-03-05 (cap overshoots when all running) | `test_jobmanager_overshoots_when_all_running` — cap=3, 5 pending jobs all retained |
| T-03-06 (silent SSE event loss across reconnect) | Smoke-tested: synthetic events.evicted emitted on first poll when last_seq+1 < oldest |

T-03-07 (malformed `?after_seq=` returns 422 not 500) is FastAPI's default behavior — accepted.

## Plan 05 Hand-off

Plan 05 (Wave 2) consumes the contracts ratified here:
- `job.next_seq` is publicly readable from Jinja2 (`templates/job.html` will emit `?after_seq={{ job.next_seq }}`)
- `job.oldest_seq()` callable; returns 0 for empty Job
- `/jobs/{id}/stream?after_seq=N` is the canonical query format
- Real-event SSE JSON includes `seq` field for client-side cursor tracking
- Synthetic `events.evicted` event shape stable for client-side UI handling

The interim window between Plan 03 and Plan 05 shipping is graceful: the unchanged template emits `?after=<int>`, FastAPI ignores the unknown query param, `after_seq` defaults to 0, and the SSE stream replays from seq 0 (redundant but functional — no 500).

## Self-Check: PASSED

- [x] `local_story_archive/web/runner.py` exists at HEAD — commit 608d640
- [x] `local_story_archive/web/routes.py` exists at HEAD — commit edbad03
- [x] `tests/unit/test_runner.py` exists at HEAD — commit 63b58c4
- [x] `.planning/phases/01-local-hardening-fixes/01-03-SUMMARY.md` exists (this file)
- [x] `608d640` in git log — verified (`feat(01-03): bound Job.events deque...`)
- [x] `63b58c4` in git log — verified (`test(01-03): add 19 tests for Job.events deque...`)
- [x] `edbad03` in git log — verified (`feat(01-03): SSE handler accepts after_seq...`)
- [x] `pytest tests/unit/test_runner.py` — 31 passed
- [x] `pytest tests/unit/test_web_routes.py` — 24 passed (no regression)
- [x] `ruff check` — passes on all three modified files
- [x] No `_next_seq` substring remains in `local_story_archive/web/runner.py` or `routes.py` (verified by Grep)
- [x] No `after_index` substring remains in any of the three modified files (verified by Grep)
- [x] ROADMAP §Phase 1 success criterion #3 satisfied: 60 jobs caps at 50, 1100 events caps at 1000
