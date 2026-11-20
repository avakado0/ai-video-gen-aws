"""WanGenerator's pure parts. The model itself needs a GPU and is checked by the Kaggle spike."""
import random
import subprocess
import sys

from worker import wan


def test_three_seconds_is_49_frames_and_five_is_81():
    assert wan.num_frames("3") == 49
    assert wan.num_frames("5") == 81


def test_frame_counts_are_the_4n_plus_1_the_model_needs():
    for seconds in ("3", "5"):
        assert (wan.num_frames(seconds) - 1) % 4 == 0


def test_given_seed_is_used_as_is():
    assert wan.pick_seed({"seed": 42}, random.Random(0)) == 42
    assert wan.pick_seed({"seed": 0}, random.Random(0)) == 0


def test_unset_seed_is_random_but_in_the_manifest_range():
    seed = wan.pick_seed({}, random.Random(1))
    assert 0 <= seed <= 2147483647


def test_fast_is_640x368_at_20_steps_and_high_is_832x480_at_30():
    assert wan.preset("fast") == (640, 368, 20)
    assert wan.preset("high") == (832, 480, 30)


def test_every_size_is_a_multiple_of_16():
    for quality in wan.QUALITY:
        width, height, _ = wan.preset(quality)
        assert width % 16 == 0 and height % 16 == 0


def test_every_manifest_quality_has_a_preset():
    import json
    import pathlib
    manifest = json.loads((pathlib.Path(wan.__file__).parents[1] / "model_manifest.json").read_text())
    values = next(p["values"] for p in manifest["params"] if p["name"] == "quality")
    assert sorted(values) == sorted(wan.QUALITY)


def test_unknown_quality_is_not_guessed():
    import pytest
    with pytest.raises(KeyError):
        wan.preset("ultra")


def test_frame_rate_is_16():
    assert wan.FPS == 16


def test_importing_the_module_needs_no_torch():
    code = "import sys; sys.modules['torch'] = None; import worker.wan, worker.runner"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=".", env={"PYTHONPATH": "lambdas:."})


# --- cleanup after an abort (found by the Kaggle spike: 2.7 GiB stayed on the GPU, next run OOM) ---

class _FakePipe:
    def __init__(self, calls):
        self.calls = calls

    def __call__(self, callback_on_step_end=None, **kw):
        callback_on_step_end(self, 0, 0, {})
        raise AssertionError("the callback should have aborted")

    def maybe_free_model_hooks(self):
        self.calls.append("free_hooks")


def _install_fake_torch(monkeypatch, calls):
    import types
    torch = types.ModuleType("torch")
    torch.Generator = lambda device: types.SimpleNamespace(manual_seed=lambda s: None)
    torch.cuda = types.SimpleNamespace(empty_cache=lambda: calls.append("empty_cache"))
    utils = types.ModuleType("diffusers.utils")
    utils.export_to_video = lambda *a, **k: None
    diffusers = types.ModuleType("diffusers")
    diffusers.utils = utils
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    monkeypatch.setitem(sys.modules, "diffusers.utils", utils)


def test_abort_returns_the_model_to_cpu_and_frees_gpu_memory(monkeypatch, tmp_path):
    import types
    import pytest
    from worker.errors import Aborted

    calls = []
    _install_fake_torch(monkeypatch, calls)
    gen = wan.WanGenerator(tmp_path, "m")
    gen.pipe = _FakePipe(calls)
    job = types.SimpleNamespace(prompt="p", params={"duration_seconds": "3", "quality": "fast"})

    with pytest.raises(Aborted):
        gen.generate(job, {}, lambda: calls.append("check") or len(calls) > 1, tmp_path)

    assert calls[-2:] == ["free_hooks", "empty_cache"]


class _RecordingPipe:
    def __init__(self):
        self.kwargs = None

    def __call__(self, **kw):
        import types
        self.kwargs = kw
        return types.SimpleNamespace(frames=[[]])

    def maybe_free_model_hooks(self):
        pass


def _generate(monkeypatch, tmp_path, quality, steps=None):
    import types
    _install_fake_torch(monkeypatch, [])
    gen = wan.WanGenerator(tmp_path, "m", steps=steps)
    gen.pipe = _RecordingPipe()
    job = types.SimpleNamespace(prompt="p", params={"duration_seconds": "5", "quality": quality})
    gen.generate(job, {}, lambda: False, tmp_path)
    return gen.pipe.kwargs


def test_the_pipeline_gets_the_size_and_steps_of_the_chosen_quality(monkeypatch, tmp_path):
    kw = _generate(monkeypatch, tmp_path, "fast")
    assert (kw["width"], kw["height"], kw["num_inference_steps"], kw["num_frames"]) == (640, 368, 20, 81)
    kw = _generate(monkeypatch, tmp_path, "high")
    assert (kw["width"], kw["height"], kw["num_inference_steps"]) == (832, 480, 30)


def test_a_steps_override_wins_over_the_preset(monkeypatch, tmp_path):
    assert _generate(monkeypatch, tmp_path, "high", steps=8)["num_inference_steps"] == 8
