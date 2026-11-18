"""ECS task scale-in protection: while a job runs, the autoscaler must not stop this instance.

Needs ecs:UpdateTaskProtection on the task role (iam.tf has it). The cluster and task ARN come
from the task metadata endpoint the ECS agent exposes to every container.
"""
import json
import urllib.request


class NoProtection:
    """Outside ECS (a laptop, a test): nothing to protect."""

    def protect(self):
        pass

    def release(self):
        pass


class TaskProtection:
    # A crashed worker cannot pin an instance for longer than this; a job takes under 20 minutes.
    EXPIRES_MINUTES = 120

    def __init__(self, ecs, cluster, task_arn):
        self.ecs, self.cluster, self.task_arn = ecs, cluster, task_arn

    def _set(self, enabled):
        args = {"cluster": self.cluster, "tasks": [self.task_arn], "protectionEnabled": enabled}
        if enabled:
            args["expiresInMinutes"] = self.EXPIRES_MINUTES
        resp = self.ecs.update_task_protection(**args)
        if resp.get("failures"):
            raise RuntimeError(f"update_task_protection failed: {resp['failures']}")

    def protect(self):
        self._set(True)

    def release(self):
        self._set(False)


def protection_from_env(env, ecs):
    uri = env.get("ECS_CONTAINER_METADATA_URI_V4")
    if not uri:
        return NoProtection()
    with urllib.request.urlopen(f"{uri}/task", timeout=2) as resp:
        meta = json.load(resp)
    return TaskProtection(ecs, meta["Cluster"], meta["TaskARN"])
