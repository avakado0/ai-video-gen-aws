"""POST /jobs: validate against the manifest, enforce the cap, write the marker, queue the job."""
import json
import uuid
from datetime import datetime

import pytest

from conftest import BUCKET, IMAGE_TO_VIDEO, ROOT

JOB = "6f1c1f0e-8a2b-4c3d-9e4f-5a6b7c8d9e0f"


@pytest.fixture(autouse=True)
def i2v(use_manifest):
    use_manifest(IMAGE_TO_VIDEO)


@pytest.fixture
def put_input(aws):
    def _put(slot="image", index=0, size=100, sub="user-1", job=JOB):
        aws.s3.put_object(Bucket=BUCKET, Key=f"inputs/{sub}/{job}/{slot}/{index}", Body=b"x" * size)
    return _put


def submit(call, **over):
    body = {"job_id": JOB, "prompt": "a cat", "params": {}, "inputs": {"image": 1}}
    body.update(over)
    return call("POST", "/jobs", body=body)


def queued(aws):
    msgs = aws.sqs.receive_message(QueueUrl=aws.queue_url, MaxNumberOfMessages=10).get("Messages", [])
    return [json.loads(m["Body"]) for m in msgs]


def marker(aws, job=JOB, sub="user-1"):
    return json.loads(aws.s3.get_object(Bucket=BUCKET, Key=f"clips/{sub}/{job}.pending")["Body"].read())


def pending(aws, job, sub="user-1", ext="pending"):
    aws.s3.put_object(Bucket=BUCKET, Key=f"clips/{sub}/{job}.{ext}", Body=b"{}")


def other_job():
    return str(uuid.uuid4())


# --- the happy path -----------------------------------------------------------

def test_submit_writes_the_marker_and_queues_the_job(call, aws, put_input):
    put_input()

    res = submit(call, params={"seed": 7})

    assert res.status == 201 and res.json() == {"job_id": JOB}
    m = marker(aws)
    assert m["job_id"] == JOB and m["prompt"] == "a cat" and m["inputs"] == {"image": 1}
    assert m["params"]["seed"] == 7
    assert datetime.fromisoformat(m["created"])
    assert queued(aws) == [{"job_id": JOB, "sub": "user-1", "prompt": "a cat", "params": m["params"],
                            "inputs": {"image": 1}, "created": m["created"]}]


def test_text_only_model_needs_no_job_id_or_inputs(call, aws, use_manifest):
    use_manifest(json.loads((ROOT / "model_manifest.json").read_text()))

    res = call("POST", "/jobs", body={"prompt": "a cat", "params": {"duration_seconds": "5"}, "inputs": {}})

    assert res.status == 201
    job_id = res.json()["job_id"]
    assert str(uuid.UUID(job_id)) == job_id
    [msg] = queued(aws)
    assert msg["params"] == {"duration_seconds": "5", "quality": "fast", "negative_prompt": ""}  # seed unset: left out


def test_the_sub_in_the_marker_and_message_comes_from_the_claims(call, aws, put_input):
    put_input(sub="user-2")

    res = submit(call, sub="user-2")  # body field named sub is ignored

    assert res.status == 400  # user-2's upload does not count for user-1
    assert queued(aws) == []


# --- prompt -------------------------------------------------------------------

def test_prompt_is_trimmed(call, aws, put_input):
    put_input()

    submit(call, prompt="  a cat \n")

    assert marker(aws)["prompt"] == "a cat"


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 51])
def test_prompt_required_and_limited(call, put_input, prompt):
    put_input()

    res = submit(call, prompt=prompt)

    assert res.status == 400 and "prompt" in res.json()["detail"]


def test_prompt_modes_optional_and_none(call, aws, use_manifest, put_input):
    use_manifest({**IMAGE_TO_VIDEO, "prompt": "optional"})
    put_input()
    assert submit(call, prompt="").status == 201

    use_manifest({**IMAGE_TO_VIDEO, "prompt": "none"})
    other = other_job()
    put_input(job=other)
    assert submit(call, job_id=other, prompt="hello").status == 400
    assert submit(call, job_id=other, prompt="").status == 201


# --- params -------------------------------------------------------------------

def test_defaults_fill_in_and_null_means_unset(call, aws, put_input):
    put_input()

    submit(call, params={"seed": None, "style": None})

    assert marker(aws)["params"] == {"strength": 0.5, "style": "plain", "hd": False, "negative_prompt": ""}


def test_numbers_are_clamped_to_their_range(call, aws, put_input):
    put_input()

    submit(call, params={"seed": 500, "strength": -3})

    p = marker(aws)["params"]
    assert p["seed"] == 100 and p["strength"] == 0


@pytest.mark.parametrize("params", [
    {"nope": 1},
    {"seed": "7"}, {"seed": 7.5}, {"seed": True},
    {"strength": "high"}, {"strength": True},
    {"style": "wild"}, {"style": 3},
    {"hd": 1}, {"hd": "yes"},
    {"negative_prompt": "x" * 11}, {"negative_prompt": 5},
])
def test_bad_param_values_are_400(call, aws, put_input, params):
    put_input()

    res = submit(call, params=params)

    assert res.status == 400 and isinstance(res.json()["detail"], str)
    assert queued(aws) == []


def test_params_must_be_an_object(call, put_input):
    put_input()

    assert submit(call, params=[1]).status == 400


# --- inputs -------------------------------------------------------------------

def test_required_slot_must_have_a_file(call, aws):
    res = submit(call, inputs={})

    assert res.status == 400 and "image" in res.json()["detail"]


@pytest.mark.parametrize("inputs", [{"image": 1, "audio": 1}, {"image": 2}, {"image": 1, "refs": 4},
                                    {"image": 1, "refs": -1}, {"image": "1"}])
def test_input_counts_are_checked(call, put_input, inputs):
    put_input()

    assert submit(call, inputs=inputs).status == 400


def test_every_declared_file_must_already_be_uploaded(call, put_input):
    put_input()  # image 0 is there, refs 0 and 1 are not

    res = submit(call, inputs={"image": 1, "refs": 2})

    assert res.status == 400 and "refs" in res.json()["detail"]


def test_all_uploaded_files_pass(call, aws, put_input):
    put_input()
    put_input("refs", 0, 5)
    put_input("refs", 1, 5)

    assert submit(call, inputs={"image": 1, "refs": 2}).status == 201
    assert queued(aws)[0]["inputs"] == {"image": 1, "refs": 2}


def test_input_bytes_are_logged_for_cost_attribution(call, put_input, capsys):
    put_input(size=100)
    put_input("refs", 0, 40)

    submit(call, inputs={"image": 1, "refs": 1})

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert {"event": "inputs", "sub": "user-1", "job_id": JOB, "bytes": 140} in lines


# --- job id, duplicates, cap --------------------------------------------------

@pytest.mark.parametrize("job_id", ["nope", "../x", JOB.upper()])
def test_job_id_must_be_a_canonical_uuid(call, put_input, job_id):
    put_input()

    assert submit(call, job_id=job_id).status == 400


def test_a_job_id_cannot_be_submitted_twice(call, aws, put_input):
    put_input()
    assert submit(call).status == 201

    again = submit(call)

    assert again.status == 409
    assert len(queued(aws)) == 1


def test_a_fourth_active_job_gets_429(call, aws, put_input):
    for _ in range(3):
        pending(aws, other_job())
    put_input()

    res = submit(call)

    assert res.status == 429
    assert queued(aws) == []


def test_finished_jobs_and_other_users_do_not_count_toward_the_cap(call, aws, put_input):
    ready, failed = other_job(), other_job()
    pending(aws, ready); pending(aws, ready, ext="mp4")
    pending(aws, failed); pending(aws, failed, ext="failed")
    pending(aws, other_job(), sub="user-2"); pending(aws, other_job(), sub="user-2")
    pending(aws, other_job()); pending(aws, other_job())  # 2 active for user-1
    put_input()

    assert submit(call).status == 201


def test_the_cap_comes_from_the_environment(call, aws, put_input, monkeypatch):
    from api import handler
    monkeypatch.setenv("MAX_ACTIVE_JOBS", "1")
    handler.reset()
    pending(aws, other_job())
    put_input()

    assert submit(call).status == 429


# --- failure to queue ---------------------------------------------------------

def test_if_queueing_fails_the_marker_is_removed(call, aws, put_input):
    put_input()
    aws.sqs.delete_queue(QueueUrl=aws.queue_url)

    res = submit(call)

    assert res.status == 503
    keys = [o["Key"] for o in aws.s3.list_objects_v2(Bucket=BUCKET, Prefix="clips/").get("Contents", [])]
    assert keys == []  # no orphan marker eating the user's cap


# --- undo window --------------------------------------------------------------

def test_the_queue_message_is_delayed_so_a_quick_cancel_costs_nothing(call, aws, put_input, monkeypatch):
    from api import handler
    monkeypatch.setenv("SUBMIT_DELAY_SECONDS", "30")
    handler.reset()
    put_input()

    assert submit(call).status == 201

    assert queued(aws) == []  # not visible yet: no worker can start the job inside the window
    attrs = aws.sqs.get_queue_attributes(QueueUrl=aws.queue_url, AttributeNames=["ApproximateNumberOfMessagesDelayed"])
    assert attrs["Attributes"]["ApproximateNumberOfMessagesDelayed"] == "1"
