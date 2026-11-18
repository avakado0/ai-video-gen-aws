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


@pytest.fixture
def http():
    """A tiny local HTTP server standing in for IMDS and the ECS metadata endpoint.

    `server.routes[(method, path)] = (status, body)`; `server.seen` records (method, path, headers).
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            self.server.seen.append((self.command, self.path, dict(self.headers)))
            status, body = self.server.routes.get((self.command, self.path), (404, ""))
            if callable(body):
                status, body = body()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

        do_GET = do_PUT = _answer

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.routes, server.seen = {}, []
    server.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()
