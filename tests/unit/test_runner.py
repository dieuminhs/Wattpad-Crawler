import threading
import time
from collections import deque

from local_story_archive.auth import AuthFailedError
from local_story_archive.web import runner
from local_story_archive.web.runner import Job, JobManager, JobRunner, JobStatus, ProgressEvent


def test_progress_event_holds_fields():
    e = ProgressEvent(kind="part.done", data={"part_id": "100"})
    assert e.kind == "part.done"
    assert e.data == {"part_id": "100"}
    assert e.timestamp > 0


def test_job_default_state():
    job = Job(job_id="j1", kind="archive_story", args={"story_id": "42"})
    assert job.status == JobStatus.pending
    # Plan 01-03: events is a deque, not a list — compare by length, not to []
    assert len(job.events) == 0
    assert job.error is None


def test_job_emit_appends_event():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.emit("part.start", {"part_id": "100"})
    assert len(job.events) == 1
    assert job.events[0].kind == "part.start"


def test_job_set_running_set_done():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.set_running()
    assert job.status == JobStatus.running
    job.set_done()
    assert job.status == JobStatus.done


def test_job_set_failed_records_error():
    job = Job(job_id="j1", kind="archive_story", args={})
    job.set_failed("something exploded")
    assert job.status == JobStatus.failed
    assert job.error == "something exploded"


def test_job_emit_is_thread_safe():
    """Concurrent emits from multiple threads must not lose events."""
    job = Job(job_id="j1", kind="archive_story", args={})

    def worker(n: int):
        for i in range(50):
            job.emit("tick", {"n": n, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert len(job.events) == 200
    seen = {(e.data["n"], e.data["i"]) for e in job.events}
    assert len(seen) == 200


def test_jobmanager_create_returns_job_with_unique_id():
    mgr = JobManager()
    j1 = mgr.create("archive_story", {"story_id": "1"})
    j2 = mgr.create("archive_story", {"story_id": "2"})
    assert j1.job_id != j2.job_id
    assert mgr.get(j1.job_id) is j1


def test_jobmanager_get_unknown_returns_none():
    mgr = JobManager()
    assert mgr.get("nope") is None


def test_jobmanager_list_returns_recent_first():
    mgr = JobManager()
    j1 = mgr.create("archive_story", {})
    j2 = mgr.create("archive_story", {})
    j3 = mgr.create("archive_story", {})
    listed = mgr.list_jobs()
    assert [j.job_id for j in listed] == [j3.job_id, j2.job_id, j1.job_id]


def test_jobrunner_runs_callable_in_thread_and_marks_done():
    mgr = JobManager()
    job = mgr.create("test", {})
    runner = JobRunner(mgr)

    def work(emit):
        emit("test.tick", {"n": 1})
        emit("test.tick", {"n": 2})

    runner.submit(job, work)
    deadline = time.monotonic() + 2.0
    while job.status not in (JobStatus.done, JobStatus.failed):
        if time.monotonic() > deadline:
            raise AssertionError("job did not complete")
        time.sleep(0.01)
    assert job.status == JobStatus.done
    assert len(job.events) == 2


def test_jobrunner_records_failure():
    mgr = JobManager()
    job = mgr.create("test", {})
    runner = JobRunner(mgr)

    def boom(emit):
        emit("started", {})
        raise RuntimeError("kaboom")

    runner.submit(job, boom)
    deadline = time.monotonic() + 2.0
    while job.status not in (JobStatus.done, JobStatus.failed):
        if time.monotonic() > deadline:
            raise AssertionError("job did not finish")
        time.sleep(0.01)
    assert job.status == JobStatus.failed
    assert "kaboom" in job.error


def test_jobrunner_running_jobs_count():
    mgr = JobManager()
    runner = JobRunner(mgr)
    started = threading.Event()
    done = threading.Event()

    def slow(emit):
        started.set()
        done.wait(timeout=2)

    job = mgr.create("test", {})
    runner.submit(job, slow)
    started.wait(timeout=1)
    assert runner.running_count() >= 1
    done.set()
    deadline = time.monotonic() + 2.0
    while job.status != JobStatus.done:
        if time.monotonic() > deadline:
            raise AssertionError("job did not complete")
        time.sleep(0.01)
    assert runner.running_count() == 0


# --- Phase 1 REL-02 event-cap tests ---


def test_progress_event_has_seq_field():
    ev = ProgressEvent(kind="x", data={})
    # Default seq is 0; Job.emit assigns the real value.
    assert ev.seq == 0


def test_job_emit_assigns_monotonic_seq_starting_at_one():
    job = Job(job_id="j1", kind="k", args={})
    for i in range(5):
        job.emit("tick", {"i": i})
    seqs = [e.seq for e in job.events]
    assert seqs == [1, 2, 3, 4, 5]
    assert job.next_seq == 5


def test_job_events_is_a_deque_with_default_maxlen():
    job = Job(job_id="j1", kind="k", args={})
    assert isinstance(job.events, deque)
    assert job.events.maxlen == runner._MAX_EVENTS_PER_JOB == 1000


def test_job_events_deque_evicts_oldest_at_maxlen(monkeypatch):
    """REL-02: 1100 emits leave exactly 1000 events; oldest 100 are evicted."""
    # No monkeypatch needed — default cap is 1000.
    job = Job(job_id="j1", kind="k", args={})
    for i in range(1100):
        job.emit("tick", {"i": i})
    assert len(job.events) == 1000
    # Leftmost surviving event has seq 101 (1..100 evicted).
    assert job.events[0].seq == 101
    assert job.events[-1].seq == 1100
    # next_seq still records every emit.
    assert job.next_seq == 1100


def test_job_events_deque_with_patched_maxlen(monkeypatch):
    """Monkeypatching _MAX_EVENTS_PER_JOB before Job(...) creation picks up new cap."""
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 5)
    job = Job(job_id="j1", kind="k", args={})
    assert job.events.maxlen == 5
    for i in range(7):
        job.emit("tick", {"i": i})
    assert len(job.events) == 5
    assert [e.seq for e in job.events] == [3, 4, 5, 6, 7]


def test_job_snapshot_events_filters_by_seq():
    job = Job(job_id="j1", kind="k", args={})
    for i in range(5):
        job.emit("tick", {"i": i})
    # snapshot_events(after_seq=2) returns events with seq 3, 4, 5
    snap = job.snapshot_events(after_seq=2)
    assert [e.seq for e in snap] == [3, 4, 5]


def test_job_snapshot_events_default_returns_all():
    job = Job(job_id="j1", kind="k", args={})
    for i in range(3):
        job.emit("tick", {"i": i})
    snap = job.snapshot_events()
    assert [e.seq for e in snap] == [1, 2, 3]


def test_job_snapshot_events_after_high_seq_returns_empty():
    job = Job(job_id="j1", kind="k", args={})
    for i in range(3):
        job.emit("tick", {"i": i})
    assert job.snapshot_events(after_seq=99) == []


def test_job_oldest_seq_empty_returns_zero():
    job = Job(job_id="j1", kind="k", args={})
    assert job.oldest_seq() == 0


def test_job_oldest_seq_returns_leftmost_seq():
    job = Job(job_id="j1", kind="k", args={})
    for _ in range(5):
        job.emit("tick", {})
    assert job.oldest_seq() == 1


def test_job_oldest_seq_after_eviction(monkeypatch):
    monkeypatch.setattr(runner, "_MAX_EVENTS_PER_JOB", 3)
    job = Job(job_id="j1", kind="k", args={})
    for _ in range(5):
        job.emit("tick", {})
    # Events 1, 2 evicted; 3, 4, 5 remain.
    assert job.oldest_seq() == 3


# --- Phase 1 REL-03 JobManager-pruning tests ---


def test_jobmanager_under_cap_does_not_prune(monkeypatch):
    monkeypatch.setattr(runner, "_MAX_JOBS", 5)
    mgr = JobManager()
    for _ in range(3):
        mgr.create("k", {})
    assert len(mgr._jobs) == 3
    assert len(mgr._order) == 3


def test_jobmanager_prunes_oldest_done_jobs_at_cap(monkeypatch):
    monkeypatch.setattr(runner, "_MAX_JOBS", 5)
    mgr = JobManager()
    jobs = [mgr.create("k", {}) for _ in range(5)]
    for j in jobs:
        j.set_done()
    # 6th create triggers prune; oldest done job evicted.
    j6 = mgr.create("k", {})
    assert len(mgr._jobs) == 5
    assert jobs[0].job_id not in mgr._jobs
    assert j6.job_id in mgr._jobs


def test_jobmanager_pruning_preserves_running_jobs(monkeypatch):
    """D-13: pending/running jobs are pinned; only done/failed are evicted."""
    monkeypatch.setattr(runner, "_MAX_JOBS", 3)
    mgr = JobManager()
    j1 = mgr.create("k", {})  # pending — pinned
    j2 = mgr.create("k", {})  # will mark done
    j2.set_done()
    j3 = mgr.create("k", {})  # will mark done
    j3.set_done()
    # 4th create: cap is 3, 1 pending + 2 done present; need to evict
    # exactly one. j2 is the oldest done job.
    j4 = mgr.create("k", {})
    assert j1.job_id in mgr._jobs  # pinned
    assert j2.job_id not in mgr._jobs  # oldest done evicted
    assert j3.job_id in mgr._jobs
    assert j4.job_id in mgr._jobs
    # 5th create: now j3 is the oldest done; evict it.
    j5 = mgr.create("k", {})
    assert j1.job_id in mgr._jobs
    assert j3.job_id not in mgr._jobs
    assert j4.job_id in mgr._jobs
    assert j5.job_id in mgr._jobs


def test_jobmanager_evicts_failed_as_well_as_done(monkeypatch):
    monkeypatch.setattr(runner, "_MAX_JOBS", 2)
    mgr = JobManager()
    j1 = mgr.create("k", {})
    j1.set_failed("err")
    j2 = mgr.create("k", {})
    j2.set_done()
    # j1 (failed) is the oldest terminal — should be evictable on next create.
    j3 = mgr.create("k", {})
    assert j1.job_id not in mgr._jobs
    assert j2.job_id in mgr._jobs
    assert j3.job_id in mgr._jobs


def test_jobmanager_overshoots_when_all_running(monkeypatch):
    """D-13 explicit: cap is exceeded if no terminal jobs are evictable."""
    monkeypatch.setattr(runner, "_MAX_JOBS", 3)
    mgr = JobManager()
    js = [mgr.create("k", {}) for _ in range(5)]  # all pending
    # Cap is 3 but every job is pending — none are evictable.
    assert len(mgr._jobs) == 5
    for j in js:
        assert j.status == JobStatus.pending


def test_jobmanager_60_jobs_caps_at_50_when_done(monkeypatch):
    """REL-03 ROADMAP literal: 60+ jobs submitted, all done -> _jobs <= 50."""
    # Default cap is 50.
    mgr = JobManager()
    for _ in range(60):
        j = mgr.create("k", {})
        j.set_done()
    assert len(mgr._jobs) == 50


def test_jobmanager_just_created_job_is_never_pruned(monkeypatch):
    """RESEARCH Pitfall 5: insert-then-prune order ensures the new job survives."""
    monkeypatch.setattr(runner, "_MAX_JOBS", 1)
    mgr = JobManager()
    j1 = mgr.create("k", {})
    j1.set_done()
    # Cap is 1; creating j2 evicts j1 (done) but j2 is pending and survives.
    j2 = mgr.create("k", {})
    assert j2.job_id in mgr._jobs
    # j1 is gone.
    assert j1.job_id not in mgr._jobs


def test_jobmanager_order_list_consistent_after_prune(monkeypatch):
    """_order contains exactly the surviving job ids, oldest-first."""
    monkeypatch.setattr(runner, "_MAX_JOBS", 3)
    mgr = JobManager()
    a = mgr.create("k", {})
    a.set_done()
    b = mgr.create("k", {})
    b.set_done()
    c = mgr.create("k", {})
    c.set_done()
    d = mgr.create("k", {})  # triggers prune
    assert mgr._order == [b.job_id, c.job_id, d.job_id]
    assert set(mgr._order) == set(mgr._jobs.keys())


# ---- AUTH-04 integration test (Phase 2 / Plan 04) ----


def test_runner_marks_failed_on_auth_failure():
    """AUTH-04: a JobWork that raises AuthFailedError causes JobRunner to mark
    the job `failed` with the AuthFailedError message in job.error.

    This validates the full propagation path: archive_story raises (Plan 04) ->
    JobRunner._run catches Exception -> job.set_failed(str(e)).
    Mirrors test_jobrunner_records_failure (lines 106-122) verbatim with
    AuthFailedError swapped in for RuntimeError.
    """
    mgr = JobManager()
    job = mgr.create("archive_story", {"story_id": "42"})
    job_runner = JobRunner(mgr)

    def work(emit):
        raise AuthFailedError(
            "simulated auth failure",
            status_code=401,
            url="https://w/x",
        )

    job_runner.submit(job, work)
    deadline = time.monotonic() + 2.0
    while job.status not in (JobStatus.done, JobStatus.failed):
        if time.monotonic() > deadline:
            raise AssertionError("job did not finish")
        time.sleep(0.01)
    assert job.status == JobStatus.failed
    assert "simulated auth failure" in (job.error or "")
