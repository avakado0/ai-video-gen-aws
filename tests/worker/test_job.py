"""Parsing a queue message into a Job, and re-checking it against the manifest."""
import json
import pathlib

import pytest

from common import manifest as manifest_module
from worker.errors import BadMessage, PermanentJobError
from worker.job import Job

ROOT = pathlib.Path(__file__).resolve().parents[2]
JOB_ID = "6f1c1c2e-8a53-4d0b-9d0e-5b7d2f0a1c11"
MANIFEST = manifest_module.load((ROOT / "model_manifest.json").read_text())


def body(**overrides):
    msg = {"job_id": JOB_ID, "sub": "user-1", "prompt": "a cat walking", "params": {}, "inputs": {},
           "created": "2026-11-18T10:00:00+00:00"}
    msg.update(overrides)
    return json.dumps(msg)


def test_parses_the_message_the_api_sends():
    job = Job.from_message(body(params={"seed": 7}), receive_count=2)
    assert (job.job_id, job.sub, job.prompt, job.params, job.receive_count) == (
        JOB_ID, "user-1", "a cat walking", {"seed": 7}, 2)


@pytest.mark.parametrize("raw", [
    "not json",
    "[]",
    json.dumps({"job_id": JOB_ID}),                                 # no sub
    body(job_id="../../etc/passwd"),                                # not a UUID
    body(job_id=JOB_ID.upper()),                                    # not canonical
    body(sub=""),
    body(sub="a/b"),                                                # would escape the user's prefix
    body(sub=5),
    body(prompt=None),
    body(params=[]),
    body(inputs={"image": "two"}),
])
def test_unusable_message_is_a_bad_message(raw):
    with pytest.raises(BadMessage):
        Job.from_message(raw, receive_count=1)


def test_checked_fills_defaults_and_trims_the_prompt():
    job = Job.from_message(body(prompt="  a cat  "), receive_count=1).checked(MANIFEST)
    assert job.prompt == "a cat"
    assert job.params["duration_seconds"] == "3"
    assert job.params["quality"] == "fast"


def test_checked_clamps_numbers_like_the_api_does():
    job = Job.from_message(body(params={"seed": 99999999999}), receive_count=1).checked(MANIFEST)
    assert job.params["seed"] == 2147483647


def test_job_the_manifest_refuses_is_a_permanent_error_with_a_user_message():
    job = Job.from_message(body(params={"duration_seconds": "9"}), receive_count=1)
    with pytest.raises(PermanentJobError) as err:
        job.checked(MANIFEST)
    assert err.value.code == "invalid_request"
    assert "duration_seconds" in err.value.message


def test_unknown_quality_is_a_permanent_error():
    job = Job.from_message(body(params={"quality": "ultra"}), receive_count=1)
    with pytest.raises(PermanentJobError) as err:
        job.checked(MANIFEST)
    assert err.value.code == "invalid_request" and "quality" in err.value.message


def test_permanent_error_json_has_only_the_documented_fields():
    assert PermanentJobError("invalid_request", "bad").to_json() == {"code": "invalid_request", "message": "bad"}
    assert PermanentJobError("invalid_request", "bad", field="seed").to_json() == {
        "code": "invalid_request", "field": "seed", "message": "bad"}
