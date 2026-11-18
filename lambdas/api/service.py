"""What the API does. Routes call this; this calls the store and the rules."""
import logging
import uuid
from datetime import datetime, timezone

from . import rules
from .usage import log_usage

log = logging.getLogger(__name__)


class JobService:
    def __init__(self, store, queue, manifest, max_active_jobs):
        self.store = store
        self.queue = queue
        self.manifest = manifest
        self.max_active_jobs = max_active_jobs

    def create_upload(self, sub, slot, index, mime, size, job_id=None):
        """Pre-signed POST for one input file. The first upload of a job mints its id."""
        job_id = rules.parse_job_id(job_id) if job_id is not None else str(uuid.uuid4())
        slot_def = rules.check_upload(self.manifest, slot, index, mime, size)
        key = self.store.input_key(sub, job_id, slot, index)
        post = self.store.presigned_post(key, mime, slot_def["max_bytes"])
        return {"job_id": job_id, "url": post["url"], "fields": post["fields"]}

    def submit(self, sub, prompt, params, inputs, job_id=None):
        """Validate, enforce the cap, write the .pending marker, queue the job. Returns the job id."""
        job_id = rules.parse_job_id(job_id) if job_id is not None else str(uuid.uuid4())
        prompt = rules.check_prompt(self.manifest, prompt)
        params = rules.normalize_params(self.manifest, params)
        counts = rules.check_input_counts(self.manifest, inputs)

        jobs = self.store.list_jobs(sub)
        if job_id in jobs:
            raise rules.RequestError(409, "this job was already submitted")
        active = sum(1 for files in jobs.values() if "pending" in files and not {"mp4", "failed"} & files.keys())
        if active >= self.max_active_jobs:
            raise rules.RequestError(429, f"You already have {active} jobs in progress. Wait for one to finish.")

        total = 0
        for slot, count in counts.items():
            for index in range(count):
                head = self.store.head(self.store.input_key(sub, job_id, slot, index))
                if head is None:
                    raise rules.RequestError(400, f"input {slot}[{index}] was not uploaded")
                total += head["ContentLength"]

        created = datetime.now(timezone.utc).isoformat()
        marker_key = self.store.clip_key(sub, job_id, "pending")
        self.store.put_json(marker_key, {"job_id": job_id, "created": created, "prompt": prompt,
                                         "params": params, "inputs": counts})
        try:
            self.queue.send({"job_id": job_id, "sub": sub, "prompt": prompt, "params": params,
                             "inputs": counts, "created": created})
        except Exception:
            log.exception("could not queue job %s", job_id)
            self.store.delete([marker_key])  # an orphan marker would count against the user's cap
            raise rules.RequestError(503, "Could not queue the job. Try again.")
        if counts:
            log_usage("inputs", sub=sub, job_id=job_id, bytes=total)
        return job_id
