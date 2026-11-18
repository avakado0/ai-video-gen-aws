"""Lambda entry point: API Gateway HTTP API (payload v2) -> Mangum -> FastAPI."""
import json
import logging

import boto3
from botocore.config import Config
from mangum import Mangum

from .app import create_app
from .service import JobService
from .settings import Settings
from .store import JobStore

log = logging.getLogger()
log.setLevel(logging.INFO)

_handler = None


def reset():
    """Forget the cached app (tests change the environment between runs)."""
    global _handler
    _handler = None


def _build():
    settings = Settings.from_env()
    # Signature v4 and virtual-host addressing: pre-signed URLs and POSTs for a bucket in a
    # non-default region must point at the regional endpoint, or browsers get redirected.
    s3 = boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    service = JobService(JobStore(s3, settings.clips_bucket), settings.manifest)
    return Mangum(create_app(settings.manifest, service), lifespan="off")


def _internal_error():
    return {"statusCode": 500, "headers": {"content-type": "application/json"},
            "body": json.dumps({"detail": "Internal error"})}


def lambda_handler(event, context):
    # CORS preflight (OPTIONS route, no authorizer): API Gateway adds the CORS headers from
    # cors_configuration, the browser only needs a 2xx. Answered before anything else, so it
    # never depends on configuration or AWS.
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 204}
    global _handler
    try:
        if _handler is None:
            _handler = _build()
        return _handler(event, context)
    except Exception:
        log.exception("unhandled error")  # the traceback stays in CloudWatch, not in the response
        return _internal_error()
