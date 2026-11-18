"""Configuration the API Lambda reads from its environment (set in api.tf)."""
import os
from dataclasses import dataclass

from . import manifest as manifest_module


@dataclass(frozen=True)
class Settings:
    clips_bucket: str
    queue_url: str
    max_active_jobs: int
    submit_delay_seconds: int
    manifest: dict

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        # The undo window: a new job's queue message stays invisible this long, so a Cancel
        # inside it costs nothing. SQS accepts 0 to 900 seconds.
        delay = int(env.get("SUBMIT_DELAY_SECONDS", "30"))
        if not 0 <= delay <= 900:
            raise ValueError("SUBMIT_DELAY_SECONDS must be from 0 to 900")
        return cls(
            clips_bucket=env["CLIPS_BUCKET"],
            queue_url=env["QUEUE_URL"],
            max_active_jobs=int(env.get("MAX_ACTIVE_JOBS", "3")),
            submit_delay_seconds=delay,
            manifest=manifest_module.load(env["MODEL_MANIFEST"]),
        )
