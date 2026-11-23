import pathlib

import pytest

from worker.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV = {"QUEUE_URL": "https://sqs/q", "CLIPS_BUCKET": "clips", "MODELS_DIR": "/data/models",
       "MODEL_MANIFEST": (ROOT / "model_manifest.json").read_text()}


def test_reads_the_environment_the_task_definition_sets():
    cfg = Config.from_env(ENV)
    assert (cfg.queue_url, cfg.clips_bucket, cfg.models_dir) == ("https://sqs/q", "clips", "/data/models")
    assert cfg.manifest["model"] == "wan2-2-ti2v-5b"


def test_defaults_follow_the_spec():
    cfg = Config.from_env(ENV)
    assert cfg.visibility_timeout == 180
    assert cfg.heartbeat_seconds == 60
    assert cfg.poll_wait_seconds == 20
    assert not hasattr(cfg, "steps")   # steps come from the quality preset (worker/wan22.py)


def test_a_broken_manifest_stops_the_worker_at_start():
    with pytest.raises(Exception):
        Config.from_env({**ENV, "MODEL_MANIFEST": "{}"})


def test_missing_variable_is_an_error():
    with pytest.raises(KeyError):
        Config.from_env({k: v for k, v in ENV.items() if k != "QUEUE_URL"})


def test_spot_price_fallback_is_a_json_map_and_optional():
    assert Config.from_env(ENV).spot_price_fallback == {}
    cfg = Config.from_env({**ENV, "SPOT_PRICE_FALLBACK": '{"g5.2xlarge": 0.9}'})
    assert cfg.spot_price_fallback == {"g5.2xlarge": 0.9}


def test_a_broken_spot_price_fallback_is_ignored_not_fatal():
    # Pricing is best effort; a typo in a Terraform variable must not stop the worker.
    for bad in ("not json", "[1, 2]", '{"g5.2xlarge": "cheap"}', ""):
        assert Config.from_env({**ENV, "SPOT_PRICE_FALLBACK": bad}).spot_price_fallback == {}
