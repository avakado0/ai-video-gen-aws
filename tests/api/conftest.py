"""Shared fixtures for the API tests.

Every test goes through `handler.lambda_handler` with a real API Gateway HTTP API (v2)
event, so the JWT-claims path, Mangum and FastAPI are all exercised together. AWS is moto.
"""
import json
import pathlib
import types

import boto3
import pytest
from moto import mock_aws

from api import handler

REGION = "us-west-2"
BUCKET = "clips-test"
ROOT = pathlib.Path(__file__).resolve().parents[2]


class Response:
    def __init__(self, raw):
        self.status = raw["statusCode"]
        self.headers = raw.get("headers", {})
        self.text = raw.get("body", "") or ""

    def json(self):
        return json.loads(self.text)


def make_event(method, path, sub="user-1", body=None, claims=True):
    context = {"http": {"method": method, "path": path, "sourceIp": "1.2.3.4",
                        "protocol": "HTTP/1.1", "userAgent": "pytest"}}
    if claims:
        context["authorizer"] = {"jwt": {"claims": {"sub": sub}}}
    event = {"version": "2.0", "routeKey": "ANY /{proxy+}", "rawPath": path, "rawQueryString": "",
             "headers": {"content-type": "application/json"}, "requestContext": context,
             "isBase64Encoded": False}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


@pytest.fixture
def aws(monkeypatch):
    """Fake clips bucket and jobs queue, with the environment the Lambda reads."""
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                 "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="jobs")["QueueUrl"]
        monkeypatch.setenv("CLIPS_BUCKET", BUCKET)
        monkeypatch.setenv("QUEUE_URL", queue_url)
        monkeypatch.setenv("MAX_ACTIVE_JOBS", "3")
        monkeypatch.setenv("MODEL_MANIFEST", (ROOT / "model_manifest.json").read_text())
        handler.reset()
        yield types.SimpleNamespace(s3=s3, sqs=sqs, bucket=BUCKET, queue_url=queue_url)
        handler.reset()


@pytest.fixture
def call(aws):
    def _call(method, path, **kw):
        return Response(handler.lambda_handler(make_event(method, path, **kw), None))
    return _call
