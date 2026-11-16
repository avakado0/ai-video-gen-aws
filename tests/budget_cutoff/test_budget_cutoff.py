"""Budget cut-off Lambda: the 100% budget alert zeroes the GPU ASG (ADR 0004)."""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from budget_cutoff import handler as cutoff

ASG = "aivideogen-gpu"
SNS_EVENT = {
    "Records": [
        {"EventSource": "aws:sns", "Sns": {"Message": "Budget Notification: 100% of $20"}}
    ]
}


@pytest.fixture
def asg(monkeypatch):
    monkeypatch.setenv("ASG_NAME", ASG)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    with mock_aws():
        client = boto3.client("autoscaling", region_name="us-west-2")
        client.create_launch_configuration(
            LaunchConfigurationName="lc", ImageId="ami-12345678", InstanceType="t3.micro"
        )
        client.create_auto_scaling_group(
            AutoScalingGroupName=ASG,
            LaunchConfigurationName="lc",
            MinSize=0,
            MaxSize=2,
            DesiredCapacity=2,
            AvailabilityZones=["us-west-2a"],
        )
        yield client


def group(client):
    return client.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG])[
        "AutoScalingGroups"
    ][0]


def test_zeroes_max_and_desired(asg):
    cutoff.lambda_handler(SNS_EVENT, None)

    g = group(asg)
    assert (g["MinSize"], g["MaxSize"], g["DesiredCapacity"]) == (0, 0, 0)


def test_is_idempotent(asg):
    cutoff.lambda_handler(SNS_EVENT, None)
    cutoff.lambda_handler(SNS_EVENT, None)

    assert group(asg)["MaxSize"] == 0


def test_works_when_the_group_is_already_at_zero(asg):
    asg.update_auto_scaling_group(AutoScalingGroupName=ASG, MaxSize=0, DesiredCapacity=0)

    cutoff.lambda_handler(SNS_EVENT, None)

    assert group(asg)["MaxSize"] == 0


def test_touches_only_the_configured_group(asg):
    asg.create_auto_scaling_group(
        AutoScalingGroupName="other",
        LaunchConfigurationName="lc",
        MinSize=0,
        MaxSize=3,
        DesiredCapacity=1,
        AvailabilityZones=["us-west-2a"],
    )

    cutoff.lambda_handler(SNS_EVENT, None)

    other = asg.describe_auto_scaling_groups(AutoScalingGroupNames=["other"])[
        "AutoScalingGroups"
    ][0]
    assert (other["MaxSize"], other["DesiredCapacity"]) == (3, 1)


def test_missing_group_raises_so_the_invocation_is_retried(asg):
    asg.delete_auto_scaling_group(AutoScalingGroupName=ASG, ForceDelete=True)

    with pytest.raises(ClientError):
        cutoff.lambda_handler(SNS_EVENT, None)


def test_missing_setting_fails_loudly(monkeypatch):
    monkeypatch.delenv("ASG_NAME", raising=False)

    with pytest.raises(RuntimeError, match="ASG_NAME"):
        cutoff.lambda_handler(SNS_EVENT, None)


def test_logs_the_cut_off(asg, capsys):
    cutoff.lambda_handler(SNS_EVENT, None)

    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert {"event": "budget_cutoff", "asg": ASG} in lines
