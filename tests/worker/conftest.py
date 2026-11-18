"""Fixtures for the worker tests: a fake clips bucket and jobs queue (moto)."""
import boto3
import pytest
from moto import mock_aws

REGION = "us-west-2"
BUCKET = "clips-test"


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                 "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="jobs", Attributes={"VisibilityTimeout": "180"})["QueueUrl"]
        yield type("AWS", (), {"s3": s3, "sqs": sqs, "bucket": BUCKET, "queue_url": url})
