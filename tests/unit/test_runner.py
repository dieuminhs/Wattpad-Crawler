import threading
import time

from wattpad_crawler.web.runner import Job, JobManager, JobRunner, JobStatus, ProgressEvent


def test_progress_event_holds_fields():
    e = ProgressEvent(kind="part.done", data={"part_id": "100"})
    assert e.kind == "part.done"
    assert e.data == {"part_id": "100"}
    assert e.timestamp > 0


def test_job_default_state():
    job = Job(job_id="j1", kind="archive_story", args={"story_id": "42"})
    assert job.status == JobStatus.pending
    assert job.events == []
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
