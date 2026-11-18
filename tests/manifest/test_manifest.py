"""The capability manifest (spec 3B): the v1 file, the front end's mock manifests, and the validator."""
import copy
import json
import pathlib

import pytest

from api import manifest

ROOT = pathlib.Path(__file__).resolve().parents[2]
V1 = ROOT / "model_manifest.json"
MOCKS = sorted((ROOT / "frontend/js/mock/manifests").glob("*.json"))


def load_v1():
    return json.loads(V1.read_text())


def slot(**over):
    base = {"name": "image", "label": "Image", "kind": "image", "required": True,
            "mime": ["image/png"], "max_bytes": 1000}
    return {**base, **over}


def with_(**over):
    m = load_v1()
    m.update(over)
    return m


# --- the real v1 file ---------------------------------------------------------

def test_v1_manifest_is_valid():
    assert manifest.validate(load_v1()) == []


def test_v1_is_text_to_video_with_three_params():
    m = load_v1()
    assert m.get("prompt", "required") == "required"
    assert m["inputs"] == []
    assert [p["name"] for p in m["params"]] == ["seed", "duration_seconds", "negative_prompt"]


def test_v1_param_details():
    p = {x["name"]: x for x in load_v1()["params"]}
    assert p["seed"]["type"] == "int" and "default" not in p["seed"]
    assert p["duration_seconds"]["type"] == "enum" and p["duration_seconds"]["values"] == ["3", "5"]
    assert p["negative_prompt"]["type"] == "string" and p["negative_prompt"]["max_chars"] == 200
    assert p["negative_prompt"]["default"] == ""


def test_v1_fits_in_a_lambda_environment_variable():
    # Lambda allows 4 KB for all variables together; the manifest shares them with a few others.
    assert len(json.dumps(load_v1(), separators=(",", ":"))) < 2000


# --- the front end's mock manifests must stay valid ---------------------------

@pytest.mark.parametrize("path", MOCKS, ids=lambda p: p.name)
def test_mock_manifests_are_valid(path):
    assert manifest.validate(json.loads(path.read_text())) == []


# --- the validator rejects bad manifests --------------------------------------

def bad(m, fragment):
    errors = manifest.validate(m)
    assert any(fragment in e for e in errors), errors


def test_prompt_mode_must_be_known():
    bad(with_(prompt="maybe"), "prompt")


def test_prompt_defaults_to_required():
    m = load_v1()
    m.pop("prompt", None)
    assert manifest.validate(m) == []


def test_prompt_limit_must_be_a_positive_int():
    bad(with_(limits={"prompt_max_chars": 0}), "prompt_max_chars")
    bad(with_(limits={}), "prompt_max_chars")


def test_inputs_must_be_a_list():
    bad(with_(inputs={}), "inputs")


@pytest.mark.parametrize("name", ["Image", "my-image", "a/b", "", "a b"])
def test_slot_name_is_lowercase_digits_underscore(name):
    bad(with_(inputs=[slot(name=name)]), "name")


def test_slot_names_are_unique():
    bad(with_(inputs=[slot(), slot()]), "duplicate")


def test_slot_kind_is_image_audio_or_video():
    bad(with_(inputs=[slot(kind="pdf")]), "kind")


def test_slot_needs_mime_and_max_bytes():
    bad(with_(inputs=[slot(mime=[])]), "mime")
    bad(with_(inputs=[slot(max_bytes=0)]), "max_bytes")


def test_slot_max_files_is_at_least_one():
    bad(with_(inputs=[slot(max_files=0)]), "max_files")
    assert manifest.validate(with_(inputs=[slot(max_files=4)])) == []


def test_max_seconds_only_for_audio_and_video():
    bad(with_(inputs=[slot(max_seconds=5)]), "max_seconds")
    ok = slot(kind="audio", mime=["audio/wav"], max_seconds=60)
    assert manifest.validate(with_(inputs=[ok])) == []


def test_param_type_must_be_known():
    bad(with_(params=[{"name": "x", "type": "complex"}]), "type")


def test_param_names_are_unique_and_not_reserved():
    p = {"name": "x", "type": "bool", "default": True}
    bad(with_(params=[p, p]), "duplicate")
    bad(with_(params=[{"name": "prompt", "type": "bool"}]), "reserved")


def test_enum_needs_string_values_and_a_default_among_them():
    bad(with_(params=[{"name": "x", "type": "enum"}]), "values")
    bad(with_(params=[{"name": "x", "type": "enum", "values": [3, 5]}]), "values")
    bad(with_(params=[{"name": "x", "type": "enum", "values": ["a"], "default": "b"}]), "default")


def test_int_and_float_need_ordered_bounds_and_a_default_inside_them():
    bad(with_(params=[{"name": "x", "type": "int", "min": 5, "max": 1}]), "min")
    bad(with_(params=[{"name": "x", "type": "int", "min": 0, "max": 9, "default": 10}]), "default")
    bad(with_(params=[{"name": "x", "type": "int", "min": 0}]), "max")
    bad(with_(params=[{"name": "x", "type": "int", "min": 0, "max": 9, "default": 1.5}]), "default")


def test_string_needs_max_chars_and_a_default_that_fits():
    bad(with_(params=[{"name": "x", "type": "string"}]), "max_chars")
    bad(with_(params=[{"name": "x", "type": "string", "max_chars": 2, "default": "abc"}]), "default")


def test_bool_default_must_be_bool():
    bad(with_(params=[{"name": "x", "type": "bool", "default": "yes"}]), "default")


def test_top_level_must_be_an_object():
    assert manifest.validate([]) != []


def test_validate_does_not_mutate_its_input():
    m = load_v1()
    before = copy.deepcopy(m)
    manifest.validate(m)
    assert m == before


# --- loading ------------------------------------------------------------------

def test_load_parses_valid_json():
    assert manifest.load(V1.read_text()) == load_v1()


def test_load_raises_with_every_problem_listed():
    broken = json.dumps(with_(prompt="maybe", inputs=[slot(kind="pdf")]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.load(broken)
    assert "prompt" in str(exc.value) and "kind" in str(exc.value)


def test_load_raises_on_invalid_json():
    with pytest.raises(manifest.ManifestError):
        manifest.load("{not json")
