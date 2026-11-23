"""The clips bucket: where job state lives (spec 3B, 3C). No database; the keys are the state.

    clips/{sub}/{job_id}.pending   JSON: created, prompt, params
    clips/{sub}/{job_id}.mp4       the finished clip
    clips/{sub}/{job_id}.failed    JSON: code, field?, message
    clips/{sub}/{job_id}.attempt-{n}.json   JSON: gpu_seconds, cost_usd, outcome, instance_type (worker, plan 08)
    inputs/{sub}/{job_id}/{slot}/{index}   uploaded input files
"""

import json

from botocore.exceptions import ClientError

UPLOAD_EXPIRES = 900  # 15 minutes to start the upload
DOWNLOAD_EXPIRES = 21600  # 6 hours; best effort, the API mints a fresh URL on every GET (spec 3C)
MISSING = ("404", "NoSuchKey", "NotFound")
EXTENSIONS = ("pending", "mp4", "failed")


class JobStore:
    def __init__(self, s3, bucket):
        self.s3 = s3
        self.bucket = bucket

    @staticmethod
    def clip_key(sub, job_id, ext):
        return f"clips/{sub}/{job_id}.{ext}"

    @staticmethod
    def input_key(sub, job_id, slot, index):
        return f"inputs/{sub}/{job_id}/{slot}/{index}"

    def presigned_post(self, key, mime, max_bytes):
        """A pre-signed POST (not PUT) so the policy can pin the size and the type."""
        return self.s3.generate_presigned_post(
            self.bucket, key,
            Fields={"Content-Type": mime},
            Conditions=[{"Content-Type": mime}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=UPLOAD_EXPIRES,
        )

    def head(self, key):
        """The object's metadata, or None when it does not exist (needs s3:ListBucket, else 403)."""
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in MISSING:
                return None
            raise

    def put_json(self, key, data):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(data).encode(),
                           ContentType="application/json")

    def delete(self, keys):
        self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys]})

    def list_jobs(self, sub):
        """{job_id: {ext: LastModified}} for one user, from the `clips/{sub}/` listing alone."""
        prefix = f"clips/{sub}/"
        jobs = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                job_id, _, ext = obj["Key"][len(prefix):].partition(".")
                if ext in EXTENSIONS:
                    jobs.setdefault(job_id, {})[ext] = obj["LastModified"]
        return jobs

    def list_attempts(self, sub, job_id):
        """[(n, record)] for the job's per-attempt usage records, oldest first. A record that cannot
        be read comes back as {}, so the caller can tell it exists."""
        prefix = f"clips/{sub}/{job_id}.attempt-"
        found = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                number = obj["Key"][len(prefix):].removesuffix(".json")
                if number.isdigit():
                    found.append((int(number), obj["Key"]))
        return [(n, self.read_json(key) or {}) for n, key in sorted(found)]

    def attempt_keys(self, sub, job_id):
        prefix = f"clips/{sub}/{job_id}.attempt-"
        return [obj["Key"]
                for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
                for obj in page.get("Contents", [])]

    def read_json(self, key):
        """The JSON object stored at key, or None when it is missing or not readable."""
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            data = json.loads(body)
        except ClientError as e:
            if e.response["Error"]["Code"] in MISSING:
                return None
            raise
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def presigned_get(self, key):
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=DOWNLOAD_EXPIRES)
