"""Usage log lines for per-user cost attribution (spec 3H), written by the API and the worker.

One bare JSON object per line on stdout, so CloudWatch Logs Insights finds the fields
without parsing a prefix.
"""
import json


def log_usage(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)
