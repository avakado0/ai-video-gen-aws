"""What the API does. Routes call this; this calls the store and the rules."""
import logging
import uuid
from datetime import datetime, timezone

from common import rules
from common.usage import log_usage

log = logging.getLogger(__name__)


class JobService:
    def __init__(self, store, queue, manifest, max_active_jobs, submit_delay_seconds=0):
        self.store = store
        self.queue = queue
        self.manifest = manifest
        self.max_active_jobs = max_active_jobs
        self.submit_delay_seconds = submit_delay_seconds

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
                             "inputs": counts, "created": created}, self.submit_delay_seconds)
        except Exception:
            log.exception("could not queue job %s", job_id)
            self.store.delete([marker_key])  # an orphan marker would count against the user's cap
            raise rules.RequestError(503, "Could not queue the job. Try again.")
        if counts:
            log_usage("inputs", sub=sub, job_id=job_id, bytes=total)
        return job_id

    # --- reading job state -------------------------------------------------------

    def _view(self, sub, job_id, status, modified):
        """One job as the client sees it. `modified` backs up `created` when the marker is gone."""
        marker = self.store.read_json(self.store.clip_key(sub, job_id, "pending")) or {}
        view = {"id": job_id, "status": status}
        if isinstance(marker.get("prompt"), str):
            view["prompt"] = marker["prompt"]
        created = marker.get("created")
        view["created"] = created if isinstance(created, str) else modified.isoformat()
        if status == "failed":
            reason = self.store.read_json(self.store.clip_key(sub, job_id, "failed"))
            view["reason"] = reason or {"code": "internal", "message": "The job failed."}
        return view

    def list_jobs(self, sub):
        """The caller's jobs, newest first. Found by listing `clips/{sub}/`: there is no database."""
        jobs = []
        for job_id, files in self.store.list_jobs(sub).items():
            status = "ready" if "mp4" in files else "failed" if "failed" in files else "pending"
            jobs.append(self._view(sub, job_id, status, min(files.values())))
        return sorted(jobs, key=lambda j: j["created"], reverse=True)

    def get_job(self, sub, job_id):
        """ready (with a fresh pre-signed URL), failed (with the reason) or pending; 404 if none."""
        rules.parse_job_id(job_id)
        for ext, status in (("mp4", "ready"), ("failed", "failed"), ("pending", "pending")):
            key = self.store.clip_key(sub, job_id, ext)
            head = self.store.head(key)
            if head is not None:
                view = self._view(sub, job_id, status, head["LastModified"])
                if status == "ready":
                    view["url"] = self.store.presigned_get(key)
                return view
        raise rules.RequestError(404, "Job not found")

    def delete_job(self, sub, job_id):
        """After a confirmed download (or to cancel): remove the clip, the failure and the marker."""
        rules.parse_job_id(job_id)
        found = {}
        for ext in ("mp4", "failed", "pending"):
            key = self.store.clip_key(sub, job_id, ext)
            head = self.store.head(key)
            if head is not None:
                found[key] = (ext, head)
        if not found:
            raise rules.RequestError(404, "Job not found")
        self.store.delete(list(found))
        for ext, head in found.values():
            if ext == "mp4":
                log_usage("download", sub=sub, job_id=job_id, bytes=head["ContentLength"])
