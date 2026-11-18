import json

import pytest

from worker.protection import NoProtection, TaskProtection, protection_from_env


class FakeEcs:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or []

    def update_task_protection(self, **kw):
        self.calls.append(kw)
        return {"protectedTasks": [], "failures": self.failures}


def test_protect_and_release_call_ecs_for_this_task():
    ecs = FakeEcs()
    p = TaskProtection(ecs, "cluster-1", "arn:aws:ecs:us-west-2:1:task/cluster-1/abc")
    p.protect()
    p.release()
    assert [c["protectionEnabled"] for c in ecs.calls] == [True, False]
    assert all(c["cluster"] == "cluster-1" and c["tasks"] == ["arn:aws:ecs:us-west-2:1:task/cluster-1/abc"]
               for c in ecs.calls)
    assert ecs.calls[0]["expiresInMinutes"] == 120     # a crashed worker cannot pin an instance forever


def test_a_reported_failure_is_an_error():
    ecs = FakeEcs(failures=[{"arn": "x", "reason": "TASK_NOT_VALID"}])
    with pytest.raises(RuntimeError):
        TaskProtection(ecs, "c", "t").protect()


def test_reads_cluster_and_task_arn_from_the_task_metadata_endpoint(http):
    http.routes[("GET", "/v4/abc/task")] = (200, json.dumps({"Cluster": "aivideogen", "TaskARN": "arn:task/1"}))
    ecs = FakeEcs()
    p = protection_from_env({"ECS_CONTAINER_METADATA_URI_V4": f"{http.url}/v4/abc"}, ecs)
    p.protect()
    assert ecs.calls[0]["cluster"] == "aivideogen" and ecs.calls[0]["tasks"] == ["arn:task/1"]


def test_outside_ecs_protection_is_a_no_op():
    p = protection_from_env({}, FakeEcs())
    assert isinstance(p, NoProtection)
    p.protect()
    p.release()
