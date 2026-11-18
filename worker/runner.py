"""One job from receive to delete (spec section 4).

Outcomes, as written to the usage log:
  success    clip uploaded, message deleted
  stale      the job's marker was already gone: message deleted, GPU never used
  cancelled  the marker vanished mid-run or before upload: message and inputs deleted, nothing uploaded
  released   Spot notice or SIGTERM: message handed back at once for another worker
  failed     the job can never succeed: `.failed` marker written, message deleted
  error      anything unexpected: message handed back; the queue's redrive policy ends endless retries
  bad_message  could not even read whose job it is: message deleted
"""
import logging
import shutil
import time

from common.usage import log_usage as default_log_usage

from .errors import Aborted, BadMessage, PermanentJobError
from .job import Job
from .watcher import JobWatcher

log = logging.getLogger("worker.runner")


class Worker:
    def __init__(self, config, queue, store, generator, protection, spot_notice, stopping,
                 instance_type, work_dir, log_usage=default_log_usage, clock=time.monotonic):
        self.config, self.queue, self.store, self.generator = config, queue, store, generator
        self.protection, self.spot_notice, self.stopping = protection, spot_notice, stopping
        self.instance_type, self.work_dir = instance_type, work_dir
        self.log_usage, self.clock = log_usage, clock

    def run_forever(self):
        while not self.stopping():
            try:
                message = self.queue.receive()
            except Exception:
                log.exception("receive failed")
                time.sleep(5)
                continue
            if message is not None:
                self.handle(message)

    def handle(self, message):
        """Process one message; return the outcome."""
        try:
            job = Job.from_message(message.body, message.receive_count)
        except BadMessage as e:
            log.error("dropping unreadable message: %s", e)
            self.queue.delete(message)
            return "bad_message"

        self._gpu_seconds = 0.0
        outcome = "error"
        try:
            outcome = self._attempt(message, job)
        except Exception:
            log.exception("job %s failed unexpectedly; handing it back", job.job_id)
            self._release(message)
        finally:
            self.log_usage("job_attempt", sub=job.sub, job_id=job.job_id, attempt=job.receive_count,
                           instance_type=self.instance_type(), gpu_seconds=round(self._gpu_seconds, 1), outcome=outcome)
        return outcome

    # --- one attempt ---------------------------------------------------------------

    def _attempt(self, message, job):
        """Returns the outcome. Raises for "error": the caller releases the message."""
        try:
            job = job.checked(self.config.manifest)
            if not self.store.marker_exists(job.sub, job.job_id):
                self._finish(message, job)
                return "stale"
            return self._work(message, job)
        except PermanentJobError as e:
            return self._fail(message, job, e)

    def _work(self, message, job):
        scratch = self.work_dir / job.job_id
        self._protect()
        try:
            watcher = JobWatcher(self.queue, self.store, message, job, self.spot_notice, self.stopping,
                                 self.config.heartbeat_seconds, self.config.visibility_timeout,
                                 self.config.spot_poll_seconds)
            with watcher:
                scratch.mkdir(parents=True, exist_ok=True)
                inputs = self.store.fetch_inputs(job, scratch / "inputs")
                started = self.clock()
                try:
                    try:
                        clip = self.generator.generate(job, inputs, watcher.should_abort, scratch)
                    finally:
                        self._gpu_seconds = self.clock() - started   # counted even when the run blows up
                except Aborted:
                    outcome = "cancelled" if watcher.reason == "cancelled" else "released"
                except PermanentJobError as e:
                    self.store.put_failed(job.sub, job.job_id, e.to_json())
                    outcome = "failed"
                else:
                    outcome = self._decide(watcher, job)
                    if outcome == "success":
                        self.store.upload_clip(job.sub, job.job_id, clip)
            # The watcher thread has stopped, so its heartbeat cannot undo what follows.
            if outcome == "released":
                self._release(message)
            else:
                self._finish(message, job)
            return outcome
        finally:
            self._unprotect()
            shutil.rmtree(scratch, ignore_errors=True)

    def _decide(self, watcher, job):
        """The generator returned a clip. Is it still wanted, and is this instance still ours?"""
        if watcher.reason == "interrupted":
            return "released"          # a clip made under a Spot notice is dropped; another worker redoes it
        # Last look before the upload: a cancel in the final seconds must not leave a clip.
        if watcher.reason == "cancelled" or not self.store.marker_exists(job.sub, job.job_id):
            return "cancelled"
        return "success"

    # --- helpers -------------------------------------------------------------------

    def _fail(self, message, job, error):
        self.store.put_failed(job.sub, job.job_id, error.to_json())
        self._finish(message, job)
        return "failed"

    def _finish(self, message, job):
        """The job is over for good: delete its message and whatever was uploaded for it."""
        self.queue.delete(message)
        try:
            self.store.delete_inputs(job.sub, job.job_id)
        except Exception:
            log.exception("could not delete inputs of %s; the 1-day lifecycle will", job.job_id)

    def _release(self, message):
        try:
            self.queue.release(message)
        except Exception:
            log.exception("could not release message; it returns after the visibility timeout")

    def _protect(self):
        try:
            self.protection.protect()
        except Exception:
            log.exception("could not turn on scale-in protection; running unprotected")

    def _unprotect(self):
        try:
            self.protection.release()
        except Exception:
            log.exception("could not turn off scale-in protection")
