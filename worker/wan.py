"""Wan 2.1 T2V 1.3B through diffusers' WanPipeline (spec section 4, "Model").

UNVERIFIED: nothing here has run on a GPU yet. The Kaggle spike runs this same class and checks
speed, memory, the step count and whether fp16 gives clean frames. Until then treat the numbers
below (steps, guidance, flow_shift) as the documented defaults, not measured ones.

torch and diffusers are imported inside methods, so the rest of the worker (and its tests)
load without them.
"""
import pathlib
import random

from .errors import Aborted

WIDTH, HEIGHT, FPS = 832, 480, 16
GUIDANCE_SCALE = 5.0
FLOW_SHIFT = 3.0          # diffusers' Wan docs: 3.0 for 480p, 5.0 for 720p
FRAMES = {"3": 49, "5": 81}   # 16 fps, and the model needs 4n+1 frames
MAX_SEED = 2147483647


def num_frames(duration_seconds):
    return FRAMES[duration_seconds]


def pick_seed(params, rng=random):
    seed = params.get("seed")
    return seed if seed is not None else rng.randint(0, MAX_SEED)


class WanGenerator:
    def __init__(self, models_dir, model_name, steps):
        self.path = str(pathlib.Path(models_dir) / model_name)
        self.steps = steps
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
        import torch
        from diffusers.utils import export_to_video

        if should_abort():
            raise Aborted()

        def on_step(pipe, step, timestep, callback_kwargs):
            if should_abort():
                raise Aborted()          # leaves the denoising loop between two steps
            return callback_kwargs

        seed = pick_seed(job.params)
        try:
            frames = self.pipe(
                prompt=job.prompt,
                negative_prompt=job.params.get("negative_prompt") or None,
                width=WIDTH, height=HEIGHT,
                num_frames=num_frames(job.params["duration_seconds"]),
                num_inference_steps=self.steps,
                guidance_scale=GUIDANCE_SCALE,
                generator=torch.Generator("cpu").manual_seed(seed),
                callback_on_step_end=on_step,
            ).frames[0]
            out = pathlib.Path(out_dir) / "clip.mp4"
            export_to_video(frames, str(out), fps=FPS)
            return out
        finally:
            torch.cuda.empty_cache()
