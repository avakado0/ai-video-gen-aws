"""What the API does. Routes call this; this calls the store and the rules."""
import uuid

from . import rules


class JobService:
    def __init__(self, store, manifest):
        self.store = store
        self.manifest = manifest

    def create_upload(self, sub, slot, index, mime, size, job_id=None):
        """Pre-signed POST for one input file. The first upload of a job mints its id."""
        job_id = rules.parse_job_id(job_id) if job_id is not None else str(uuid.uuid4())
        slot_def = rules.check_upload(self.manifest, slot, index, mime, size)
        key = self.store.input_key(sub, job_id, slot, index)
        post = self.store.presigned_post(key, mime, slot_def["max_bytes"])
        return {"job_id": job_id, "url": post["url"], "fields": post["fields"]}
