"""One job, as the API put it on the queue."""
import json
from dataclasses import dataclass, field, replace

from common import rules

from .errors import BadMessage, PermanentJobError


@dataclass(frozen=True)
class Job:
    job_id: str
    sub: str
    prompt: str
    params: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)   # slot -> number of files uploaded
    created: str = ""
    receive_count: int = 1

    @classmethod
    def from_message(cls, body, receive_count):
        """Parse the queue message. Anything that stops us telling the user (no job_id or sub we
        can put in a key) is a BadMessage; the content is checked later by `checked`."""
        try:
            msg = json.loads(body)
        except (TypeError, ValueError):
            raise BadMessage("body is not JSON")
        if not isinstance(msg, dict):
            raise BadMessage("body is not an object")
        try:
            job_id = rules.parse_job_id(msg.get("job_id"))
        except rules.RequestError:
            raise BadMessage("job_id is not a canonical UUID")
        sub = msg.get("sub")
        if not isinstance(sub, str) or not sub or "/" in sub:
            raise BadMessage("sub is missing or unsafe")
        prompt, params, inputs = msg.get("prompt"), msg.get("params"), msg.get("inputs")
        if not isinstance(prompt, str):
            raise BadMessage("prompt is not a string")
        if not isinstance(params, dict):
            raise BadMessage("params is not an object")
        if not isinstance(inputs, dict) or not all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in inputs.items()):
            raise BadMessage("inputs is not a slot-to-count object")
        created = msg.get("created")
        return cls(job_id=job_id, sub=sub, prompt=prompt, params=params, inputs=inputs,
                   created=created if isinstance(created, str) else "", receive_count=receive_count)

    def checked(self, manifest):
        """The job with prompt, params and inputs re-checked against the manifest (the API did it
        once; the manifest may have changed since). Raises PermanentJobError when it no longer fits."""
        try:
            return replace(self,
                           prompt=rules.check_prompt(manifest, self.prompt),
                           params=rules.normalize_params(manifest, self.params),
                           inputs=rules.check_input_counts(manifest, self.inputs))
        except rules.RequestError as e:
            raise PermanentJobError("invalid_request", e.message)
