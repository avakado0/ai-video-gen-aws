"""Watches a running job from a side thread (spec section 4).

Every few seconds: is there a Spot interruption notice, or has SIGTERM arrived? Once a minute:
re-HEAD the job's `.pending` marker (a 404 means the user cancelled) and extend the message's
visibility so it does not come back while we work. The generator only reads `should_abort()`.
"""
import logging
import threading
import time

log = logging.getLogger("worker.watcher")


class JobWatcher:
    def __init__(self, queue, store, message, job, spot_notice, stopping, heartbeat_seconds,
                 visibility_timeout, poll_seconds, clock=time.monotonic):
        self.queue, self.store, self.message, self.job = queue, store, message, job
        self.spot_notice, self.stopping, self.clock = spot_notice, stopping, clock
        self.heartbeat_seconds, self.visibility_timeout, self.poll_seconds = (
            heartbeat_seconds, visibility_timeout, poll_seconds)
        self._cancelled = False
        self._interrupted = False
        self._last_heartbeat = clock()
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="job-watcher", daemon=True)

    @property
    def reason(self):
        """"cancelled" (user gave up on the job), "interrupted" (Spot or SIGTERM: hand the job
        back), or None. Cancelled wins: nobody else should run a job nobody wants."""
        if self._cancelled:
            return "cancelled"
        return "interrupted" if self._interrupted else None

    def should_abort(self):
        return self.reason is not None

    def tick(self):
        """One round of checks. Never raises: a flaky check must not kill a running job."""
        try:
            if self.spot_notice() or self.stopping():
                self._interrupted = True
        except Exception:
            log.exception("interruption check failed")
        if self.clock() - self._last_heartbeat >= self.heartbeat_seconds:
            self._last_heartbeat = self.clock()
            self._heartbeat()

    def _heartbeat(self):
        try:
            if not self.store.marker_exists(self.job.sub, self.job.job_id):
                self._cancelled = True
                return
        except Exception:
            # Not proof of a cancel. The check before the upload raises again if it persists.
            log.exception("marker check failed for %s", self.job.job_id)
        try:
            self.queue.extend(self.message, self.visibility_timeout)
        except Exception:
            log.exception("heartbeat failed for %s", self.job.job_id)

    def _loop(self):
        while not self._halt.wait(self.poll_seconds):
            self.tick()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._halt.set()
        self._thread.join()
