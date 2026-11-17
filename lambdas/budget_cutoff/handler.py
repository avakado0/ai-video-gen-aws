"""Budget cut-off Lambda (spec 3G, ADR 0004).

Subscribed to the SNS topic that the 100% budget alert publishes to. Applies
three brakes, in this order:

1. The worker service's autoscaling target goes to min 0 / max 0, so the
   wake-up and target-tracking policies cannot raise the task count again.
2. The ECS service's desired count goes to 0. The running task gets SIGTERM,
   releases its message (ChangeMessageVisibility 0), and ECS lifts managed
   termination protection from the instance.
3. The GPU ASG's MinSize, MaxSize and DesiredCapacity go to 0, so the
   unprotected instance is terminated and no new one starts.

Zeroing the ASG alone is not enough: with a queue backlog the service keeps
wanting its task, and ECS keeps the instance protected from scale-in.

Each brake is tried even if an earlier one fails. If any failed, the handler
raises after trying all three, so SNS retries the invocation; every brake is
idempotent. Re-enable by hand after reviewing the bill (ADR 0004).

Only budgets.amazonaws.com in this account may publish to the topic (topic
policy), so the message content is not inspected. Standalone module: handler
is `handler.lambda_handler`.
"""

import json
import os

import boto3


class CutoffIncomplete(Exception):
    """At least one brake failed; raised so SNS retries the invocation."""


def _setting(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def lambda_handler(event, context):
    asg = _setting("ASG_NAME")
    cluster = _setting("ECS_CLUSTER")
    service = _setting("ECS_SERVICE")
    resource_id = f"service/{cluster}/{service}"

    brakes = [
        (
            "service_autoscaling",
            lambda: boto3.client("application-autoscaling").register_scalable_target(
                ServiceNamespace="ecs",
                ResourceId=resource_id,
                ScalableDimension="ecs:service:DesiredCount",
                MinCapacity=0,
                MaxCapacity=0,
            ),
        ),
        (
            "ecs_service",
            lambda: boto3.client("ecs").update_service(
                cluster=cluster, service=service, desiredCount=0
            ),
        ),
        (
            "asg",
            lambda: boto3.client("autoscaling").update_auto_scaling_group(
                AutoScalingGroupName=asg, MinSize=0, MaxSize=0, DesiredCapacity=0
            ),
        ),
    ]

    failed = []
    for name, apply in brakes:
        try:
            apply()
        except Exception as exc:  # keep going: the other brakes still matter
            failed.append(name)
            print(json.dumps({"event": "budget_cutoff_error", "brake": name, "error": type(exc).__name__}))

    print(json.dumps({"event": "budget_cutoff", "asg": asg, "service": resource_id, "failed": failed}))
    if failed:
        raise CutoffIncomplete(f"brakes failed: {', '.join(failed)}")
