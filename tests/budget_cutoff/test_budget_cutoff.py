"""Budget cut-off Lambda: the 100% budget alert stops GPU work (ADR 0004).

Three brakes, in this order: the worker service's autoscaling max goes to 0
(so autoscaling cannot raise the task count again), the service's desired count
goes to 0 (the task gets SIGTERM and releases its message, which lifts managed
termination protection), and the ASG goes to 0. moto does not model scale-in
or termination protection, so these tests pin the calls; phase 7 proves the
instance actually stops.
"""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from budget_cutoff import handler as cutoff

REGION = "us-west-2"
ASG = "aivideogen-gpu"
CLUSTER = "aivideogen"
SERVICE = "aivideogen-worker"
RESOURCE_ID = f"service/{CLUSTER}/{SERVICE}"
SNS_EVENT = {
    "Records": [
        {"EventSource": "aws:sns", "Sns": {"Message": "Budget Notification: 100% of $20"}}
    ]
}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("ASG_NAME", ASG)
    monkeypatch.setenv("ECS_CLUSTER", CLUSTER)
    monkeypatch.setenv("ECS_SERVICE", SERVICE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        asg = boto3.client("autoscaling", region_name=REGION)
        asg.create_launch_configuration(
            LaunchConfigurationName="lc", ImageId="ami-12345678", InstanceType="t3.micro"
        )
        asg.create_auto_scaling_group(
            AutoScalingGroupName=ASG,
            LaunchConfigurationName="lc",
            MinSize=0,
            MaxSize=2,
            DesiredCapacity=2,
            AvailabilityZones=["us-west-2a"],
        )
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.create_cluster(clusterName=CLUSTER)
        ecs.register_task_definition(
            family="worker", containerDefinitions=[{"name": "w", "image": "x", "memory": 128}]
        )
        ecs.create_service(
            cluster=CLUSTER, serviceName=SERVICE, taskDefinition="worker", desiredCount=1
        )
        aas = boto3.client("application-autoscaling", region_name=REGION)
        aas.register_scalable_target(
            ServiceNamespace="ecs",
            ResourceId=RESOURCE_ID,
            ScalableDimension="ecs:service:DesiredCount",
            MinCapacity=0,
            MaxCapacity=2,
        )
        yield {"asg": asg, "ecs": ecs, "aas": aas}


def group(client, name=ASG):
    return client.describe_auto_scaling_groups(AutoScalingGroupNames=[name])[
        "AutoScalingGroups"
    ][0]


def service_desired(ecs):
    return ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0][
        "desiredCount"
    ]


def scaling_target(aas):
    targets = aas.describe_scalable_targets(
        ServiceNamespace="ecs", ResourceIds=[RESOURCE_ID]
    )["ScalableTargets"]
    return targets[0]


# ---- ASG -----------------------------------------------------------------------


def test_zeroes_the_asg(aws):
    cutoff.lambda_handler(SNS_EVENT, None)

    g = group(aws["asg"])
    assert (g["MinSize"], g["MaxSize"], g["DesiredCapacity"]) == (0, 0, 0)


def test_touches_only_the_configured_group(aws):
    aws["asg"].create_auto_scaling_group(
        AutoScalingGroupName="other",
        LaunchConfigurationName="lc",
        MinSize=0,
        MaxSize=3,
        DesiredCapacity=1,
        AvailabilityZones=["us-west-2a"],
    )

    cutoff.lambda_handler(SNS_EVENT, None)

    other = group(aws["asg"], "other")
    assert (other["MaxSize"], other["DesiredCapacity"]) == (3, 1)


# ---- ECS service and its autoscaling target -------------------------------------


def test_zeroes_the_worker_service(aws):
    cutoff.lambda_handler(SNS_EVENT, None)

    assert service_desired(aws["ecs"]) == 0


def test_zeroes_the_service_autoscaling_max(aws):
    cutoff.lambda_handler(SNS_EVENT, None)

    t = scaling_target(aws["aas"])
    assert (t["MinCapacity"], t["MaxCapacity"]) == (0, 0)


# ---- Robustness ------------------------------------------------------------------


def test_is_idempotent(aws):
    cutoff.lambda_handler(SNS_EVENT, None)
    cutoff.lambda_handler(SNS_EVENT, None)

    assert group(aws["asg"])["MaxSize"] == 0
    assert service_desired(aws["ecs"]) == 0
    assert scaling_target(aws["aas"])["MaxCapacity"] == 0


def test_works_when_everything_is_already_at_zero(aws):
    aws["asg"].update_auto_scaling_group(AutoScalingGroupName=ASG, MaxSize=0, DesiredCapacity=0)
    aws["ecs"].update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)

    cutoff.lambda_handler(SNS_EVENT, None)

    assert group(aws["asg"])["MaxSize"] == 0


def test_a_failing_brake_does_not_skip_the_others_and_still_raises(aws, monkeypatch):
    # Service not found: the ECS brake fails, but the ASG must still be zeroed,
    # and the invocation must fail so SNS retries it.
    monkeypatch.setenv("ECS_SERVICE", "missing")

    with pytest.raises(cutoff.CutoffIncomplete):
        cutoff.lambda_handler(SNS_EVENT, None)

    assert group(aws["asg"])["MaxSize"] == 0


def test_missing_group_raises_so_the_invocation_is_retried(aws):
    aws["asg"].delete_auto_scaling_group(AutoScalingGroupName=ASG, ForceDelete=True)

    with pytest.raises(cutoff.CutoffIncomplete):
        cutoff.lambda_handler(SNS_EVENT, None)

    assert service_desired(aws["ecs"]) == 0


@pytest.mark.parametrize("var", ["ASG_NAME", "ECS_CLUSTER", "ECS_SERVICE"])
def test_missing_setting_fails_loudly(monkeypatch, var):
    monkeypatch.setenv("ASG_NAME", ASG)
    monkeypatch.setenv("ECS_CLUSTER", CLUSTER)
    monkeypatch.setenv("ECS_SERVICE", SERVICE)
    monkeypatch.delenv(var)

    with pytest.raises(RuntimeError, match=var):
        cutoff.lambda_handler(SNS_EVENT, None)


def test_logs_each_brake(aws, capsys):
    cutoff.lambda_handler(SNS_EVENT, None)

    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert {
        "event": "budget_cutoff",
        "asg": ASG,
        "service": RESOURCE_ID,
        "failed": [],
    } in lines


def test_failed_brake_is_named_in_the_log(aws, capsys, monkeypatch):
    monkeypatch.setenv("ECS_SERVICE", "missing")

    with pytest.raises(cutoff.CutoffIncomplete):
        cutoff.lambda_handler(SNS_EVENT, None)

    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert any(l.get("failed") == ["service_autoscaling", "ecs_service"] for l in lines)


def test_cutoff_incomplete_is_not_a_client_error():
    # SNS retries on any exception; a distinct type keeps the log readable.
    assert not issubclass(cutoff.CutoffIncomplete, ClientError)
