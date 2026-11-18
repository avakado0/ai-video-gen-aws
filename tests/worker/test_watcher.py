"""The watcher runs beside the generator: Spot notice, SIGTERM, heartbeat, cancel check."""
import time

from worker.job import Job
from worker.watcher import JobWatcher

JOB = Job(job_id="6f1c1c2e-8a53-4d0b-9d0e-5b7d2f0a1c11", sub="user-1", prompt="p")


class FakeQueue:
    def __init__(self):
        self.extended = []
        self.fail = False

    def extend(self, message, seconds):
        if self.fail:
            raise RuntimeError("receipt handle expired")
        self.extended.append((message, seconds))


class FakeStore:
    def __init__(self):
        self.wanted = True
        self.error = None
        self.heads = 0

    def marker_exists(self, sub, job_id):
        self.heads += 1
        if self.error:
            raise self.error
        return self.wanted


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(spot=lambda: False, stopping=lambda: False, **kw):
    clock, queue, store = Clock(), FakeQueue(), FakeStore()
    w = JobWatcher(queue, store, "msg", JOB, spot_notice=spot, stopping=stopping, clock=clock,
                   heartbeat_seconds=60, visibility_timeout=180, poll_seconds=5, **kw)
    return w, clock, queue, store


def test_quiet_job_does_not_abort():
    w, *_ = make()
    w.tick()
    assert not w.should_abort() and w.reason is None


def test_no_heartbeat_before_a_minute_has_passed():
    w, clock, queue, store = make()
    clock.now += 59
    w.tick()
    assert queue.extended == [] and store.heads == 0


def test_heartbeat_extends_visibility_and_checks_the_marker():
    w, clock, queue, store = make()
    clock.now += 60
    w.tick()
    assert queue.extended == [("msg", 180)] and store.heads == 1
    clock.now += 30
    w.tick()
    assert len(queue.extended) == 1            # next one is a minute after the last


def test_missing_marker_means_cancelled():
    w, clock, queue, store = make()
    store.wanted = False
    clock.now += 60
    w.tick()
    assert w.should_abort() and w.reason == "cancelled"


def test_spot_notice_means_interrupted_and_is_seen_between_heartbeats():
    notice = []
    w, clock, *_ = make(spot=lambda: bool(notice))
    w.tick()
    assert not w.should_abort()
    notice.append(1)
    w.tick()                                    # no heartbeat due, still noticed
    assert w.should_abort() and w.reason == "interrupted"


def test_sigterm_means_interrupted():
    flag = []
    w, *_ = make(stopping=lambda: bool(flag))
    flag.append(1)
    w.tick()
    assert w.reason == "interrupted"


def test_cancel_wins_when_both_happen():
    w, clock, queue, store = make(spot=lambda: True)
    store.wanted = False
    clock.now += 60
    w.tick()
    assert w.reason == "cancelled"


def test_marker_check_error_does_not_cancel_the_job():
    # A 403 or a network blip must not throw away a running job; the check before upload decides.
    w, clock, queue, store = make()
    store.error = RuntimeError("403")
    clock.now += 60
    w.tick()
    assert not w.should_abort()


def test_heartbeat_failure_does_not_stop_the_job():
    w, clock, queue, store = make()
    queue.fail = True
    clock.now += 60
    w.tick()
    assert not w.should_abort()


def test_cancelled_job_is_not_extended():
    w, clock, queue, store = make()
    store.wanted = False
    clock.now += 60
    w.tick()
    assert queue.extended == []


def test_thread_runs_ticks_until_stopped():
    notice = []
    clock, queue, store = Clock(), FakeQueue(), FakeStore()
    w = JobWatcher(queue, store, "msg", JOB, spot_notice=lambda: bool(notice), stopping=lambda: False,
                   clock=clock, heartbeat_seconds=60, visibility_timeout=180, poll_seconds=0.01)
    with w:
        notice.append(1)
        for _ in range(200):
            if w.should_abort():
                break
            time.sleep(0.01)
    assert w.reason == "interrupted"
    assert not w._thread.is_alive()
