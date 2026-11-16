"""PLACEHOLDER for the API Lambda, so api.tf can be written and validated.

The real FastAPI app (behind Mangum) replaces this in build-order phase 2,
written test-first. Until then every request gets 501.
"""

import json


def lambda_handler(event, context):
    return {
        "statusCode": 501,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"message": "not implemented"}),
    }
