"""Configuration the API Lambda reads from its environment (set in api.tf)."""
import os
from dataclasses import dataclass

from . import manifest as manifest_module


@dataclass(frozen=True)
class Settings:
    clips_bucket: str
    queue_url: str
    max_active_jobs: int
    manifest: dict

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            clips_bucket=env["CLIPS_BUCKET"],
            queue_url=env["QUEUE_URL"],
            max_active_jobs=int(env.get("MAX_ACTIVE_JOBS", "3")),
            manifest=manifest_module.load(env["MODEL_MANIFEST"]),
        )
