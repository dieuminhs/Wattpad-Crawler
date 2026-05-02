import threading

from wattpad_crawler.web.runner import Job, JobManager, JobStatus, ProgressEvent


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
