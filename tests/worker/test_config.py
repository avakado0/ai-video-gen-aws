import pathlib

import pytest

from worker.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV = {"QUEUE_URL": "https://sqs/q", "CLIPS_BUCKET": "clips", "MODELS_DIR": "/data/models",
       "MODEL_MANIFEST": (ROOT / "model_manifest.json").read_text()}


def test_reads_the_environment_the_task_definition_sets():
    cfg = Config.from_env(ENV)
    assert (cfg.queue_url, cfg.clips_bucket, cfg.models_dir) == ("https://sqs/q", "clips", "/data/models")
    assert cfg.manifest["model"] == "wan2-1-t2v-1-3b"


def test_defaults_follow_the_spec():
    cfg = Config.from_env(ENV)
    assert cfg.visibility_timeout == 180
    assert cfg.heartbeat_seconds == 60
    assert cfg.poll_wait_seconds == 20
    assert not hasattr(cfg, "steps")   # steps come from the quality preset (worker/wan.py)


def test_a_broken_manifest_stops_the_worker_at_start():
    with pytest.raises(Exception):
        Config.from_env({**ENV, "MODEL_MANIFEST": "{}"})


def test_missing_variable_is_an_error():
    with pytest.raises(KeyError):
        Config.from_env({k: v for k, v in ENV.items() if k != "QUEUE_URL"})
