"""Request rules: what the manifest allows. Pure functions, no AWS."""
import uuid


class RequestError(Exception):
    """A request the API refuses; `status` and `message` go straight to the client."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_job_id(value):
    """Return the job id if it is a canonical lowercase UUID, else raise.

    The id goes into S3 keys, so only the canonical form is accepted. That rules out path
    pieces, braces, URN prefixes and upper-case twins of the same id.
    """
    try:
        ok = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        ok = False
    if not ok:
        raise RequestError(400, "job_id must be a UUID")
    return value


def find_slot(manifest, name):
    for slot in manifest["inputs"]:
        if slot["name"] == name:
            return slot
    raise RequestError(400, f"unknown input slot: {name!r}")


def check_upload(manifest, slot_name, index, mime, size):
    """Check one file against its slot; return the slot definition."""
    slot = find_slot(manifest, slot_name)
    if not 0 <= index < slot.get("max_files", 1):
        raise RequestError(400, f"index must be from 0 to {slot.get('max_files', 1) - 1} for slot {slot_name!r}")
    if mime not in slot["mime"]:
        raise RequestError(400, f"mime type {mime!r} is not allowed for slot {slot_name!r}")
    if not 0 < size <= slot["max_bytes"]:
        raise RequestError(400, f"size must be from 1 to {slot['max_bytes']} bytes for slot {slot_name!r}")
    return slot
