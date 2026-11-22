"""The memory report (plan 06, open item G17): what the container's RAM is made of, read from /proc
and the cgroup, since the instance has no SSH and `ru_maxrss` cannot tell reclaimable file pages
(memory-mapped weights) from memory that can really get the container killed."""
import pathlib

from worker import memory

GIB = 2**30


def _proc(tmp_path, **kb):
    """A /proc/<pid>/status file; values in kB, the way Linux writes them."""
    lines = [f"{key}:\t{value} kB" for key, value in kb.items()]
    path = tmp_path / "status"
    path.write_text("Name:\tpython\n" + "\n".join(lines) + "\n")
    return path


def _cgroup(tmp_path, current, peak, anon, file):
    d = tmp_path / "cgroup"
    d.mkdir()
    (d / "memory.current").write_text(f"{int(current * GIB)}\n")
    (d / "memory.peak").write_text(f"{int(peak * GIB)}\n")
    (d / "memory.stat").write_text(f"anon {int(anon * GIB)}\nfile {int(file * GIB)}\nkernel 1000\n")
    return d


def test_it_splits_the_process_into_anonymous_and_file_pages(tmp_path):
    status = _proc(tmp_path, VmRSS=20 * 2**20, RssAnon=11 * 2**20, RssFile=9 * 2**20, VmHWM=24 * 2**20)
    line = memory.report("after load", proc_status=status, cgroup_dir=tmp_path / "none")
    assert line.startswith("memory after load:")
    assert "process 20.0 GiB (anon 11.0, file 9.0, peak 24.0)" in line


def test_it_reads_the_containers_own_counters(tmp_path):
    status = _proc(tmp_path, VmRSS=2**20, RssAnon=2**20, RssFile=0, VmHWM=2**20)
    cgroup = _cgroup(tmp_path, current=18.5, peak=27.25, anon=11.0, file=7.5)
    line = memory.report("after job", proc_status=status, cgroup_dir=cgroup)
    assert "container 18.5 GiB (anon 11.0, file 7.5, peak 27.2)" in line


def test_a_missing_cgroup_is_left_out_not_an_error(tmp_path):
    status = _proc(tmp_path, VmRSS=2**20, RssAnon=2**20, RssFile=0, VmHWM=2**20)
    line = memory.report("x", proc_status=status, cgroup_dir=tmp_path / "none")
    assert "container" not in line and "process" in line


def test_a_machine_without_proc_still_gives_a_line(tmp_path):
    line = memory.report("x", proc_status=tmp_path / "none", cgroup_dir=tmp_path / "none")
    assert line == "memory x: unavailable"


def test_a_cgroup_without_a_peak_file_still_reports_the_rest(tmp_path):
    cgroup = _cgroup(tmp_path, current=1.0, peak=1.0, anon=0.5, file=0.5)
    (cgroup / "memory.peak").unlink()
    line = memory.report("x", proc_status=tmp_path / "none", cgroup_dir=cgroup)
    assert "container 1.0 GiB (anon 0.5, file 0.5)" in line


def test_the_default_paths_read_this_machine_without_failing():
    line = memory.report("now")
    assert line.startswith("memory now:")
    assert isinstance(pathlib.Path(memory.PROC_STATUS), pathlib.Path)
