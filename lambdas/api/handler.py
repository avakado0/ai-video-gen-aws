"""Lambda entry point: API Gateway HTTP API (payload v2) -> Mangum -> FastAPI."""
import json
import logging

from mangum import Mangum

from .app import create_app
from .settings import Settings

log = logging.getLogger()
log.setLevel(logging.INFO)

_handler = None


def reset():
    """Forget the cached app (tests change the environment between runs)."""
    global _handler
    _handler = None


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
            _handler = Mangum(create_app(Settings.from_env().manifest), lifespan="off")
        return _handler(event, context)
    except Exception:
        log.exception("unhandled error")  # the traceback stays in CloudWatch, not in the response
        return _internal_error()
