"""Budget cut-off Lambda (spec 5, ADR 0004).

Subscribed to the SNS topic that the 100% budget alert publishes to. Sets the
GPU ASG's MinSize, MaxSize and DesiredCapacity to 0 so no new GPU instance
starts. Running instances finish their current job; the idle timeout then
stops them. Re-enable by hand-setting `gpu_max` after reviewing the bill.

Only budgets.amazonaws.com in this account may publish to the topic (topic
policy), so the message content is not inspected. Standalone module: handler
is `handler.lambda_handler`.
"""

import json
import os

import boto3


def _asg_name():
    name = os.environ.get("ASG_NAME")
    if not name:
        raise RuntimeError("ASG_NAME is not set")
    return name


def lambda_handler(event, context):
    name = _asg_name()
    # Errors propagate on purpose: SNS retries a failed invocation, and a cut-off
    # that silently failed is worse than a noisy one.
    boto3.client("autoscaling").update_auto_scaling_group(
        AutoScalingGroupName=name, MinSize=0, MaxSize=0, DesiredCapacity=0
    )
    print(json.dumps({"event": "budget_cutoff", "asg": name}))
