"""GET /jobs, GET /jobs/{id} and DELETE /jobs/{id}: job state read from the S3 keys alone."""
import json

import pytest

from conftest import BUCKET

A = "6f1c1f0e-8a2b-4c3d-9e4f-5a6b7c8d9e0f"
B = "0b8e6f3a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
C = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"


@pytest.fixture
def put(aws):
    def _put(job, ext, body=b"{}", sub="user-1"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        aws.s3.put_object(Bucket=BUCKET, Key=f"clips/{sub}/{job}.{ext}", Body=body)

    def marker(job, created="2026-11-18T10:00:00+00:00", prompt="a cat", sub="user-1"):
        _put(job, "pending", {"job_id": job, "created": created, "prompt": prompt, "params": {}, "inputs": {}}, sub)

    _put.marker = marker
    return _put


def keys(aws):
    return sorted(o["Key"] for o in aws.s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


# --- GET /jobs ----------------------------------------------------------------

def test_list_is_empty_for_a_new_user(call):
    res = call("GET", "/jobs")

    assert res.status == 200 and res.json() == {"jobs": []}


def test_list_groups_keys_into_pending_ready_and_failed(call, put):
    put.marker(A)
    put.marker(B, prompt="a dog"); put(B, "mp4", b"video")
    put.marker(C, prompt="boom"); put(C, "failed", {"code": "invalid_input", "field": "prompt", "message": "Bad."})

    jobs = {j["id"]: j for j in call("GET", "/jobs").json()["jobs"]}

    assert jobs[A] == {"id": A, "status": "pending", "prompt": "a cat", "created": "2026-11-18T10:00:00+00:00"}
    assert jobs[B]["status"] == "ready" and jobs[B]["prompt"] == "a dog"
    assert jobs[C]["status"] == "failed"
    assert jobs[C]["reason"] == {"code": "invalid_input", "field": "prompt", "message": "Bad."}


def test_list_carries_no_download_urls(call, put):
    put.marker(A); put(A, "mp4", b"video")

    assert "url" not in call("GET", "/jobs").json()["jobs"][0]


def test_list_is_newest_first(call, put):
    put.marker(A, created="2026-11-18T10:00:00+00:00")
    put.marker(B, created="2026-11-18T12:00:00+00:00")
    put.marker(C, created="2026-11-18T11:00:00+00:00")

    assert [j["id"] for j in call("GET", "/jobs").json()["jobs"]] == [B, C, A]


def test_list_shows_only_the_callers_jobs(call, put):
    put.marker(A)
    put.marker(B, sub="user-2")

    assert [j["id"] for j in call("GET", "/jobs").json()["jobs"]] == [A]
    assert [j["id"] for j in call("GET", "/jobs", sub="user-2").json()["jobs"]] == [B]


def test_list_survives_odd_keys_and_broken_markers(call, put, aws):
    put(A, "pending", b"{not json")
    put(B, "mp4", b"video")                      # no marker at all: still listed, just no prompt
    aws.s3.put_object(Bucket=BUCKET, Key="clips/user-1/notes.txt", Body=b"x")
    aws.s3.put_object(Bucket=BUCKET, Key="inputs/user-1/whatever/image/0", Body=b"x")

    jobs = {j["id"]: j for j in call("GET", "/jobs").json()["jobs"]}

    assert set(jobs) == {A, B}
    assert jobs[A]["status"] == "pending" and "prompt" not in jobs[A]
    assert jobs[B]["status"] == "ready" and jobs[B]["created"]  # falls back to the object's own time


def test_a_failed_job_with_an_unreadable_reason_still_says_failed(call, put):
    put(A, "failed", b"{not json")

    job = call("GET", "/jobs").json()["jobs"][0]

    assert job["status"] == "failed" and job["reason"]["code"] == "internal"


# --- GET /jobs/{id} -----------------------------------------------------------

def test_get_pending(call, put):
    put.marker(A)

    res = call("GET", f"/jobs/{A}")

    assert res.status == 200
    assert res.json() == {"id": A, "status": "pending", "prompt": "a cat", "created": "2026-11-18T10:00:00+00:00"}


def test_get_ready_returns_a_fresh_six_hour_url(call, put):
    put.marker(A); put(A, "mp4", b"video")

    out = call("GET", f"/jobs/{A}").json()

    assert out["status"] == "ready"
    assert out["url"].startswith("https://") and f"clips/user-1/{A}.mp4" in out["url"]
    assert "X-Amz-Expires=21600" in out["url"]


def test_get_failed_returns_the_reason(call, put):
    put.marker(A); put(A, "failed", {"code": "internal", "message": "It broke."})

    out = call("GET", f"/jobs/{A}").json()

    assert out["status"] == "failed" and out["reason"] == {"code": "internal", "message": "It broke."}


def test_a_clip_beats_a_marker_and_a_stale_failure(call, put):
    put.marker(A); put(A, "failed", {"code": "internal", "message": "x"}); put(A, "mp4", b"video")

    assert call("GET", f"/jobs/{A}").json()["status"] == "ready"


def test_get_unknown_job_is_404(call):
    assert call("GET", f"/jobs/{A}").status == 404


def test_users_cannot_read_each_others_jobs(call, put):
    put.marker(A, sub="user-2"); put(A, "mp4", b"video", sub="user-2")

    assert call("GET", f"/jobs/{A}").status == 404


@pytest.mark.parametrize("bad", ["nope", "..", "%2e%2e", A.upper()])
def test_job_id_in_the_path_must_be_a_uuid(call, bad):
    assert call("GET", f"/jobs/{bad}").status in (400, 404)
    assert call("DELETE", f"/jobs/{bad}").status in (400, 404)


# --- DELETE /jobs/{id} --------------------------------------------------------

def test_delete_removes_clip_marker_and_failure(call, put, aws):
    put.marker(A); put(A, "mp4", b"video"); put(A, "failed", {"code": "x", "message": "y"})
    put.marker(B)

    res = call("DELETE", f"/jobs/{A}")

    assert res.status == 204
    assert keys(aws) == [f"clips/user-1/{B}.pending"]


def test_delete_logs_the_clip_size_for_egress_attribution(call, put, capsys):
    put.marker(A); put(A, "mp4", b"x" * 1234)

    call("DELETE", f"/jobs/{A}")

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert {"event": "download", "sub": "user-1", "job_id": A, "bytes": 1234} in lines


def test_deleting_a_pending_job_cancels_it(call, put, aws):
    put.marker(A)

    assert call("DELETE", f"/jobs/{A}").status == 204
    assert keys(aws) == []  # the worker will find no marker and skip the job


def test_delete_unknown_job_is_404(call):
    assert call("DELETE", f"/jobs/{A}").status == 404


def test_users_cannot_delete_each_others_jobs(call, put, aws):
    put.marker(A, sub="user-2"); put(A, "mp4", b"video", sub="user-2")

    assert call("DELETE", f"/jobs/{A}").status == 404
    assert len(keys(aws)) == 2
