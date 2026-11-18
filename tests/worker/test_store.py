import json

import pytest
from botocore.exceptions import ClientError

from worker.job import Job
from worker.store import ClipStore

JOB_ID = "6f1c1c2e-8a53-4d0b-9d0e-5b7d2f0a1c11"
SUB = "user-1"


def job(**inputs):
    return Job(job_id=JOB_ID, sub=SUB, prompt="p", inputs=inputs)


@pytest.fixture
def store(aws):
    return ClipStore(aws.s3, aws.bucket)


def put(aws, key, body=b"x"):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=body)


def keys(aws):
    return sorted(o["Key"] for o in aws.s3.list_objects_v2(Bucket=aws.bucket).get("Contents", []))


def test_marker_exists_true_and_false(aws, store):
    assert store.marker_exists(SUB, JOB_ID) is False
    put(aws, f"clips/{SUB}/{JOB_ID}.pending")
    assert store.marker_exists(SUB, JOB_ID) is True


def test_marker_check_does_not_hide_other_errors(aws, store):
    # A 403 must not look like "cancelled": the worker would drop a live job.
    class Denied:
        def head_object(self, **kw):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
    with pytest.raises(ClientError):
        ClipStore(Denied(), aws.bucket).marker_exists(SUB, JOB_ID)


def test_put_failed_writes_the_json_the_api_reads(aws, store):
    store.put_failed(SUB, JOB_ID, {"code": "invalid_request", "message": "bad"})
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"clips/{SUB}/{JOB_ID}.failed")["Body"].read()
    assert json.loads(body) == {"code": "invalid_request", "message": "bad"}


def test_upload_clip_lands_at_the_clip_key(aws, store, tmp_path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"video")
    store.upload_clip(SUB, JOB_ID, f)
    obj = aws.s3.get_object(Bucket=aws.bucket, Key=f"clips/{SUB}/{JOB_ID}.mp4")
    assert obj["Body"].read() == b"video" and obj["ContentType"] == "video/mp4"


def test_fetch_inputs_downloads_each_file_by_slot(aws, store, tmp_path):
    put(aws, f"inputs/{SUB}/{JOB_ID}/image/0", b"a")
    put(aws, f"inputs/{SUB}/{JOB_ID}/image/1", b"b")
    got = store.fetch_inputs(job(image=2), tmp_path)
    assert [p.read_bytes() for p in got["image"]] == [b"a", b"b"]


def test_delete_inputs_removes_only_this_jobs_inputs(aws, store):
    put(aws, f"inputs/{SUB}/{JOB_ID}/image/0")
    put(aws, f"inputs/{SUB}/other-job/image/0")
    put(aws, f"clips/{SUB}/{JOB_ID}.pending")
    store.delete_inputs(SUB, JOB_ID)
    assert keys(aws) == [f"clips/{SUB}/{JOB_ID}.pending", f"inputs/{SUB}/other-job/image/0"]
