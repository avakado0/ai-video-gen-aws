"""A one-line memory report for the log (plan 06, open item G17).

`ru_maxrss` counts every page the process has mapped, including memory-mapped weight files. Those
pages are reclaimable: the kernel drops them under pressure, so they cannot get the container killed.
Only anonymous memory (and unreclaimable kernel memory) can. This report splits the two, for the
process (/proc) and for the container as a whole (its cgroup, which is what the task's memory limit
is enforced on), so a log line shows which one made a peak. The instance has no SSH, so the log is
the only way to see it. Anything missing on the machine is left out of the line, never an error.
"""
import pathlib

PROC_STATUS = "/proc/self/status"
CGROUP_DIR = "/sys/fs/cgroup"          # cgroup v2, which the ECS-optimized AL2023 AMI uses
GIB = 2**30


def _proc_kb(path):
    values = {}
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if len(parts) == 2 and parts[1] == "kB" and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
    except OSError:
        pass
    return values


def _int_file(path):
    try:
        return int(pathlib.Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _stat(path):
    values = {}
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            key, _, value = line.partition(" ")
            if value.strip().isdigit():
                values[key] = int(value)
    except OSError:
        pass
    return values


def _gib(n):
    return f"{n / GIB:.1f}"


def _part(name, total, anon, file, peak):
    inner = []
    if anon is not None:
        inner.append(f"anon {_gib(anon)}")
    if file is not None:
        inner.append(f"file {_gib(file)}")
    if peak is not None:
        inner.append(f"peak {_gib(peak)}")
    detail = f" ({', '.join(inner)})" if inner else ""
    return f"{name} {_gib(total)} GiB{detail}"


def report(label, proc_status=PROC_STATUS, cgroup_dir=CGROUP_DIR):
    """`memory <label>: process N GiB (anon, file, peak); container N GiB (anon, file, peak)`."""
    parts = []
    proc = _proc_kb(proc_status)
    if "VmRSS" in proc:
        parts.append(_part("process", proc["VmRSS"], proc.get("RssAnon"), proc.get("RssFile"), proc.get("VmHWM")))
    cgroup = pathlib.Path(cgroup_dir)
    current = _int_file(cgroup / "memory.current")
    if current is not None:
        stat = _stat(cgroup / "memory.stat")
        parts.append(_part("container", current, stat.get("anon"), stat.get("file"), _int_file(cgroup / "memory.peak")))
    return f"memory {label}: " + ("; ".join(parts) if parts else "unavailable")
