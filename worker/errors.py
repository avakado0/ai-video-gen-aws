"""The ways a job attempt can end other than success."""


class Aborted(Exception):
    """The generator stopped early because `should_abort()` turned true."""


class BadMessage(Exception):
    """A queue message that cannot be tied to a job (no usable job_id or sub). Nobody to tell."""


class PermanentJobError(Exception):
    """The job can never succeed. Becomes a `.failed` marker the user sees, so `message` must be
    safe to show: no paths, no stack traces."""

    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_json(self):
        out = {"code": self.code}
        if self.field:
            out["field"] = self.field
        out["message"] = self.message
        return out
