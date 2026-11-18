"""The clips bucket, from the worker's side. Key layout is the API's (spec 3C)."""
import json

from botocore.exceptions import ClientError

NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


class ClipStore:
    def __init__(self, s3, bucket):
        self.s3 = s3
        self.bucket = bucket

    def _clip_key(self, sub, job_id, ext):
        return f"clips/{sub}/{job_id}.{ext}"

    def _input_prefix(self, sub, job_id):
        return f"inputs/{sub}/{job_id}/"

    def marker_exists(self, sub, job_id):
        """Is the job still wanted? Only a clear "not found" says no. Any other error (a 403 from
        a policy slip, a network blip) is raised, so a live job is never dropped by mistake."""
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._clip_key(sub, job_id, "pending"))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in NOT_FOUND:
                return False
            raise

    def put_failed(self, sub, job_id, error):
        self.s3.put_object(Bucket=self.bucket, Key=self._clip_key(sub, job_id, "failed"),
                           Body=json.dumps(error).encode(), ContentType="application/json")

    def upload_clip(self, sub, job_id, path):
        # put_object, not upload_file: no multipart, so the task role needs only s3:PutObject
        with open(path, "rb") as f:
            self.s3.put_object(Bucket=self.bucket, Key=self._clip_key(sub, job_id, "mp4"),
                               Body=f, ContentType="video/mp4")

    def fetch_inputs(self, job, dest):
        """Download the job's uploaded inputs into `dest`; return {slot: [Path, ...]} in index order."""
        got = {}
        for slot, count in job.inputs.items():
            folder = dest / slot
            folder.mkdir(parents=True, exist_ok=True)
            got[slot] = []
            for i in range(count):
                path = folder / str(i)
                self.s3.download_file(self.bucket, f"{self._input_prefix(job.sub, job.job_id)}{slot}/{i}", str(path))
                got[slot].append(path)
        return got

    def delete_inputs(self, sub, job_id):
        """Remove everything uploaded for the job. Lists the prefix, so it works from any state."""
        prefix = self._input_prefix(sub, job_id)
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
        for page in pages:
            for obj in page.get("Contents", []):
                self.s3.delete_object(Bucket=self.bucket, Key=obj["Key"])
