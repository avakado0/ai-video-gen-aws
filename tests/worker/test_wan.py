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


def test_both_presets_are_480p_and_differ_in_steps_fast_30_high_50():
    # Plan 05: 480p is the size Wan 1.3B was trained for; 50 steps is Wan's own default.
    assert wan.preset("fast") == (832, 480, 30)
    assert wan.preset("high") == (832, 480, 50)


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
    assert (kw["width"], kw["height"], kw["num_inference_steps"], kw["num_frames"]) == (832, 480, 30, 81)
    kw = _generate(monkeypatch, tmp_path, "high")
    assert (kw["width"], kw["height"], kw["num_inference_steps"]) == (832, 480, 50)


def test_a_steps_override_wins_over_the_preset(monkeypatch, tmp_path):
    assert _generate(monkeypatch, tmp_path, "high", steps=8)["num_inference_steps"] == 8


# --- what the log says during generation -----------------------------------------------

class _SteppingPipe:
    """Calls the step callback `steps` times, as diffusers does, then returns."""

    def __init__(self, steps):
        self.steps = steps

    def __call__(self, callback_on_step_end=None, **kw):
        import types
        for i in range(self.steps):
            callback_on_step_end(self, i, 0, {})
        return types.SimpleNamespace(frames=[[]])

    def maybe_free_model_hooks(self):
        pass


def _run_logged(monkeypatch, tmp_path, steps, caplog):
    import logging
    import types
    caplog.set_level(logging.INFO, logger="worker.wan")
    _install_fake_torch(monkeypatch, [])
    gen = wan.WanGenerator(tmp_path, "m", steps=steps)
    gen.pipe = _SteppingPipe(steps)
    job = types.SimpleNamespace(prompt="a very private cat", params={"duration_seconds": "3", "quality": "fast"})
    gen.generate(job, {}, lambda: False, tmp_path)
    return [r.getMessage() for r in caplog.records]


def test_generation_logs_its_settings_and_never_the_prompt(monkeypatch, tmp_path, caplog):
    lines = _run_logged(monkeypatch, tmp_path, 10, caplog)
    assert any("832x480" in l and "49 frames" in l and "10 steps" in l for l in lines)
    assert not any("private" in l for l in lines)


def test_generation_logs_progress_every_few_steps_and_the_last_one(monkeypatch, tmp_path, caplog):
    lines = _run_logged(monkeypatch, tmp_path, 12, caplog)
    steps_logged = [l for l in lines if "step " in l]
    assert any("step 5/12" in l for l in steps_logged)
    assert any("step 10/12" in l for l in steps_logged)
    assert any("step 12/12" in l for l in steps_logged)
    assert not any("step 4/12" in l for l in steps_logged)


# --- tunable guidance, flow_shift and default negative prompt (plan 05, Kaggle quality grid) ------

def _generate_with(monkeypatch, tmp_path, negative_prompt="", **generator_kwargs):
    import types
    _install_fake_torch(monkeypatch, [])
    gen = wan.WanGenerator(tmp_path, "m", **generator_kwargs)
    gen.pipe = _RecordingPipe()
    job = types.SimpleNamespace(prompt="p", params={"duration_seconds": "3", "quality": "high",
                                                    "negative_prompt": negative_prompt})
    gen.generate(job, {}, lambda: False, tmp_path)
    return gen.pipe.kwargs


def test_guidance_defaults_to_6_and_can_be_overridden(monkeypatch, tmp_path):
    assert _generate_with(monkeypatch, tmp_path)["guidance_scale"] == 6.0
    assert _generate_with(monkeypatch, tmp_path, guidance=5.0)["guidance_scale"] == 5.0


def test_guidance_of_zero_is_honoured_not_replaced_by_the_default(monkeypatch, tmp_path):
    assert _generate_with(monkeypatch, tmp_path, guidance=0.0)["guidance_scale"] == 0.0


def test_no_negative_prompt_from_the_job_means_wans_official_default(monkeypatch, tmp_path):
    kw = _generate_with(monkeypatch, tmp_path)
    assert kw["negative_prompt"] == wan.DEFAULT_NEGATIVE_PROMPT
    assert kw["negative_prompt"].startswith("Bright tones, overexposed")


def test_the_job_prompt_replaces_the_default_it_is_not_added_to_it(monkeypatch, tmp_path):
    kw = _generate_with(monkeypatch, tmp_path, negative_prompt="blurry")
    assert kw["negative_prompt"] == "blurry"


def test_the_jobs_own_negative_prompt_is_used_when_it_has_one(monkeypatch, tmp_path):
    kw = _generate_with(monkeypatch, tmp_path, negative_prompt="blurry", default_negative_prompt="oversaturated")
    assert kw["negative_prompt"] == "blurry"


def test_the_default_negative_prompt_fills_in_when_the_job_has_none(monkeypatch, tmp_path):
    kw = _generate_with(monkeypatch, tmp_path, negative_prompt="", default_negative_prompt="oversaturated")
    assert kw["negative_prompt"] == "oversaturated"


def _load_recording_flow_shift(monkeypatch, tmp_path, **generator_kwargs):
    import types
    seen = {}

    class Scheduler:
        @staticmethod
        def from_config(config, flow_shift):
            seen["flow_shift"] = flow_shift
            return "scheduler"

    class Pipe:
        scheduler = types.SimpleNamespace(config={})

        @classmethod
        def from_pretrained(cls, *a, **k):
            return cls()

        def enable_model_cpu_offload(self):
            pass

    class Vae:
        @staticmethod
        def from_pretrained(*a, **k):
            return "vae"

    torch = types.ModuleType("torch")
    torch.float32 = torch.float16 = "dtype"
    diffusers = types.ModuleType("diffusers")
    diffusers.AutoencoderKLWan, diffusers.WanPipeline = Vae, Pipe
    diffusers.UniPCMultistepScheduler = Scheduler
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    wan.WanGenerator(tmp_path, "m", **generator_kwargs).load()
    return seen["flow_shift"]


def test_flow_shift_defaults_to_8_and_can_be_overridden(monkeypatch, tmp_path):
    assert _load_recording_flow_shift(monkeypatch, tmp_path) == 8.0
    assert _load_recording_flow_shift(monkeypatch, tmp_path, flow_shift=3.0) == 3.0
