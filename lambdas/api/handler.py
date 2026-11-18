"""PLACEHOLDER for the API Lambda, so api.tf can be written and validated.

The real FastAPI app (behind Mangum) replaces this in build-order phase 2,
written test-first. Until then every request gets 501.
"""

import json


def lambda_handler(event, context):
    # CORS preflight (OPTIONS route, no authorizer): API Gateway adds the CORS
    # headers from cors_configuration; the browser only needs a 2xx.
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 204}
    return {
        "statusCode": 501,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"message": "not implemented"}),
    }
