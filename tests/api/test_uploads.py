"""POST /uploads: a pre-signed POST for one input file, checked against the manifest slot."""
import base64
import json
import uuid

import pytest

from conftest import IMAGE_TO_VIDEO, ROOT

JOB = "6f1c1f0e-8a2b-4c3d-9e4f-5a6b7c8d9e0f"


@pytest.fixture(autouse=True)
def i2v(use_manifest):
    use_manifest(IMAGE_TO_VIDEO)


def upload(call, **over):
    body = {"slot": "image", "index": 0, "mime": "image/png", "size": 100}
    body.update(over)
    return call("POST", "/uploads", body=body)


def policy_of(res):
    return json.loads(base64.b64decode(res.json()["fields"]["policy"]))


def test_first_upload_mints_a_job_id_and_signs_the_input_key(call):
    res = upload(call)

    assert res.status == 200
    out = res.json()
    assert str(uuid.UUID(out["job_id"])) == out["job_id"]
    assert out["fields"]["key"] == f"inputs/user-1/{out['job_id']}/image/0"
    assert out["url"].startswith("https://") and "clips-test" in out["url"]


def test_later_uploads_reuse_the_job_id_and_use_their_own_index(call):
    res = upload(call, slot="refs", index=2, mime="image/png", size=10, job_id=JOB)

    assert res.json()["job_id"] == JOB
    assert res.json()["fields"]["key"] == f"inputs/user-1/{JOB}/refs/2"


def test_the_sub_comes_from_the_claims_not_the_body(call):
    res = upload(call, sub="someone-else", job_id=JOB)  # a body field named sub is ignored

    assert res.json()["fields"]["key"].startswith(f"inputs/user-1/{JOB}/")


def test_policy_pins_size_type_and_key(call):
    res = upload(call, size=100)
    conditions = policy_of(res)["conditions"]

    assert ["content-length-range", 1, 1000] in conditions
    assert {"Content-Type": "image/png"} in conditions
    assert {"key": res.json()["fields"]["key"]} in conditions


def test_policy_size_limit_is_the_slots_not_the_requests(call):
    res = upload(call, slot="refs", mime="image/png", size=1)

    assert ["content-length-range", 1, 500] in policy_of(res)["conditions"]


@pytest.mark.parametrize("over, fragment", [
    ({"slot": "audio"}, "slot"),
    ({"slot": "Image"}, "slot"),
    ({"index": 1}, "index"),               # image slot holds one file
    ({"slot": "refs", "index": 3, "mime": "image/png"}, "index"),
    ({"index": -1}, "index"),
    ({"mime": "image/gif"}, "mime"),
    ({"mime": "video/mp4"}, "mime"),
    ({"size": 0}, "size"),
    ({"size": 1001}, "size"),
    ({"size": -5}, "size"),
    ({"job_id": "not-a-uuid"}, "job_id"),
    ({"job_id": "../../etc"}, "job_id"),
    ({"job_id": JOB.upper()}, "job_id"),
    ({"job_id": "{" + JOB + "}"}, "job_id"),
])
def test_bad_requests_are_400_with_a_readable_detail(call, over, fragment):
    res = upload(call, **over)

    assert res.status == 400
    assert isinstance(res.json()["detail"], str) and fragment in res.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"slot": "image"}, {"slot": "image", "index": "0", "mime": "image/png", "size": 1},
                                  {"slot": "image", "index": True, "mime": "image/png", "size": 1},
                                  {"slot": "image", "index": 0, "mime": "image/png", "size": 1.5}])
def test_missing_or_mistyped_fields_are_400(call, body):
    res = call("POST", "/uploads", body=body)

    assert res.status == 400
    assert isinstance(res.json()["detail"], str)


def test_a_text_only_model_has_no_slots(call, use_manifest):
    # The parked Wan 2.1 manifest is the text-only one now; the live 5B manifest has an image slot.
    use_manifest(json.loads((ROOT / "model_manifest.wan2-1.json").read_text()))

    assert upload(call).status == 400
