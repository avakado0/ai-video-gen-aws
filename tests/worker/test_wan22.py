"""Wan22Generator (plan 06): Wan 2.2 TI2V 5B, text to video and image to video.

The model needs a GPU, so these tests use fake pipelines and a fake torch/diffusers. What they pin is
our own logic: presets, frame math, defaults, the start-image fit, abort cleanup, which pipeline runs
and when the CPU-offload hooks move. Whether the real pipelines accept the wiring is shown by trial 1
on AWS (plan 06, step B4), not here.
"""
import io
import logging
import pathlib
import subprocess
import sys
import types

import pytest

from worker import wan, wan22
from worker.errors import Aborted, PermanentJobError

ROOT = pathlib.Path(__file__).resolve().parents[2]


# --- presets and frame math ---------------------------------------------------------------

def test_both_presets_are_1280x704_and_differ_in_steps_fast_30_high_50():
    assert wan22.preset("fast") == (1280, 704, 30)
    assert wan22.preset("high") == (1280, 704, 50)


def test_every_size_is_a_multiple_of_16():
    for quality in wan22.QUALITY:
        width, height, _ = wan22.preset(quality)
        assert width % 16 == 0 and height % 16 == 0


def test_unknown_quality_is_not_guessed():
    with pytest.raises(KeyError):
        wan22.preset("ultra")


def test_three_seconds_is_73_frames_and_five_is_121_at_24_fps():
    assert wan22.FPS == 24
    assert wan22.num_frames("3") == 73
    assert wan22.num_frames("5") == 121


def test_frame_counts_are_the_4n_plus_1_the_model_needs():
    for seconds in ("3", "5"):
        assert (wan22.num_frames(seconds) - 1) % 4 == 0


def test_the_helpers_are_shared_with_wan_not_copied():
    assert wan22.pick_seed is wan.pick_seed
    assert wan22.DEFAULT_NEGATIVE_PROMPT is wan.DEFAULT_NEGATIVE_PROMPT


def test_every_manifest_quality_and_duration_has_a_preset():
    import json
    manifest = json.loads((ROOT / "model_manifest.json").read_text())
    params = {p["name"]: p for p in manifest["params"]}
    assert sorted(params["quality"]["values"]) == sorted(wan22.QUALITY)
    assert sorted(params["duration_seconds"]["values"]) == sorted(wan22.FRAMES)


def test_importing_the_module_needs_no_torch_and_no_pillow():
    code = "import sys; sys.modules['torch'] = None; sys.modules['PIL'] = None; import worker.wan22, worker.runner"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT, env={"PYTHONPATH": "lambdas:."})


# --- fakes ---------------------------------------------------------------------------------

def _install_fake_torch(monkeypatch, calls):
    torch = types.ModuleType("torch")
    torch.Generator = lambda device: types.SimpleNamespace(manual_seed=lambda s: None)
    torch.cuda = types.SimpleNamespace(
        empty_cache=lambda: calls.append("empty_cache"),
        reset_peak_memory_stats=lambda: calls.append("reset_peak"),
        max_memory_allocated=lambda: 5 * 2**30,      # a fake GPU that peaked at 5 GiB
    )
    torch.float32, torch.bfloat16 = "float32", "bfloat16"
    utils = types.ModuleType("diffusers.utils")
    utils.export_to_video = lambda *a, **k: None
    diffusers = types.ModuleType("diffusers")
    diffusers.utils = utils
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    monkeypatch.setitem(sys.modules, "diffusers.utils", utils)


class FakePipe:
    """Records its call and how often its offload hooks were set up or freed."""

    def __init__(self, name, log):
        self.name, self.log, self.kwargs, self.calls = name, log, None, 0

    def __call__(self, **kw):
        self.kwargs, self.calls = kw, self.calls + 1
        self.log.append(f"run_{self.name}")
        return types.SimpleNamespace(frames=[[]])

    def enable_model_cpu_offload(self):
        self.log.append(f"offload_{self.name}")

    def maybe_free_model_hooks(self):
        self.log.append(f"free_{self.name}")


def _gen(monkeypatch, tmp_path, **kw):
    log = []
    _install_fake_torch(monkeypatch, log)
    gen = wan22.Wan22Generator(tmp_path, "m", **kw)
    gen.pipe, gen.image_pipe = FakePipe("text", log), FakePipe("image", log)
    gen._offloaded = gen.pipe
    return gen, log


def _job(quality="high", duration="5", prompt="p", **params):
    return types.SimpleNamespace(prompt=prompt, params={"duration_seconds": duration, "quality": quality, **params})


def _png(path, size, color=(200, 80, 40)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)
    return path


# --- the text-to-video path ----------------------------------------------------------------

def test_the_text_pipeline_gets_the_size_steps_and_frames_of_the_chosen_quality(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job("fast", "3"), {}, lambda: False, tmp_path)
    kw = gen.pipe.kwargs
    assert (kw["width"], kw["height"], kw["num_inference_steps"], kw["num_frames"]) == (1280, 704, 30, 73)
    gen.generate(_job("high", "5"), {}, lambda: False, tmp_path)
    kw = gen.pipe.kwargs
    assert (kw["width"], kw["height"], kw["num_inference_steps"], kw["num_frames"]) == (1280, 704, 50, 121)
    assert "image" not in kw and gen.image_pipe.calls == 0


def test_a_steps_override_wins_over_the_preset(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path, steps=8)
    gen.generate(_job("high"), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["num_inference_steps"] == 8


def test_guidance_defaults_to_5_and_can_be_overridden(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job(), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["guidance_scale"] == 5.0
    gen, _ = _gen(monkeypatch, tmp_path, guidance=4.0)
    gen.generate(_job(), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["guidance_scale"] == 4.0


def test_guidance_of_zero_is_honoured_not_replaced_by_the_default(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path, guidance=0.0)
    gen.generate(_job(), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["guidance_scale"] == 0.0


def test_no_negative_prompt_from_the_job_means_wans_official_default(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job(), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["negative_prompt"] == wan.DEFAULT_NEGATIVE_PROMPT


def test_the_job_prompt_replaces_the_default_it_is_not_added_to_it(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job(negative_prompt="blurry"), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["negative_prompt"] == "blurry"


def test_the_default_negative_prompt_can_be_set_on_the_generator(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path, default_negative_prompt="oversaturated")
    gen.generate(_job(negative_prompt=""), {}, lambda: False, tmp_path)
    assert gen.pipe.kwargs["negative_prompt"] == "oversaturated"


# --- abort frees the GPU (same cleanup as wan.py, found by the Kaggle spike) ----------------

def test_abort_returns_the_model_to_cpu_and_frees_gpu_memory(monkeypatch, tmp_path):
    gen, log = _gen(monkeypatch, tmp_path)

    def aborting(**kw):
        kw["callback_on_step_end"](gen.pipe, 0, 0, {})
        raise AssertionError("the callback should have aborted")

    gen.pipe.__class__ = type("Aborting", (FakePipe,), {"__call__": lambda self, **kw: aborting(**kw)})
    calls = {"n": 0}

    def should_abort():
        calls["n"] += 1
        return calls["n"] > 1       # not before the first step, yes during it

    with pytest.raises(Aborted):
        gen.generate(_job(), {}, should_abort, tmp_path)
    assert log[-2:] == ["free_text", "empty_cache"]


def test_a_job_aborted_before_it_starts_runs_nothing(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    with pytest.raises(Aborted):
        gen.generate(_job(), {}, lambda: True, tmp_path)
    assert gen.pipe.calls == 0 and gen.image_pipe.calls == 0


# --- the start image -----------------------------------------------------------------------

@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1000, 1000), (1280, 704), (4000, 700)])
def test_every_start_image_comes_out_exactly_1280x704(tmp_path, size):
    out = wan22.fit_image(_png(tmp_path / "in.png", size))
    assert out.size == (1280, 704)
    assert out.mode == "RGB"


def test_the_crop_is_centred(tmp_path):
    # Left half red, right half blue, 2560x704: the centre crop keeps the middle of both.
    from PIL import Image
    img = Image.new("RGB", (2560, 704), (255, 0, 0))
    img.paste((0, 0, 255), (1280, 0, 2560, 704))
    path = tmp_path / "split.png"
    img.save(path)
    out = wan22.fit_image(path)
    assert out.getpixel((10, 350))[0] > 200 and out.getpixel((1270, 350))[2] > 200


def test_a_transparent_png_is_flattened_not_rejected(tmp_path):
    from PIL import Image
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (800, 600), (10, 20, 30, 0)).save(path)
    assert wan22.fit_image(path).mode == "RGB"


def test_an_image_with_a_short_side_under_512_is_rejected(tmp_path):
    with pytest.raises(PermanentJobError) as e:
        wan22.fit_image(_png(tmp_path / "small.png", (2000, 511)))
    assert e.value.code == "invalid_input" and e.value.field == "image"
    assert "512" in e.value.message


def test_an_image_with_a_short_side_of_exactly_512_is_accepted(tmp_path):
    assert wan22.fit_image(_png(tmp_path / "ok.png", (900, 512))).size == (1280, 704)


def test_a_file_that_is_not_an_image_is_rejected_without_leaking_its_path(tmp_path):
    path = tmp_path / "secret-name.png"
    path.write_bytes(b"not an image at all")
    with pytest.raises(PermanentJobError) as e:
        wan22.fit_image(path)
    assert e.value.code == "invalid_input" and e.value.field == "image"
    assert "secret-name" not in e.value.message and str(tmp_path) not in e.value.message


def test_a_truncated_image_is_rejected(tmp_path):
    good = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (900, 900), (1, 2, 3)).save(good, format="PNG")
    path = tmp_path / "cut.png"
    path.write_bytes(good.getvalue()[:100])
    with pytest.raises(PermanentJobError):
        wan22.fit_image(path)


# --- the image-to-video path ---------------------------------------------------------------

def test_a_job_with_an_image_runs_the_image_pipeline_with_the_fitted_image(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    start = _png(tmp_path / "start.png", (1600, 900))
    gen.generate(_job("fast", "3"), {"image": [start]}, lambda: False, tmp_path)
    assert gen.pipe.calls == 0 and gen.image_pipe.calls == 1
    kw = gen.image_pipe.kwargs
    assert kw["image"].size == (1280, 704)
    assert (kw["width"], kw["height"], kw["num_inference_steps"], kw["num_frames"]) == (1280, 704, 30, 73)
    assert kw["guidance_scale"] == 5.0 and kw["negative_prompt"] == wan.DEFAULT_NEGATIVE_PROMPT


def test_an_empty_image_slot_means_text_to_video(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job(), {"image": []}, lambda: False, tmp_path)
    assert gen.pipe.calls == 1 and gen.image_pipe.calls == 0


def test_a_bad_image_fails_the_job_before_any_gpu_work(monkeypatch, tmp_path):
    gen, _ = _gen(monkeypatch, tmp_path)
    tiny = _png(tmp_path / "tiny.png", (100, 100))
    with pytest.raises(PermanentJobError):
        gen.generate(_job(), {"image": [tiny]}, lambda: False, tmp_path)
    assert gen.pipe.calls == 0 and gen.image_pipe.calls == 0


def test_the_offload_hooks_move_only_when_the_kind_of_job_changes(monkeypatch, tmp_path):
    # Both pipelines share one set of weights (no second copy); the second `enable_model_cpu_offload`
    # replaces the first one's hooks, so it is called on a switch and not on every job.
    gen, log = _gen(monkeypatch, tmp_path)
    start = _png(tmp_path / "s.png", (1280, 704))
    gen.generate(_job(), {}, lambda: False, tmp_path)                       # text, hooks already on it
    gen.generate(_job(), {}, lambda: False, tmp_path)                       # text again
    gen.generate(_job(), {"image": [start]}, lambda: False, tmp_path)       # switch to image
    gen.generate(_job(), {"image": [start]}, lambda: False, tmp_path)       # image again
    gen.generate(_job(), {}, lambda: False, tmp_path)                       # back to text
    assert [x for x in log if x.startswith("offload_")] == ["offload_image", "offload_text"]


def test_after_an_image_job_the_image_pipelines_hooks_are_freed(monkeypatch, tmp_path):
    gen, log = _gen(monkeypatch, tmp_path)
    start = _png(tmp_path / "s.png", (1280, 704))
    gen.generate(_job(), {"image": [start]}, lambda: False, tmp_path)
    assert "free_image" in log and "free_text" not in log


# --- logging -------------------------------------------------------------------------------

def test_generation_logs_its_settings_and_never_the_prompt(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="worker.wan22")
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job("fast", "3", prompt="a very private cat"), {}, lambda: False, tmp_path)
    lines = [r.getMessage() for r in caplog.records]
    assert any("1280x704" in l and "73 frames" in l and "30 steps" in l for l in lines)
    assert not any("private" in l for l in lines)


def test_the_log_says_which_kind_of_job_it_is(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="worker.wan22")
    gen, _ = _gen(monkeypatch, tmp_path)
    start = _png(tmp_path / "s.png", (1280, 704))
    gen.generate(_job(), {"image": [start]}, lambda: False, tmp_path)
    assert any("image to video" in r.getMessage() for r in caplog.records)


def test_progress_is_logged_every_few_steps_and_the_last_one(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="worker.wan22")
    gen, _ = _gen(monkeypatch, tmp_path, steps=12)

    def stepping(**kw):
        for i in range(12):
            kw["callback_on_step_end"](gen.pipe, i, 0, {})
        return types.SimpleNamespace(frames=[[]])

    gen.pipe.__class__ = type("Stepping", (FakePipe,), {"__call__": lambda self, **kw: stepping(**kw)})
    gen.generate(_job(), {}, lambda: False, tmp_path)
    steps = [r.getMessage() for r in caplog.records if "step " in r.getMessage() and "/" in r.getMessage()]
    assert any("step 5/12" in l for l in steps) and any("step 10/12" in l for l in steps)
    assert any("step 12/12" in l for l in steps) and not any("step 4/12" in l for l in steps)


# --- load ---------------------------------------------------------------------------------

def _load(monkeypatch, tmp_path, **generator_kwargs):
    """Runs load() with fake torch/diffusers and returns what it built."""
    seen = {"offload": [], "tiling": 0, "dtypes": {}}

    class Scheduler:
        @staticmethod
        def from_config(config, flow_shift):
            seen["flow_shift"] = flow_shift
            return "scheduler"

    class TextPipe:
        scheduler = types.SimpleNamespace(config={})
        tokenizer, text_encoder, vae, transformer, transformer_2 = "tok", "te", None, "tr", None
        config = types.SimpleNamespace(boundary_ratio=None, expand_timesteps=True)

        @classmethod
        def from_pretrained(cls, path, vae=None, torch_dtype=None):
            seen["dtypes"]["pipeline"] = torch_dtype
            pipe = cls()
            pipe.vae = vae
            seen["text"] = pipe
            return pipe

        def enable_model_cpu_offload(self):
            seen["offload"].append("text")

    class ImagePipe:
        def __init__(self, **kw):
            seen["image_kwargs"] = kw

        def enable_model_cpu_offload(self):
            seen["offload"].append("image")

    class Vae:
        def enable_tiling(self):
            seen["tiling"] += 1

        @classmethod
        def from_pretrained(cls, path, subfolder=None, torch_dtype=None):
            seen["dtypes"]["vae"] = torch_dtype
            seen["vae"] = cls()
            return seen["vae"]

    torch = types.ModuleType("torch")
    torch.float32, torch.bfloat16 = "float32", "bfloat16"
    diffusers = types.ModuleType("diffusers")
    diffusers.AutoencoderKLWan, diffusers.WanPipeline = Vae, TextPipe
    diffusers.WanImageToVideoPipeline, diffusers.UniPCMultistepScheduler = ImagePipe, Scheduler
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    gen = wan22.Wan22Generator(tmp_path, "m", **generator_kwargs)
    gen.load()
    return gen, seen


def test_load_uses_bf16_for_the_model_and_fp32_for_the_vae(monkeypatch, tmp_path):
    _, seen = _load(monkeypatch, tmp_path)
    assert seen["dtypes"] == {"vae": "float32", "pipeline": "bfloat16"}


def test_load_builds_the_image_pipeline_from_the_same_loaded_parts(monkeypatch, tmp_path):
    gen, seen = _load(monkeypatch, tmp_path)
    kw = seen["image_kwargs"]
    assert kw["vae"] is seen["vae"] and kw["transformer"] == "tr" and kw["text_encoder"] == "te"
    assert kw["tokenizer"] == "tok" and kw["scheduler"] == "scheduler"
    assert kw["expand_timesteps"] is True
    assert gen.pipe is seen["text"] and gen.image_pipe is not gen.pipe


def test_load_sets_up_offload_once_on_the_text_pipeline_only(monkeypatch, tmp_path):
    gen, seen = _load(monkeypatch, tmp_path)
    assert seen["offload"] == ["text"]
    assert gen._offloaded is gen.pipe


def test_load_turns_on_vae_tiling_as_cover_against_running_out_of_memory(monkeypatch, tmp_path):
    # The VAE decode at 1280x704x121 is the unmeasured out-of-memory risk (plan 06); offload does not cover it.
    _, seen = _load(monkeypatch, tmp_path)
    assert seen["tiling"] == 1


def test_flow_shift_defaults_to_5_and_can_be_overridden(monkeypatch, tmp_path):
    assert _load(monkeypatch, tmp_path)[1]["flow_shift"] == 5.0
    assert _load(monkeypatch, tmp_path, flow_shift=3.0)[1]["flow_shift"] == 3.0


# --- memory lines for the trial (plan 06): peak GPU and RAM, since the instance has no SSH -----------

def test_it_logs_peak_gpu_and_ram_memory_after_generating(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="worker.wan22")
    gen, _ = _gen(monkeypatch, tmp_path)
    gen.generate(_job(prompt="a very private cat"), {}, lambda: False, tmp_path)
    lines = [r.getMessage() for r in caplog.records if "peak memory" in r.getMessage()]
    assert len(lines) == 1
    assert "GPU 5.0 GiB" in lines[0] and "RAM" in lines[0] and "GiB" in lines[0].split("RAM")[1]
    assert "private" not in lines[0]


def test_the_gpu_peak_is_reset_before_each_job_so_it_belongs_to_that_job(monkeypatch, tmp_path):
    gen, log = _gen(monkeypatch, tmp_path)
    gen.generate(_job(), {}, lambda: False, tmp_path)
    assert log.index("reset_peak") < log.index("run_text")


def test_load_logs_how_much_ram_the_loaded_model_takes(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="worker.wan22")
    _load(monkeypatch, tmp_path)
    assert any("model loaded" in r.getMessage() and "RAM" in r.getMessage() and "GiB" in r.getMessage()
               for r in caplog.records)
