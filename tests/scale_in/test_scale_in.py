"""Fast scale-in Lambda (plan 07): when the last worker task has stopped and the queue is empty,
take the GPU instance down now instead of waiting for the capacity provider's own 15 minutes.

It acts only when everything says idle: no visible message, no in-flight message, and the
worker service has no desired, running or pending tasks. It clears scale-in protection on the
group's instances and sets the ASG's desired capacity to 0. It never touches MinSize or MaxSize
(the budget cut-off owns those, ADR 0004). moto does not model termination, so these tests pin
the calls; the measuring run on AWS proves the instance really goes.
"""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from scale_in import handler as scale_in

REGION = "us-west-2"
ASG = "aivideogen-gpu"
CLUSTER = "aivideogen"
SERVICE = "aivideogen-worker"
QUEUE = "aivideogen-jobs"
STOPPED_EVENT = {
    "source": "aws.ecs",
    "detail-type": "ECS Task State Change",
    "detail": {"lastStatus": "STOPPED", "desiredStatus": "STOPPED"},
}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("ASG_NAME", ASG)
    monkeypatch.setenv("ECS_CLUSTER", CLUSTER)
    monkeypatch.setenv("ECS_SERVICE", SERVICE)
    monkeypatch.setenv("QUEUE_NAME", QUEUE)
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
            DesiredCapacity=1,
            AvailabilityZones=["us-west-2a"],
            NewInstancesProtectedFromScaleIn=True,
        )
        ecs = boto3.client("ecs", region_name=REGION)
        ecs.create_cluster(clusterName=CLUSTER)
        ecs.register_task_definition(
            family="worker", containerDefinitions=[{"name": "w", "image": "x", "memory": 128}]
        )
        ecs.create_service(
            cluster=CLUSTER, serviceName=SERVICE, taskDefinition="worker", desiredCount=0
        )
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName=QUEUE)["QueueUrl"]
        yield {"asg": asg, "ecs": ecs, "sqs": sqs, "queue_url": url}


def group(client):
    return client.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG])["AutoScalingGroups"][0]


def protected(client):
    return [i["ProtectedFromScaleIn"] for i in group(client)["Instances"]]


def untouched(aws):
    g = group(aws["asg"])
    return g["DesiredCapacity"] == 1 and all(protected(aws["asg"]))


# ---- idle: take the instance down ---------------------------------------------------------


def test_idle_sets_the_asg_desired_capacity_to_zero(aws):
    assert group(aws["asg"])["DesiredCapacity"] == 1
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert group(aws["asg"])["DesiredCapacity"] == 0
    assert result["action"] == "scaled_in"


def test_idle_clears_scale_in_protection_first(aws, monkeypatch):
    assert protected(aws["asg"]) == [True]
    calls = []
    real = boto3.client

    def client(name, *a, **k):
        c = real(name, *a, **k)
        if name == "autoscaling":
            for method in ("set_instance_protection", "update_auto_scaling_group"):
                original = getattr(c, method)

                def recording(original=original, method=method, **kw):
                    calls.append(method)
                    return original(**kw)

                setattr(c, method, recording)
        return c

    monkeypatch.setattr(scale_in.boto3, "client", client)
    scale_in.lambda_handler(STOPPED_EVENT, None)
    # moto drops the instance when desired goes to 0, so pin the order of the calls, not the end state
    assert calls == ["set_instance_protection", "update_auto_scaling_group"]


def test_idle_never_changes_min_or_max_size(aws):
    scale_in.lambda_handler(STOPPED_EVENT, None)
    g = group(aws["asg"])
    assert (g["MinSize"], g["MaxSize"]) == (0, 2)


def test_an_asg_already_at_zero_is_left_alone(aws):
    aws["asg"].update_auto_scaling_group(AutoScalingGroupName=ASG, DesiredCapacity=0)
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert result["action"] == "none"
    assert result["reason"] == "asg_already_zero"


# ---- not idle: do nothing -----------------------------------------------------------------


def test_a_visible_message_means_not_idle(aws):
    aws["sqs"].send_message(QueueUrl=aws["queue_url"], MessageBody="{}")
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert result == {"action": "none", "reason": "queue_not_empty"}
    assert untouched(aws)


def test_an_in_flight_message_means_not_idle(aws):
    aws["sqs"].send_message(QueueUrl=aws["queue_url"], MessageBody="{}")
    aws["sqs"].receive_message(QueueUrl=aws["queue_url"], VisibilityTimeout=300)
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert result["reason"] == "queue_not_empty"
    assert untouched(aws)


def test_a_service_that_wants_a_task_means_not_idle(aws):
    aws["ecs"].update_service(cluster=CLUSTER, service=SERVICE, desiredCount=1)
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert result == {"action": "none", "reason": "service_active"}
    assert untouched(aws)


def test_running_or_pending_tasks_mean_not_idle(aws, monkeypatch):
    real = boto3.client

    def client(name, *a, **k):
        c = real(name, *a, **k)
        if name == "ecs":
            describe = c.describe_services

            def describe_services(**kw):
                out = describe(**kw)
                out["services"][0]["runningCount"] = 1
                return out

            c.describe_services = describe_services
        return c

    monkeypatch.setattr(scale_in.boto3, "client", client)
    result = scale_in.lambda_handler(STOPPED_EVENT, None)
    assert result["reason"] == "service_active"
    assert untouched(aws)


# ---- failure and logging ------------------------------------------------------------------


def test_an_aws_error_is_raised_so_the_invocation_is_retried(aws):
    aws["asg"].delete_auto_scaling_group(AutoScalingGroupName=ASG, ForceDelete=True)
    with pytest.raises((ClientError, scale_in.ScaleInFailed)):
        scale_in.lambda_handler(STOPPED_EVENT, None)


def test_a_missing_setting_is_an_error(aws, monkeypatch):
    monkeypatch.delenv("QUEUE_NAME")
    with pytest.raises(RuntimeError, match="QUEUE_NAME"):
        scale_in.lambda_handler(STOPPED_EVENT, None)


def test_the_log_line_says_what_happened_and_carries_no_ids(aws, capsys):
    scale_in.lambda_handler(STOPPED_EVENT, None)
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "scale_in"
    assert line["action"] == "scaled_in"
    assert "instance" not in json.dumps(line).lower()
