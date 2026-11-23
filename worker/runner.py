"""One job from receive to delete (spec section 4).

Outcomes, as written to the usage log:
  ok         clip uploaded, message deleted
  stale      the job's marker was already gone: message deleted, GPU never used
  cancelled  the marker vanished mid-run or before upload: message and inputs deleted, nothing uploaded
  released   Spot notice or SIGTERM: message handed back at once for another worker
  failed     the job can never succeed: `.failed` marker written, message deleted
  error      anything unexpected: message handed back; the queue's redrive policy ends endless retries
  gave_up    an unexpected error on the third receive or later: `.failed` marker written, message deleted
  bad_message  could not even read whose job it is: message deleted
"""
import logging
import shutil
import time
from datetime import datetime, timezone

from common.usage import log_usage as default_log_usage

from .errors import Aborted, BadMessage, PermanentJobError
from .job import Job
from .watcher import JobWatcher

log = logging.getLogger("worker.runner")

# An unexpected error on this receive or later ends the job instead of handing it back. Trial 1 of
# plan 06 showed a deterministic error running a 7 minute job again and again; the queue's own limit
# of 8 receives would have meant about an hour of GPU. Spot releases count as receives too.
GIVE_UP_AFTER_RECEIVES = 3
# Outcomes that leave a usage record (plan 08). `stale` never used the GPU and `cancelled` belongs to a
# job nobody will look at again; `bad_message` has no job at all.
RECORDED = {"ok", "failed", "released", "error", "gave_up"}
GAVE_UP = PermanentJobError("internal", "The job could not be completed.")   # same text as the DLQ Lambda


class Worker:
    def __init__(self, config, queue, store, generator, protection, spot_notice, stopping,
                 instance_type, work_dir, log_usage=default_log_usage, clock=time.monotonic,
                 attempt_cost=lambda instance_type, start, end: None,
                 wall_clock=lambda: datetime.now(timezone.utc)):
        self.config, self.queue, self.store, self.generator = config, queue, store, generator
        self.protection, self.spot_notice, self.stopping = protection, spot_notice, stopping
        self.instance_type, self.work_dir = instance_type, work_dir
        self.log_usage, self.clock = log_usage, clock
        self.attempt_cost, self.wall_clock = attempt_cost, wall_clock

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

        started = self.clock()        # spec 3H: wall-clock from receive to delete or release
        started_at = self.wall_clock()   # the same moment as a date, to look up the Spot price
        # Ids and settings only, never the prompt or the user's id: CloudWatch is the one window into a job.
        log.info("received job %s (attempt %d, quality %s)", job.job_id, job.receive_count,
                 job.params.get("quality"))
        outcome = "error"
        try:
            outcome = self._attempt(message, job)
        except Exception:
            if job.receive_count >= GIVE_UP_AFTER_RECEIVES:
                log.exception("job %s failed unexpectedly on receive %d; giving up", job.job_id, job.receive_count)
                outcome = self._give_up(message, job)
            else:
                log.exception("job %s failed unexpectedly; handing it back", job.job_id)
                self._release(message)
        finally:
            gpu_seconds = round(self.clock() - started, 1)
            instance_type = self.instance_type()
            cost = self._cost(instance_type, started_at) if outcome in RECORDED else None
            self.log_usage("job_attempt", sub=job.sub, job_id=job.job_id, attempt=job.receive_count,
                           instance_type=instance_type, quality=job.params.get("quality"),
                           gpu_seconds=gpu_seconds, cost_usd=cost, outcome=outcome)
            if outcome in RECORDED:
                self._record_attempt(job, outcome, instance_type, gpu_seconds, cost)
        return outcome

    def _cost(self, instance_type, started_at):
        """Estimated USD for the attempt, or None. Pricing must never change what happens to a job."""
        try:
            cost = self.attempt_cost(instance_type, started_at, self.wall_clock())
            return None if cost is None else round(cost, 6)
        except Exception:
            log.exception("could not price the attempt")
            return None

    def _record_attempt(self, job, outcome, instance_type, gpu_seconds, cost):
        """The record the API sums into the job's cost and try count. Best effort, like the usage log."""
        try:
            self.store.put_attempt(job.sub, job.job_id, job.receive_count, {
                "gpu_seconds": gpu_seconds, "cost_usd": cost, "outcome": outcome,
                "instance_type": instance_type})
        except Exception:
            log.exception("could not write the usage record for job %s attempt %d", job.job_id, job.receive_count)

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
                try:
                    clip = self.generator.generate(job, inputs, watcher.should_abort, scratch)
                except Aborted:
                    outcome = "cancelled" if watcher.reason == "cancelled" else "released"
                except PermanentJobError as e:
                    self.store.put_failed(job.sub, job.job_id, e.to_json())
                    outcome = "failed"
                else:
                    outcome = self._decide(watcher, job)
                    if outcome == "ok":
                        self.store.upload_clip(job.sub, job.job_id, clip)
                        log.info("uploaded clip for job %s", job.job_id)
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
        return "ok"

    # --- helpers -------------------------------------------------------------------

    def _fail(self, message, job, error):
        self.store.put_failed(job.sub, job.job_id, error.to_json())
        self._finish(message, job)
        return "failed"

    def _give_up(self, message, job):
        """Tell the user, then drop the message. If the marker cannot be written the message goes back
        instead: a job must not vanish without the user hearing why."""
        try:
            self.store.put_failed(job.sub, job.job_id, GAVE_UP.to_json())
        except Exception:
            log.exception("could not write the failed marker of job %s; handing it back", job.job_id)
            self._release(message)
            return "error"
        self._finish(message, job)
        return "gave_up"

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
