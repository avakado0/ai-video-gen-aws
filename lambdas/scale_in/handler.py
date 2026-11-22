"""Fast scale-in Lambda (plan 07).

Triggered by an EventBridge rule when an ECS task in the GPU cluster stops. By the time the last
worker task has stopped, the service's idle alarm has already set its desired count to 0. The
capacity provider would now wait about 15 more minutes before it lets the instance go, and the
instance then needs about 6 minutes to drain (measured 2026-11-22). This function does it at once,
but only if everything says idle:

- the queue has no visible and no in-flight message,
- the worker service has no desired, running or pending tasks.

Then it clears scale-in protection on the group's instances and sets the ASG's desired capacity
to 0. It never touches MinSize or MaxSize (the budget cut-off owns those, ADR 0004) and never
raises anything. If the check or a call fails the error is raised, so Lambda retries the
(idempotent) invocation. The capacity provider's own scale-in stays as the fallback.

A job that arrives in the seconds between the checks and the update is not lost: the wake alarm
raises the service to 1 and the capacity provider raises the ASG again, a delay of a minute or two.

Standalone module: handler is `handler.lambda_handler`.
"""

import json
import os

import boto3


class ScaleInFailed(Exception):
    """A call failed; raised so Lambda retries the invocation."""


def _setting(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _log(action, reason=None):
    print(json.dumps({"event": "scale_in", "action": action, "reason": reason}))


def _none(reason):
    _log("none", reason)
    return {"action": "none", "reason": reason}


def _queue_is_empty(sqs, queue_name):
    url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"]) == 0


def _service_is_idle(ecs, cluster, service):
    found = ecs.describe_services(cluster=cluster, services=[service])["services"]
    if not found:
        raise ScaleInFailed("worker service not found")
    s = found[0]
    return s["desiredCount"] == 0 and s["runningCount"] == 0 and s["pendingCount"] == 0


def lambda_handler(event, context):
    asg_name = _setting("ASG_NAME")
    cluster = _setting("ECS_CLUSTER")
    service = _setting("ECS_SERVICE")
    queue_name = _setting("QUEUE_NAME")

    if not _queue_is_empty(boto3.client("sqs"), queue_name):
        return _none("queue_not_empty")
    if not _service_is_idle(boto3.client("ecs"), cluster, service):
        return _none("service_active")

    autoscaling = boto3.client("autoscaling")
    groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])["AutoScalingGroups"]
    if not groups:
        raise ScaleInFailed("GPU auto scaling group not found")
    group = groups[0]
    if group["DesiredCapacity"] == 0:
        return _none("asg_already_zero")

    instance_ids = [i["InstanceId"] for i in group["Instances"] if i.get("ProtectedFromScaleIn")]
    if instance_ids:
        autoscaling.set_instance_protection(
            AutoScalingGroupName=asg_name, InstanceIds=instance_ids, ProtectedFromScaleIn=False
        )
    autoscaling.update_auto_scaling_group(AutoScalingGroupName=asg_name, DesiredCapacity=0)

    _log("scaled_in")
    return {"action": "scaled_in"}
