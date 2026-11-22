"""`python -m worker` picks the generator from the manifest's model name (plan 06)."""
import pytest

from worker import __main__ as entry
from worker.wan import WanGenerator
from worker.wan22 import Wan22Generator


def test_the_5b_model_gets_the_wan22_generator():
    gen = entry.make_generator("/data/models", "wan2-2-ti2v-5b")
    assert isinstance(gen, Wan22Generator)
    assert gen.path == "/data/models/wan2-2-ti2v-5b"


def test_the_parked_1_3b_model_still_gets_its_generator():
    assert isinstance(entry.make_generator("/data/models", "wan2-1-t2v-1-3b"), WanGenerator)


def test_an_unknown_model_fails_at_start_with_a_clear_error():
    with pytest.raises(SystemExit) as e:
        entry.make_generator("/data/models", "some-other-model")
    message = str(e.value)
    assert "some-other-model" in message
    assert "wan2-2-ti2v-5b" in message and "wan2-1-t2v-1-3b" in message


def test_every_registered_model_has_a_manifest_in_the_repo():
    import json
    import pathlib
    root = pathlib.Path(entry.__file__).resolve().parents[1]
    live = json.loads((root / "model_manifest.json").read_text())["model"]
    assert live in entry.GENERATORS
