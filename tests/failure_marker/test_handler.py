"""Failure-marker Lambda: a dead-lettered job gets a `.failed` marker in S3.

Spec 3D: triggered by the DLQ, reads `sub` and `job_id` from the message body
and writes clips/{sub}/{job_id}.failed containing {"code": "internal", ...}.
"""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from failure_marker import handler as failure_handler

BUCKET = "test-clips"
SUB = "11111111-2222-3333-4444-555555555555"
JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
JOB_2 = "99999999-8888-7777-6666-555555555555"


def record(body, message_id="m-1"):
    raw = body if isinstance(body, str) else json.dumps(body)
    return {"messageId": message_id, "body": raw}


def event(*records):
    return {"Records": list(records)}


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("CLIPS_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    with mock_aws():
        client = boto3.client("s3", region_name="us-west-2")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
        )
        yield client


def keys(s3):
    listing = s3.list_objects_v2(Bucket=BUCKET)
    return sorted(o["Key"] for o in listing.get("Contents", []))


def test_writes_failed_marker_for_a_dead_lettered_job(s3):
    result = failure_handler.lambda_handler(
        event(record({"sub": SUB, "job_id": JOB, "prompt": "a cat"})), None
    )

    assert keys(s3) == [f"clips/{SUB}/{JOB}.failed"]
    assert result == {"batchItemFailures": []}


def test_marker_is_internal_error_json_with_a_safe_message(s3):
    failure_handler.lambda_handler(event(record({"sub": SUB, "job_id": JOB})), None)

    obj = s3.get_object(Bucket=BUCKET, Key=f"clips/{SUB}/{JOB}.failed")
    marker = json.loads(obj["Body"].read())

    assert obj["ContentType"] == "application/json"
    assert marker["code"] == "internal"
    assert isinstance(marker["message"], str) and marker["message"]
    assert set(marker) == {"code", "message"}


def test_marker_does_not_leak_the_prompt(s3):
    failure_handler.lambda_handler(
        event(record({"sub": SUB, "job_id": JOB, "prompt": "secret prompt text"})), None
    )

    body = s3.get_object(Bucket=BUCKET, Key=f"clips/{SUB}/{JOB}.failed")["Body"].read()
    assert b"secret prompt text" not in body


def test_handles_a_batch(s3):
    result = failure_handler.lambda_handler(
        event(
            record({"sub": SUB, "job_id": JOB}, "m-1"),
            record({"sub": SUB, "job_id": JOB_2}, "m-2"),
        ),
        None,
    )

    assert keys(s3) == sorted([f"clips/{SUB}/{JOB}.failed", f"clips/{SUB}/{JOB_2}.failed"])
    assert result == {"batchItemFailures": []}


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "[]",
        "{}",
        json.dumps({"sub": SUB}),
        json.dumps({"job_id": JOB}),
        json.dumps({"sub": 5, "job_id": JOB}),
    ],
)
def test_unusable_message_is_dropped_without_a_marker_or_retry(s3, body):
    result = failure_handler.lambda_handler(event(record(body)), None)

    assert keys(s3) == []
    # Retrying a message that can never be parsed would loop forever.
    assert result == {"batchItemFailures": []}


@pytest.mark.parametrize(
    "sub,job_id",
    [
        ("../other-user", JOB),
        (SUB, "../../escape"),
        (SUB, JOB + "/extra"),
        ("*", JOB),
        (SUB, "not-a-uuid"),
        (SUB.replace("-", ""), JOB),  # 32 hex digits, no hyphens
        (f"urn:uuid:{SUB}", JOB),
        (SUB + "\n", JOB),
    ],
)
def test_ids_that_are_not_uuids_never_reach_an_s3_key(s3, sub, job_id):
    result = failure_handler.lambda_handler(
        event(record({"sub": sub, "job_id": job_id})), None
    )

    assert keys(s3) == []
    assert result == {"batchItemFailures": []}


def test_uppercase_uuid_is_used_as_given(s3):
    # The API writes whatever form it validated, so the marker must match it.
    upper = JOB.upper()
    failure_handler.lambda_handler(event(record({"sub": SUB, "job_id": upper})), None)

    assert keys(s3) == [f"clips/{SUB}/{upper}.failed"]


def test_s3_failure_is_reported_for_retry_and_does_not_block_the_rest(s3, monkeypatch):
    real_put = failure_handler.boto3.client("s3").put_object

    class FlakyClient:
        def put_object(self, **kwargs):
            if JOB in kwargs["Key"]:
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "boom"}}, "PutObject"
                )
            return real_put(**kwargs)

    monkeypatch.setattr(failure_handler, "_s3_client", lambda: FlakyClient())

    result = failure_handler.lambda_handler(
        event(
            record({"sub": SUB, "job_id": JOB}, "m-bad"),
            record({"sub": SUB, "job_id": JOB_2}, "m-good"),
        ),
        None,
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}
    assert keys(s3) == [f"clips/{SUB}/{JOB_2}.failed"]


def test_missing_bucket_setting_fails_loudly(monkeypatch):
    monkeypatch.delenv("CLIPS_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="CLIPS_BUCKET"):
        failure_handler.lambda_handler(event(record({"sub": SUB, "job_id": JOB})), None)


def test_logs_one_json_line_per_dead_lettered_job(s3, capsys):
    failure_handler.lambda_handler(event(record({"sub": SUB, "job_id": JOB})), None)

    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert {"event": "job_dead_lettered", "sub": SUB, "job_id": JOB} in lines
