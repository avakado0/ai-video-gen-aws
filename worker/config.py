"""What the worker reads from its environment (the ECS task definition, ecs.tf)."""
import os
from dataclasses import dataclass

from common import manifest as manifest_module


@dataclass(frozen=True)
class Config:
    queue_url: str
    clips_bucket: str
    models_dir: str
    manifest: dict
    visibility_timeout: int = 180   # spec 3D: 3 minutes, extended by the heartbeat
    heartbeat_seconds: int = 60
    spot_poll_seconds: int = 5
    poll_wait_seconds: int = 20     # SQS long poll
    steps: int = 30                 # diffusion steps; the Kaggle spike confirms the number

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            queue_url=env["QUEUE_URL"],
            clips_bucket=env["CLIPS_BUCKET"],
            models_dir=env["MODELS_DIR"],
            manifest=manifest_module.load(env["MODEL_MANIFEST"]),
            steps=int(env.get("STEPS", "30")),
        )
