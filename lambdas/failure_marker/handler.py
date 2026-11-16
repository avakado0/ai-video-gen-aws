"""Failure-marker Lambda (spec 3D).

Triggered by the dead-letter queue. For each dead-lettered job it writes
clips/{sub}/{job_id}.failed so the UI shows "failed" instead of "pending"
forever. This covers deaths the worker could not report itself (out of memory,
crash, Spot loss without warning).

Standalone module: no sibling imports, so the folder zips as-is and the Lambda
handler is `handler.lambda_handler`.
"""

import json
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# 36-character hyphenated hex only. `sub` and `job_id` become part of an S3 key,
# so anything else (path separators, wildcards, newlines) is refused. The API
# applies the same rule before it writes, so keys always match.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# The UI shows this to the user, so it stays generic: no prompt, no internals.
_MARKER = {"code": "internal", "message": "The job could not be completed."}


def _s3_client():
    return boto3.client("s3")


def _bucket():
    bucket = os.environ.get("CLIPS_BUCKET")
    if not bucket:
        raise RuntimeError("CLIPS_BUCKET is not set")
    return bucket


def _log(**fields):
    print(json.dumps(fields))


def _parse(body):
    """Return (sub, job_id) from a message body, or None if unusable."""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    sub, job_id = payload.get("sub"), payload.get("job_id")
    for value in (sub, job_id):
        if not isinstance(value, str) or not _UUID.fullmatch(value):
            return None
    return sub, job_id


class FailureMarkerWriter:
    def __init__(self, s3, bucket):
        self._s3 = s3
        self._bucket = bucket

    def write(self, sub, job_id):
        self._s3.put_object(
            Bucket=self._bucket,
            Key=f"clips/{sub}/{job_id}.failed",
            Body=json.dumps(_MARKER).encode(),
            ContentType="application/json",
        )


def lambda_handler(event, context):
    writer = FailureMarkerWriter(_s3_client(), _bucket())
    failures = []

    for rec in event.get("Records", []):
        ids = _parse(rec.get("body"))
        if ids is None:
            # Retrying cannot fix a message that can never be parsed, so drop
            # it (it is consumed) and leave a trace in the logs.
            _log(event="dead_letter_unusable", message_id=rec.get("messageId"))
            continue

        sub, job_id = ids
        try:
            writer.write(sub, job_id)
        except (ClientError, BotoCoreError):
            # Transient S3 trouble: report only this record so SQS retries it.
            _log(event="marker_write_failed", sub=sub, job_id=job_id)
            failures.append({"itemIdentifier": rec["messageId"]})
            continue

        _log(event="job_dead_lettered", sub=sub, job_id=job_id)

    return {"batchItemFailures": failures}
