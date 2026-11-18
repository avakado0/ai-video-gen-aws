"""Worker.handle: one queue message from receive to delete, with a fake generator.

S3 and SQS are moto. The generator is a fake that does whatever the test scripts. The watcher is
the real one, with tiny intervals, so cancel and Spot paths go through the real thread.
"""
import json
import pathlib
import time

import pytest
from botocore.exceptions import ClientError

from common import manifest as manifest_module
from worker.config import Config
from worker.errors import Aborted, PermanentJobError
from worker.generator import Generator
from worker.jobqueue import WorkerQueue
from worker.runner import Worker
from worker.store import ClipStore

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = manifest_module.load((ROOT / "model_manifest.json").read_text())
JOB_ID = "6f1c1c2e-8a53-4d0b-9d0e-5b7d2f0a1c11"
SUB = "user-1"
PENDING = f"clips/{SUB}/{JOB_ID}.pending"


class FakeGenerator(Generator):
    """`script(should_abort, out_dir)` returns the clip path, or raises."""

    def __init__(self, script=None):
        self.calls = []
        self.script = script or (lambda should_abort, out_dir: self.write(out_dir))

    @staticmethod
    def write(out_dir):
        path = out_dir / "clip.mp4"
        path.write_bytes(b"video")
        return path

    def generate(self, job, inputs, should_abort, out_dir):
        self.calls.append(job)
        return self.script(should_abort, out_dir)


def wait_for_abort(should_abort, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if should_abort():
            raise Aborted()
        time.sleep(0.002)
    raise AssertionError("watcher never asked to abort")


class Protection:
    def __init__(self):
        self.calls = []

    def protect(self):
        self.calls.append("protect")

    def release(self):
        self.calls.append("release")


class Env:
    """Everything a test needs, wired the way __main__ wires the real thing."""

    def __init__(self, aws, tmp_path, generator=None):
        self.aws = aws
        self.generator = generator or FakeGenerator()
        self.protection = Protection()
        self.usage = []
        self.spot = []        # truthy = a Spot notice is up
        self.stopping = []    # truthy = SIGTERM arrived
        self.queue = WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0)
        self.store = ClipStore(aws.s3, aws.bucket)
        self.tmp_path = tmp_path
        self.worker = self.build(self.store)

    def build(self, store, heartbeat=0):
        cfg = Config(queue_url=self.aws.queue_url, clips_bucket=self.aws.bucket, models_dir="/m",
                     manifest=MANIFEST, heartbeat_seconds=heartbeat, spot_poll_seconds=0.002)
        return Worker(cfg, self.queue, store, self.generator, self.protection,
                      spot_notice=lambda: bool(self.spot), stopping=lambda: bool(self.stopping),
                      instance_type=lambda: "g4dn.2xlarge", work_dir=self.tmp_path,
                      log_usage=lambda event, **f: self.usage.append({"event": event, **f}))

    def submit(self, marker=True, **overrides):
        msg = {"job_id": JOB_ID, "sub": SUB, "prompt": "a cat", "params": {}, "inputs": {},
               "created": "2026-11-18T10:00:00+00:00"}
        msg.update(overrides)
        if marker:
            self.aws.s3.put_object(Bucket=self.aws.bucket, Key=PENDING, Body=b"{}")
        self.aws.sqs.send_message(QueueUrl=self.aws.queue_url, MessageBody=json.dumps(msg))

    def run_one(self):
        return self.worker.handle(self.queue.receive())

    def keys(self):
        return sorted(o["Key"] for o in self.aws.s3.list_objects_v2(Bucket=self.aws.bucket).get("Contents", []))

    def counts(self):
        a = self.aws.sqs.get_queue_attributes(QueueUrl=self.aws.queue_url, AttributeNames=["All"])["Attributes"]
        return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])


@pytest.fixture
def env(aws, tmp_path):
    return Env(aws, tmp_path)


# --- the happy path ------------------------------------------------------------------

def test_success_uploads_the_clip_and_deletes_the_message(env):
    env.submit()
    assert env.run_one() == "success"
    assert f"clips/{SUB}/{JOB_ID}.mp4" in env.keys()
    assert env.counts() == (0, 0)


def test_success_removes_the_inputs(env):
    env.submit()
    env.aws.s3.put_object(Bucket=env.aws.bucket, Key=f"inputs/{SUB}/{JOB_ID}/image/0", Body=b"x")
    env.run_one()
    assert not [k for k in env.keys() if k.startswith("inputs/")]


def test_protection_is_on_while_working_and_off_after(env):
    env.submit()
    env.run_one()
    assert env.protection.calls == ["protect", "release"]


def test_generator_gets_the_checked_job(env):
    env.submit(params={"seed": 5}, prompt="  a cat  ")
    env.run_one()
    job = env.generator.calls[0]
    assert job.prompt == "a cat" and job.params == {"seed": 5, "duration_seconds": "3", "negative_prompt": ""}


def test_one_usage_line_per_attempt(env):
    env.submit()
    env.run_one()
    (line,) = env.usage
    assert line["event"] == "job_attempt"
    assert (line["sub"], line["job_id"], line["attempt"], line["instance_type"], line["outcome"]) == (
        SUB, JOB_ID, 1, "g4dn.2xlarge", "success")
    assert line["gpu_seconds"] >= 0


# --- jobs nobody wants ---------------------------------------------------------------

def test_stale_message_is_dropped_without_touching_the_gpu(env):
    env.submit(marker=False)
    assert env.run_one() == "stale"
    assert env.generator.calls == [] and env.counts() == (0, 0)
    assert env.usage[0]["outcome"] == "stale" and env.usage[0]["gpu_seconds"] == 0
    assert env.protection.calls == []


def test_marker_check_denied_releases_instead_of_dropping(env):
    class Denied:
        def __init__(self, real):
            self.real = real

        def __getattr__(self, name):
            return getattr(self.real, name)

        def head_object(self, **kw):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    env.worker = env.build(ClipStore(Denied(env.aws.s3), env.aws.bucket))
    env.submit()
    assert env.run_one() == "error"
    assert env.generator.calls == []
    assert env.counts() == (1, 0)                       # back on the queue, not deleted


def test_cancel_during_the_run_aborts_and_uploads_nothing(env):
    def script(should_abort, out_dir):
        env.aws.s3.delete_object(Bucket=env.aws.bucket, Key=PENDING)     # the user pressed Cancel
        wait_for_abort(should_abort)

    env.generator.script = script
    env.submit()
    env.aws.s3.put_object(Bucket=env.aws.bucket, Key=f"inputs/{SUB}/{JOB_ID}/image/0", Body=b"x")
    assert env.run_one() == "cancelled"
    assert env.keys() == []                             # no clip, no inputs left
    assert env.counts() == (0, 0)
    assert env.usage[0]["outcome"] == "cancelled"
    assert env.protection.calls == ["protect", "release"]


def test_cancel_right_before_the_upload_leaves_no_orphan_clip(aws, tmp_path):
    # The generator finishes and the marker vanishes before the watcher's next look.
    def script(should_abort, out_dir):
        aws.s3.delete_object(Bucket=aws.bucket, Key=PENDING)
        return FakeGenerator.write(out_dir)

    env = Env(aws, tmp_path, FakeGenerator(script))
    env.worker = env.build(env.store, heartbeat=3600)   # watcher will not look in time
    env.submit()
    assert env.run_one() == "cancelled"
    assert env.keys() == [] and env.counts() == (0, 0)


# --- interruptions -------------------------------------------------------------------

def test_spot_notice_releases_the_job_and_writes_no_failure(env):
    def script(should_abort, out_dir):
        env.spot.append(1)
        wait_for_abort(should_abort)

    env.generator.script = script
    env.submit()
    assert env.run_one() == "released"
    assert env.counts() == (1, 0)                       # visible again at once
    assert env.keys() == [PENDING]                      # marker stays; no .failed
    assert env.usage[0]["outcome"] == "released"
    assert env.protection.calls == ["protect", "release"]


def test_sigterm_releases_the_job(env):
    def script(should_abort, out_dir):
        env.stopping.append(1)
        wait_for_abort(should_abort)

    env.generator.script = script
    env.submit()
    assert env.run_one() == "released"
    assert env.counts() == (1, 0)


def test_interruption_that_arrives_as_the_generator_finishes_still_releases(aws, tmp_path):
    def script(should_abort, out_dir):
        env.spot.append(1)
        wait_for_abort_quietly(should_abort)
        return FakeGenerator.write(out_dir)

    def wait_for_abort_quietly(should_abort):
        end = time.monotonic() + 3
        while not should_abort() and time.monotonic() < end:
            time.sleep(0.002)

    env = Env(aws, tmp_path, FakeGenerator(script))
    env.submit()
    assert env.run_one() == "released"
    assert f"clips/{SUB}/{JOB_ID}.mp4" not in env.keys()    # a half-trusted clip is not uploaded
    assert env.counts() == (1, 0)


# --- failures ------------------------------------------------------------------------

def test_permanent_error_writes_failed_marker_and_deletes_the_message(env):
    def script(should_abort, out_dir):
        raise PermanentJobError("unsupported_prompt", "That prompt cannot be used.")

    env.generator.script = script
    env.submit()
    assert env.run_one() == "failed"
    body = env.aws.s3.get_object(Bucket=env.aws.bucket, Key=f"clips/{SUB}/{JOB_ID}.failed")["Body"].read()
    assert json.loads(body) == {"code": "unsupported_prompt", "message": "That prompt cannot be used."}
    assert env.counts() == (0, 0)
    assert env.usage[0]["outcome"] == "failed"


def test_job_the_manifest_now_refuses_fails_without_running(env):
    env.submit(params={"duration_seconds": "9"})
    assert env.run_one() == "failed"
    assert env.generator.calls == []
    failed = json.loads(env.aws.s3.get_object(Bucket=env.aws.bucket, Key=f"clips/{SUB}/{JOB_ID}.failed")["Body"].read())
    assert failed["code"] == "invalid_request"


def test_unexpected_error_releases_for_retry_and_writes_nothing(env):
    def script(should_abort, out_dir):
        raise RuntimeError("CUDA out of memory")

    env.generator.script = script
    env.submit()
    assert env.run_one() == "error"
    assert env.counts() == (1, 0)
    assert env.keys() == [PENDING]
    assert env.usage[0]["outcome"] == "error"
    assert env.protection.calls == ["protect", "release"]


def test_retry_counts_attempts_from_the_receive_count(env):
    def script(should_abort, out_dir):
        raise RuntimeError("boom")

    env.generator.script = script
    env.submit()
    env.run_one()
    env.run_one()
    assert [u["attempt"] for u in env.usage] == [1, 2]


def test_bad_message_is_deleted_and_logged(env):
    env.aws.sqs.send_message(QueueUrl=env.aws.queue_url, MessageBody="not json")
    assert env.run_one() == "bad_message"
    assert env.counts() == (0, 0) and env.usage == [] and env.generator.calls == []


def test_protection_failure_does_not_stop_the_job(env):
    def broken():
        raise RuntimeError("ecs api down")

    env.protection.protect = broken
    env.submit()
    assert env.run_one() == "success"


# --- the loop ------------------------------------------------------------------------

def test_heartbeat_extends_visibility_while_running(env):
    extended = []
    real = env.queue.extend
    env.queue.extend = lambda message, seconds: (extended.append(seconds), real(message, seconds))

    def script(should_abort, out_dir):
        end = time.monotonic() + 3
        while not extended and time.monotonic() < end:     # heartbeat is 0 s here: next tick fires one
            time.sleep(0.002)
        return FakeGenerator.write(out_dir)

    env.generator.script = script
    env.submit()
    assert env.run_one() == "success"
    assert extended and extended[0] == env.worker.config.visibility_timeout


def test_gpu_seconds_are_logged_even_when_the_run_blows_up(env):
    ticks = iter(range(0, 1000, 10))
    env.worker.clock = lambda: next(ticks)

    def script(should_abort, out_dir):
        raise RuntimeError("CUDA out of memory")

    env.generator.script = script
    env.submit()
    env.run_one()
    assert env.usage[0]["gpu_seconds"] == 10


def test_run_forever_works_through_the_queue_then_stops_on_sigterm(env):
    env.submit()

    def script(should_abort, out_dir):
        env.stopping.append(1)       # SIGTERM lands during the job; the loop must then exit
        wait_for_abort(should_abort)

    env.generator.script = script
    env.worker.run_forever()
    assert len(env.generator.calls) == 1 and env.usage[0]["outcome"] == "released"
