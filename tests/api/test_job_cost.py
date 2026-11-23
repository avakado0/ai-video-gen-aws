"""The estimated cost and try count of a job, summed from the worker's per-attempt records (plan 08)."""
import json

import pytest

from conftest import BUCKET

A = "6f1c1f0e-8a2b-4c3d-9e4f-5a6b7c8d9e0f"
B = "0b8e6f3a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def put(aws):
    def _put(job, ext, body=b"{}", sub="user-1"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        aws.s3.put_object(Bucket=BUCKET, Key=f"clips/{sub}/{job}.{ext}", Body=body)

    def attempt(job, n, cost, seconds=60.0, outcome="ok", sub="user-1"):
        _put(job, f"attempt-{n}.json", {"gpu_seconds": seconds, "cost_usd": cost, "outcome": outcome,
                                        "instance_type": "g5.2xlarge"}, sub)

    _put.attempt = attempt
    return _put


def keys(aws):
    return sorted(o["Key"] for o in aws.s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def get(call, job=A):
    return call("GET", f"/jobs/{job}").json()


def test_a_ready_clip_carries_the_summed_cost_and_the_try_count(call, put):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.0412, outcome="released")
    put.attempt(A, 2, 0.1, outcome="error")
    put.attempt(A, 3, 0.2501)

    out = get(call)

    assert out["cost_usd"] == pytest.approx(0.3913) and out["attempts"] == 3


def test_a_failed_job_shows_what_it_burned(call, put):
    put(A, "failed", {"code": "internal", "message": "It broke."})
    put.attempt(A, 1, 0.1, outcome="error")
    put.attempt(A, 2, 0.1, outcome="error")
    put.attempt(A, 3, 0.1, outcome="gave_up")

    out = get(call)

    assert out["status"] == "failed" and out["cost_usd"] == pytest.approx(0.3) and out["attempts"] == 3


def test_a_pending_job_shows_no_cost(call, put):
    put(A, "pending", {"created": "2026-11-18T10:00:00+00:00"})
    put.attempt(A, 1, 0.1, outcome="released")

    out = get(call)

    assert out["status"] == "pending" and "cost_usd" not in out and "attempts" not in out


def test_an_attempt_that_left_no_record_still_counts_as_a_try(call, put):
    # Attempt 1 was killed with no warning (no record); the receive count still went up.
    put(A, "mp4", b"video")
    put.attempt(A, 2, 0.25)

    out = get(call)

    assert out["attempts"] == 2 and out["cost_usd"] == pytest.approx(0.25)


def test_one_unknown_price_makes_the_total_unknown(call, put):
    # A partial sum would look like the real cost.
    put(A, "mp4", b"video")
    put.attempt(A, 1, None, outcome="released")
    put.attempt(A, 2, 0.25)

    out = get(call)

    assert out["cost_usd"] is None and out["attempts"] == 2


def test_an_unreadable_record_makes_the_total_unknown(call, put):
    put(A, "mp4", b"video")
    put(A, "attempt-1.json", b"{not json")
    put.attempt(A, 2, 0.25)

    out = get(call)

    assert out["cost_usd"] is None and out["attempts"] == 2


def test_a_clip_with_no_records_has_null_cost_and_tries(call, put):
    put(A, "mp4", b"video")

    out = get(call)

    assert out["status"] == "ready" and out["cost_usd"] is None and out["attempts"] is None


def test_the_cost_is_rounded_to_four_decimals(call, put):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.1 + 0.2)

    assert get(call)["cost_usd"] == 0.3


def test_records_of_other_users_and_jobs_are_never_counted(call, put):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.25)
    put.attempt(B, 1, 9.0)
    put.attempt(A, 1, 7.0, sub="user-2")

    out = get(call)

    assert out["cost_usd"] == 0.25 and out["attempts"] == 1


def test_records_do_not_make_phantom_jobs_in_the_list(call, put):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.25)
    put.attempt(B, 1, 0.25)                      # the job itself is gone: only a leftover record

    assert [j["id"] for j in call("GET", "/jobs").json()["jobs"]] == [A]


def test_the_list_stays_light(call, put):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.25)

    job = call("GET", "/jobs").json()["jobs"][0]

    assert "cost_usd" not in job and "attempts" not in job


def test_deleting_a_job_removes_its_records(call, put, aws):
    put(A, "mp4", b"video")
    put.attempt(A, 1, 0.1, outcome="released")
    put.attempt(A, 2, 0.25)
    put(B, "mp4", b"video")
    put.attempt(B, 1, 0.25)

    assert call("DELETE", f"/jobs/{A}").status == 204
    assert keys(aws) == [f"clips/user-1/{B}.attempt-1.json", f"clips/user-1/{B}.mp4"]


def test_cancelling_a_pending_job_removes_its_records_too(call, put, aws):
    put(A, "pending", {"created": "2026-11-18T10:00:00+00:00"})
    put.attempt(A, 1, 0.1, outcome="released")

    assert call("DELETE", f"/jobs/{A}").status == 204
    assert keys(aws) == []


def test_a_user_cannot_delete_another_users_records(call, put, aws):
    put(A, "mp4", b"video", sub="user-2")
    put.attempt(A, 1, 0.25, sub="user-2")

    assert call("DELETE", f"/jobs/{A}").status == 404
    assert len(keys(aws)) == 2
