"""Wan 2.1 T2V 1.3B through diffusers' WanPipeline (spec section 4, "Model").

Run on a Kaggle T4 on 2026-11-20 (docs/wan_spike_findings.md): loads, clean frames in fp16 with an
fp32 VAE, abort frees the GPU. Ran on a g5.2xlarge on 2026-11-21. Settings tuned on Kaggle in plan 05
(_plans/05_quality_variants.md): flow_shift 3.0 (from a 14B docstring) blew the colours out; Wan's
README recipe (flow_shift 8, guidance 6, 50 steps, official negative prompt) was judged usable.

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
# The manifest's `quality` choices: (width, height, steps). Both are 480p, the size the 1.3B model was
# trained for (640x368 was soft). Sizes must be multiples of 16. Kaggle T4, 3 s clip, plan 05: 30 steps
# about 13.5 min, 50 steps about 21 min.
QUALITY = {"fast": (832, 480, 30), "high": (832, 480, 50)}
GUIDANCE_SCALE = 6.0      # Wan README recommendation for the 1.3B model (code default 5.0)
FLOW_SHIFT = 8.0          # Wan README: adjust within 8 to 12 for the 1.3B model (code default 5.0)
# Wan's own default negative prompt (English version, diffusers WanPipeline docstring). Used when the
# job gives none; a negative prompt typed by the user replaces it. Run 9 (plan 05) used it.
DEFAULT_NEGATIVE_PROMPT = (
    "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, "
    "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
    "poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, "
    "still picture, messy background, three legs, many people in the background, walking backwards"
)
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
    def __init__(self, models_dir, model_name, steps=None, guidance=None, flow_shift=None,
                 default_negative_prompt=None):
        self.path = str(pathlib.Path(models_dir) / model_name)
        # None for each: the module default (or the quality preset) decides. Only the spike
        # notebooks set these, to compare settings (plan 05); the worker uses the defaults.
        self.steps = steps
        self.guidance = guidance
        self.flow_shift = flow_shift
        self.default_negative_prompt = default_negative_prompt   # None: DEFAULT_NEGATIVE_PROMPT
        self.pipe = None

    def load(self):
        """Load the weights once, at start. The T4 has no bf16: transformer and text encoder in
        fp16, VAE in fp32 (spec section 4). Components move to the GPU only while they run."""
        import torch
        from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline

        vae = AutoencoderKLWan.from_pretrained(self.path, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(self.path, vae=vae, torch_dtype=torch.float16)
        flow_shift = FLOW_SHIFT if self.flow_shift is None else self.flow_shift
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)
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
            negative_prompt=job.params.get("negative_prompt") or self.default_negative_prompt or DEFAULT_NEGATIVE_PROMPT,
            width=width, height=height,
            num_frames=frame_count,
            num_inference_steps=total_steps,
            guidance_scale=GUIDANCE_SCALE if self.guidance is None else self.guidance,
            generator=torch.Generator("cpu").manual_seed(seed),
            callback_on_step_end=on_step,
        ).frames[0]
        out = pathlib.Path(out_dir) / "clip.mp4"
        export_to_video(frames, str(out), fps=FPS)
        return out
