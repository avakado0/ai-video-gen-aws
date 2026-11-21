"""Wan 2.1 T2V 1.3B through diffusers' WanPipeline (spec section 4, "Model").

Run on a Kaggle T4 on 2026-11-20 (docs/wan_spike_findings.md): loads, clean frames in fp16 with an
fp32 VAE, abort frees the GPU. Not yet run on a g4dn instance. Guidance and flow_shift are the
documented defaults, not tuned.

torch and diffusers are imported inside methods, so the rest of the worker (and its tests)
load without them.
"""
import gc
import logging
import pathlib
import random

from .errors import Aborted

log = logging.getLogger(__name__)

FPS = 16
# The manifest's `quality` choices: (width, height, steps). Kaggle T4 spike, 2026-11-20, 3 s clip:
# fast about 4.7 min, high about 15 min (docs/wan_spike_findings.md). Sizes must be multiples of 16.
QUALITY = {"fast": (640, 368, 20), "high": (832, 480, 30)}
GUIDANCE_SCALE = 5.0
FLOW_SHIFT = 3.0          # diffusers' Wan docs: 3.0 for 480p, 5.0 for 720p
FRAMES = {"3": 49, "5": 81}   # 16 fps, and the model needs 4n+1 frames
MAX_SEED = 2147483647
LOG_EVERY_STEPS = 5      # one progress line per this many denoising steps, plus the last


def num_frames(duration_seconds):
    return FRAMES[duration_seconds]


def preset(quality):
    return QUALITY[quality]


def pick_seed(params, rng=random):
    seed = params.get("seed")
    return seed if seed is not None else rng.randint(0, MAX_SEED)


class WanGenerator:
    def __init__(self, models_dir, model_name, steps=None):
        self.path = str(pathlib.Path(models_dir) / model_name)
        self.steps = steps        # None: the quality preset decides. The spike notebooks set it.
        self.pipe = None

    def load(self):
        """Load the weights once, at start. The T4 has no bf16: transformer and text encoder in
        fp16, VAE in fp32 (spec section 4). Components move to the GPU only while they run."""
        import torch
        from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline

        vae = AutoencoderKLWan.from_pretrained(self.path, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(self.path, vae=vae, torch_dtype=torch.float16)
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=FLOW_SHIFT)
        pipe.enable_model_cpu_offload()
        self.pipe = pipe

    def generate(self, job, inputs, should_abort, out_dir):
        error = None
        try:
            return self._generate(job, should_abort, out_dir)
        except Aborted as e:
            # The traceback holds the pipeline's tensors, so drop it before freeing memory.
            error = e.with_traceback(None)
        finally:
            if error is None:
                self._free()
        self._free()
        raise error

    def _free(self):
        """An abort skips diffusers' own cleanup: the transformer stayed on the GPU and the next
        job ran out of memory (Kaggle spike). Put the model back on the CPU and drop the cache."""
        import torch

        self.pipe.maybe_free_model_hooks()
        gc.collect()
        torch.cuda.empty_cache()

    def _generate(self, job, should_abort, out_dir):
        import torch
        from diffusers.utils import export_to_video

        if should_abort():
            raise Aborted()

        seed = pick_seed(job.params)
        width, height, preset_steps = preset(job.params["quality"])
        total_steps = self.steps or preset_steps
        frame_count = num_frames(job.params["duration_seconds"])
        # Settings only, never the prompt (it may be private).
        log.info("generating %dx%d, %d frames, %d steps", width, height, frame_count, total_steps)

        def on_step(pipe, step, timestep, callback_kwargs):
            if should_abort():
                raise Aborted()          # leaves the denoising loop between two steps
            done = step + 1
            if done % LOG_EVERY_STEPS == 0 or done == total_steps:
                log.info("step %d/%d", done, total_steps)
            return callback_kwargs

        frames = self.pipe(
            prompt=job.prompt,
            negative_prompt=job.params.get("negative_prompt") or None,
            width=width, height=height,
            num_frames=frame_count,
            num_inference_steps=total_steps,
            guidance_scale=GUIDANCE_SCALE,
            generator=torch.Generator("cpu").manual_seed(seed),
            callback_on_step_end=on_step,
        ).frames[0]
        out = pathlib.Path(out_dir) / "clip.mp4"
        export_to_video(frames, str(out), fps=FPS)
        return out
