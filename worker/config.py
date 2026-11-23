"""What the worker reads from its environment (the ECS task definition, ecs.tf)."""
import json
import logging
import os
from dataclasses import dataclass, field

from common import manifest as manifest_module

log = logging.getLogger("worker.config")


def _price_table(text):
    """{instance_type: USD per hour} from JSON, or {} if it is missing or malformed (best effort)."""
    try:
        table = json.loads(text or "{}")
        if isinstance(table, dict) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in table.values()):
            return {str(k): float(v) for k, v in table.items()}
    except ValueError:
        pass
    log.warning("ignoring SPOT_PRICE_FALLBACK: not a JSON map of numbers")
    return {}


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
    spot_price_fallback: dict = field(default_factory=dict)   # USD per hour by type, for the cost estimate

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            queue_url=env["QUEUE_URL"],
            clips_bucket=env["CLIPS_BUCKET"],
            models_dir=env["MODELS_DIR"],
            manifest=manifest_module.load(env["MODEL_MANIFEST"]),
            spot_price_fallback=_price_table(env.get("SPOT_PRICE_FALLBACK")),
        )
