"""The clips bucket: where job state lives (spec 3B, 3C). No database; the keys are the state.

    clips/{sub}/{job_id}.pending   JSON: created, prompt, params
    clips/{sub}/{job_id}.mp4       the finished clip
    clips/{sub}/{job_id}.failed    JSON: code, field?, message
    inputs/{sub}/{job_id}/{slot}/{index}   uploaded input files
"""

UPLOAD_EXPIRES = 900  # 15 minutes to start the upload


class JobStore:
    def __init__(self, s3, bucket):
        self.s3 = s3
        self.bucket = bucket

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
