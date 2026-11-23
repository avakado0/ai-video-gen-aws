"""Capability manifest (spec 3B): validation and loading.

One deployment serves one model, and `model_manifest.json` describes it. Terraform passes
the file to the API Lambda as an environment variable. This module checks that the manifest
itself is well formed (the API checks requests against it elsewhere). Standard library only,
so it adds nothing to the Lambda zip.
"""
import json
import re

PROMPT_MODES = ("required", "optional", "none")
SLOT_KINDS = ("image", "audio", "video")
PARAM_TYPES = ("int", "float", "enum", "bool", "string")
SLOT_NAME = re.compile(r"^[a-z0-9_]+$")
RESERVED_PARAMS = ("prompt",)  # POST /jobs carries these at the top level


class ManifestError(ValueError):
    """The manifest is not valid; the message lists every problem."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _slot_errors(i, s, seen):
    where = f"inputs[{i}]"
    if not isinstance(s, dict):
        return [f"{where}: must be an object"]
    errors = []
    name = s.get("name")
    if not isinstance(name, str) or not SLOT_NAME.match(name):
        errors.append(f"{where}: name must be lowercase letters, digits or _ (got {name!r})")
    elif name in seen:
        errors.append(f"{where}: duplicate slot name {name!r}")
    else:
        seen.add(name)
    if not s.get("label"):
        errors.append(f"{where}: label is required")
    if s.get("kind") not in SLOT_KINDS:
        errors.append(f"{where}: kind must be one of {', '.join(SLOT_KINDS)}")
    if not isinstance(s.get("required"), bool):
        errors.append(f"{where}: required must be true or false")
    mime = s.get("mime")
    if not isinstance(mime, list) or not mime or not all(isinstance(m, str) for m in mime):
        errors.append(f"{where}: mime must be a non-empty list of strings")
    if not _is_int(s.get("max_bytes")) or s["max_bytes"] < 1:
        errors.append(f"{where}: max_bytes must be a positive integer")
    if "max_files" in s and (not _is_int(s["max_files"]) or s["max_files"] < 1):
        errors.append(f"{where}: max_files must be an integer of at least 1")
    if "max_seconds" in s:
        if s.get("kind") not in ("audio", "video"):
            errors.append(f"{where}: max_seconds applies only to audio and video slots")
        elif not _is_number(s["max_seconds"]) or s["max_seconds"] <= 0:
            errors.append(f"{where}: max_seconds must be a positive number")
    if "min_side" in s:
        if s.get("kind") != "image":
            errors.append(f"{where}: min_side applies only to image slots")
        elif not _is_int(s["min_side"]) or s["min_side"] < 1:
            errors.append(f"{where}: min_side must be a positive integer (pixels)")
    return errors


def _param_errors(i, p, seen):
    where = f"params[{i}]"
    if not isinstance(p, dict):
        return [f"{where}: must be an object"]
    errors = []
    name = p.get("name")
    if not isinstance(name, str) or not SLOT_NAME.match(name):
        errors.append(f"{where}: name must be lowercase letters, digits or _ (got {name!r})")
    elif name in RESERVED_PARAMS:
        errors.append(f"{where}: {name!r} is a reserved name")
    elif name in seen:
        errors.append(f"{where}: duplicate param name {name!r}")
    else:
        seen.add(name)
    ptype = p.get("type")
    default = p.get("default")
    has_default = "default" in p
    if ptype not in PARAM_TYPES:
        errors.append(f"{where}: type must be one of {', '.join(PARAM_TYPES)}")
    elif ptype == "bool":
        if has_default and not isinstance(default, bool):
            errors.append(f"{where}: default must be true or false")
    elif ptype == "enum":
        values = p.get("values")
        # The form submits the selected option as a string, so enum values are strings.
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            errors.append(f"{where}: values must be a non-empty list of strings")
        elif has_default and default not in values:
            errors.append(f"{where}: default must be one of values")
    elif ptype == "string":
        if not _is_int(p.get("max_chars")) or p["max_chars"] < 1:
            errors.append(f"{where}: max_chars must be a positive integer")
        elif has_default and (not isinstance(default, str) or len(default) > p["max_chars"]):
            errors.append(f"{where}: default must be a string of at most max_chars")
    else:  # int or float
        check = _is_int if ptype == "int" else _is_number
        lo, hi = p.get("min"), p.get("max")
        if not check(lo) or not check(hi):
            errors.append(f"{where}: min and max must be {'integers' if ptype == 'int' else 'numbers'}")
        elif lo > hi:
            errors.append(f"{where}: min must not exceed max")
        elif has_default and (not check(default) or not lo <= default <= hi):
            errors.append(f"{where}: default must be a {ptype} between min and max")
    return errors


PROMPT_HINT_MAX_CHARS = 600  # the manifest rides in a Lambda env var (4 KB for all of them together)


def _hint_errors(data):
    hint = data.get("prompt_hint")
    if hint is None:
        return []
    if not isinstance(hint, list) or not hint or not all(isinstance(t, str) and t.strip() for t in hint):
        return ["prompt_hint must be a non-empty list of non-empty strings"]
    if data.get("prompt", "required") == "none":
        return ["prompt_hint needs a prompt field (prompt is none)"]
    if sum(len(t) for t in hint) > PROMPT_HINT_MAX_CHARS:
        return [f"prompt_hint must be at most {PROMPT_HINT_MAX_CHARS} characters in total"]
    return []


def validate(data):
    """Return a list of problems with a manifest dict; empty means valid."""
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    errors = []
    if data.get("prompt", "required") not in PROMPT_MODES:
        errors.append(f"prompt must be one of {', '.join(PROMPT_MODES)}")
    limits = data.get("limits")
    limit = limits.get("prompt_max_chars") if isinstance(limits, dict) else None
    if not _is_int(limit) or limit < 1:
        errors.append("limits.prompt_max_chars must be a positive integer")
    errors += _hint_errors(data)
    inputs = data.get("inputs")
    if not isinstance(inputs, list):
        errors.append("inputs must be a list (empty for text-only models)")
    else:
        seen = set()
        for i, s in enumerate(inputs):
            errors += _slot_errors(i, s, seen)
    params = data.get("params", [])
    if not isinstance(params, list):
        errors.append("params must be a list")
    else:
        seen = set()
        for i, p in enumerate(params):
            errors += _param_errors(i, p, seen)
    return errors


def load(text):
    """Parse manifest JSON and validate it; raise ManifestError listing every problem."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}") from e
    errors = validate(data)
    if errors:
        raise ManifestError("invalid manifest:\n- " + "\n- ".join(errors))
    return data
