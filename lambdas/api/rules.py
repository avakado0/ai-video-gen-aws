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


def check_prompt(manifest, prompt):
    """Return the trimmed prompt, or raise if the manifest's prompt mode or limit is broken."""
    prompt = prompt.strip()
    mode = manifest.get("prompt", "required")
    limit = manifest["limits"]["prompt_max_chars"]
    if mode == "none" and prompt:
        raise RequestError(400, "prompt is not accepted by this model")
    if mode == "required" and not prompt:
        raise RequestError(400, "prompt is required")
    if len(prompt) > limit:
        raise RequestError(400, f"prompt must be at most {limit} characters")
    return prompt


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _param_value(p, value):
    name, kind = p["name"], p["type"]
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "int":
        ok = _is_int(value)
    elif kind == "float":
        ok = _is_number(value)
    elif kind == "enum":
        ok = isinstance(value, str) and value in p["values"]
    else:
        ok = isinstance(value, str) and len(value) <= p["max_chars"]
    if not ok:
        raise RequestError(400, f"param {name!r} is not a valid {kind} value")
    if kind in ("int", "float"):
        return max(p["min"], min(p["max"], value))  # the spec clamps numbers to their range
    return value


def normalize_params(manifest, params):
    """Check params against the manifest; return them with defaults filled in.

    null means unset (a blank number box arrives as null): the default is used, or the
    param is left out when it has none. Numbers are clamped, everything else must be exact.
    """
    declared = {p["name"]: p for p in manifest.get("params", [])}
    unknown = sorted(set(params) - set(declared))
    if unknown:
        raise RequestError(400, f"unknown param: {unknown[0]!r}")
    out = {}
    for name, p in declared.items():
        value = params.get(name)
        if value is None:
            if "default" in p:
                out[name] = p["default"]
        else:
            out[name] = _param_value(p, value)
    return out


def check_input_counts(manifest, counts):
    """Check how many files per slot the client says it uploaded; return {slot: count} without zeros."""
    known = {s["name"]: s for s in manifest["inputs"]}
    for name in counts:
        if name not in known:
            raise RequestError(400, f"unknown input slot: {name!r}")
    out = {}
    for name, slot in known.items():
        count = counts.get(name, 0)
        if count < 0 or count > slot.get("max_files", 1):
            raise RequestError(400, f"slot {name!r} takes at most {slot.get('max_files', 1)} file(s)")
        if slot["required"] and count < 1:
            raise RequestError(400, f"input slot {name!r} is required")
        if count:
            out[name] = count
    return out
