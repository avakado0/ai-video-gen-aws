"""Wan 2.2 TI2V 5B through diffusers (plan 06): text to video, and image to video when the job
carries a start image.

Wan's own recipe for this model (wan/configs/wan_ti2v_5B.py, checked 2026-11-22): 24 fps, flow_shift
5.0, guidance 5.0, 50 steps, bf16, 1280x704. Plan 05's lesson applies: these come from Wan's repo,
not from a docstring, and only the owner's eyes decide whether the clips are good enough.

Text to video uses `WanPipeline`. Image to video uses `WanImageToVideoPipeline` built from the very
same loaded parts, so the 34 GB of weights exist once. Both pipelines share modules, so only one of
them may hold the CPU-offload hooks at a time: `enable_model_cpu_offload` on the second replaces the
first one's hooks. The generator therefore moves the hooks only when the kind of job changes.

The start image is resized to cover 1280x704 and centre-cropped by us. The pipeline's own resize
would stretch it.

The prompt encoder (UMT5, 11.4 GB) is not kept in memory. Trial 1 on AWS (2026-11-22) had the
container killed at its 29 GiB RAM limit while denoising a second job: weights in RAM were the
transformer, the VAE and this encoder, all idle but the transformer. Each job now loads the encoder
straight to the GPU (it is free then: the transformer is still on the CPU), encodes the prompt and
the negative prompt, and drops the encoder before denoising starts. The pipelines take the
embeddings instead of the text.

Not run on a GPU yet. That the image pipeline accepts this wiring (read from diffusers 0.35.2's
source, not executed) is what trial 1(c) on AWS shows.

torch, diffusers and Pillow are imported inside methods, so the rest of the worker (and its tests)
load without them. The module shares helpers with wan.py, the parked 1.3B path, by importing them.
"""
import gc
import logging
import pathlib

from .errors import Aborted, PermanentJobError
from .generator import Generator
from .wan import DEFAULT_NEGATIVE_PROMPT, LOG_EVERY_STEPS, pick_seed

log = logging.getLogger(__name__)

FPS = 24
WIDTH, HEIGHT = 1280, 704                  # the size Wan 2.2 5B is meant for; both multiples of 16
QUALITY = {"fast": 30, "high": 50}         # the manifest's `quality` choices -> denoising steps
GUIDANCE_SCALE = 5.0                       # Wan's config for the 5B model
FLOW_SHIFT = 5.0                           # Wan's `sample_shift` for the 5B model
FRAMES = {"3": 73, "5": 121}               # 24 fps, and the model needs 4n+1 frames
IMAGE_SLOT = "image"                       # the manifest's optional start-image slot
MIN_IMAGE_SIDE = 512                       # the shorter side of the start image, in pixels
ENCODER_MAX_LENGTH = 512                   # tokens, what the pipelines' own call uses


def _peak_ram_gib():
    """Peak resident memory of this process so far, in GiB (Linux reports KiB)."""
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20


def num_frames(duration_seconds):
    return FRAMES[duration_seconds]


def preset(quality):
    return WIDTH, HEIGHT, QUALITY[quality]


def fit_image(path):
    """Open the start image, check it and return it as an RGB image of exactly 1280x704: scaled to
    cover that size, then centre-cropped. Anything unreadable or too small is a PermanentJobError
    (`invalid_input`, field `image`) with a message that is safe to show: no path, no library text."""
    from PIL import Image

    unreadable = PermanentJobError(
        "invalid_input", "The starting image could not be read. Use a PNG or JPEG file.", field=IMAGE_SLOT)
    try:
        with Image.open(path) as opened:
            width, height = opened.size
            if min(width, height) < MIN_IMAGE_SIDE:
                raise PermanentJobError(
                    "invalid_input",
                    f"The starting image is too small: its shorter side must be at least {MIN_IMAGE_SIDE} pixels.",
                    field=IMAGE_SLOT)
            opened.load()           # a truncated file fails here, not later
            if opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info:
                rgba = opened.convert("RGBA")
                image = Image.new("RGB", rgba.size, (255, 255, 255))     # flatten onto white
                image.paste(rgba, mask=rgba.split()[3])
            else:
                image = opened.convert("RGB")
    except PermanentJobError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise unreadable from None

    # Scale until both sides cover the target, with integer maths so the result is never a pixel short.
    if width * HEIGHT >= height * WIDTH:
        new_width, new_height = -(-width * HEIGHT // height), HEIGHT
    else:
        new_width, new_height = WIDTH, -(-height * WIDTH // width)
    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left, top = (new_width - WIDTH) // 2, (new_height - HEIGHT) // 2
    return image.crop((left, top, left + WIDTH, top + HEIGHT))


class Wan22Generator(Generator):
    def __init__(self, models_dir, model_name, steps=None, guidance=None, flow_shift=None,
                 default_negative_prompt=None, vae_dtype=None, device="cuda"):
        self.path = str(pathlib.Path(models_dir) / model_name)
        # None for each: the module default (or the quality preset) decides.
        self.steps = steps
        self.guidance = guidance
        self.flow_shift = flow_shift
        self.default_negative_prompt = default_negative_prompt   # None: DEFAULT_NEGATIVE_PROMPT
        self.vae_dtype = vae_dtype      # None: bf16 (see load)
        self.device = device            # where the prompt encoder is loaded for each job
        self.pipe = None            # text to video
        self.image_pipe = None      # image to video, same weights
        self._offloaded = None      # whichever pipeline holds the CPU-offload hooks right now

    def load(self):
        """Load the weights once, at start. Transformer, text encoder and VAE in bf16 (the A10G and the
        L4 have it). The VAE is bf16, not the fp32 diffusers' docs show, because the fp32 decode ran out
        of memory on the A10G (22.06 GiB usable) in trial 1 of plan 06; bf16 halves the decoder's
        activations and frame caches. Components move to the GPU only while they run. VAE tiling stays OFF:
        diffusers 0.35.2's `tiled_decode` never passes `first_chunk=True` to the decoder, which the
        Wan 2.2 VAE needs for its first frame, so tiled decoding fails with a tensor size mismatch
        (found on AWS 2026-11-22, trial 1). The plain decode runs frame by frame."""
        import torch
        from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanImageToVideoPipeline, WanPipeline

        vae_dtype = torch.bfloat16 if self.vae_dtype is None else self.vae_dtype
        vae = AutoencoderKLWan.from_pretrained(self.path, subfolder="vae", torch_dtype=vae_dtype)
        # text_encoder=None: diffusers skips it. It is loaded for each job instead (see _encode_prompt).
        pipe = WanPipeline.from_pretrained(self.path, vae=vae, text_encoder=None, torch_dtype=torch.bfloat16)
        flow_shift = FLOW_SHIFT if self.flow_shift is None else self.flow_shift
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)
        # Built from named parts: `pipe.components` leaves the transformer out (diffusers 0.35.2).
        image_pipe = WanImageToVideoPipeline(
            tokenizer=pipe.tokenizer, text_encoder=None, vae=pipe.vae,
            scheduler=pipe.scheduler, transformer=pipe.transformer, transformer_2=pipe.transformer_2,
            boundary_ratio=pipe.config.boundary_ratio, expand_timesteps=True)
        pipe.enable_model_cpu_offload()
        self.pipe, self.image_pipe, self._offloaded = pipe, image_pipe, pipe
        # The instance has no SSH, so the log is the only way to see whether the weights fit in RAM.
        log.info("model loaded, peak RAM so far %.1f GiB", _peak_ram_gib())

    def generate(self, job, inputs, should_abort, out_dir):
        error = None
        try:
            return self._generate(job, inputs, should_abort, out_dir)
        except Aborted as e:
            # The traceback holds the pipeline's tensors, so drop it before freeing memory.
            error = e.with_traceback(None)
        finally:
            if error is None:
                self._free()
        self._free()
        raise error

    def _free(self):
        """An abort skips diffusers' own cleanup: put the model back on the CPU and drop the cache
        (the same fix wan.py got from the Kaggle spike)."""
        import torch

        self._offloaded.maybe_free_model_hooks()
        gc.collect()
        torch.cuda.empty_cache()

    def _encode_prompt(self, prompt, negative_prompt, with_negative):
        """Embeddings for the prompt (and the negative prompt when guidance uses it). The encoder is
        loaded straight to the device, used, and dropped, so it never sits in host RAM and is gone
        before denoising starts, whatever happens."""
        import torch
        from transformers import UMT5EncoderModel

        encoder = UMT5EncoderModel.from_pretrained(
            f"{self.path}/text_encoder", torch_dtype=torch.bfloat16, device_map=self.device)
        self.pipe.text_encoder = encoder
        try:
            with torch.no_grad():
                return self.pipe.encode_prompt(
                    prompt=prompt, negative_prompt=negative_prompt, do_classifier_free_guidance=with_negative,
                    max_sequence_length=ENCODER_MAX_LENGTH, device=self.device, dtype=torch.bfloat16)
        finally:
            self.pipe.text_encoder = None
            del encoder
            gc.collect()
            torch.cuda.empty_cache()

    def _use(self, pipe):
        """Give the offload hooks to `pipe`, only if another pipeline has them."""
        if self._offloaded is not pipe:
            pipe.enable_model_cpu_offload()
            self._offloaded = pipe
        return pipe

    def _generate(self, job, inputs, should_abort, out_dir):
        import torch
        from diffusers.utils import export_to_video

        if should_abort():
            raise Aborted()

        seed = pick_seed(job.params)
        width, height, preset_steps = preset(job.params["quality"])
        total_steps = self.steps or preset_steps
        frame_count = num_frames(job.params["duration_seconds"])

        files = (inputs or {}).get(IMAGE_SLOT) or []
        start_image = fit_image(files[0]) if files else None     # fails the job before any GPU work
        pipe = self._use(self.image_pipe if start_image is not None else self.pipe)

        # Settings only, never the prompt (it may be private).
        log.info("generating %dx%d, %d frames, %d steps, %s", width, height, frame_count, total_steps,
                 "image to video" if start_image is not None else "text to video")

        def on_step(pipe, step, timestep, callback_kwargs):
            if should_abort():
                raise Aborted()          # leaves the denoising loop between two steps
            done = step + 1
            if done % LOG_EVERY_STEPS == 0 or done == total_steps:
                log.info("step %d/%d", done, total_steps)
            return callback_kwargs

        guidance = GUIDANCE_SCALE if self.guidance is None else self.guidance
        negative_prompt = job.params.get("negative_prompt") or self.default_negative_prompt or DEFAULT_NEGATIVE_PROMPT
        # The pipelines use the negative prompt only when guidance_scale is above 1.
        prompt_embeds, negative_prompt_embeds = self._encode_prompt(job.prompt, negative_prompt, guidance > 1.0)

        kwargs = dict(
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            width=width, height=height,
            num_frames=frame_count,
            num_inference_steps=total_steps,
            guidance_scale=guidance,
            generator=torch.Generator("cpu").manual_seed(seed),
            callback_on_step_end=on_step,
        )
        if start_image is not None:
            kwargs["image"] = start_image
        torch.cuda.reset_peak_memory_stats()     # so the peak below belongs to this job
        frames = pipe(**kwargs).frames[0]
        log.info("peak memory: GPU %.1f GiB, RAM %.1f GiB",
                 torch.cuda.max_memory_allocated() / 2**30, _peak_ram_gib())
        out = pathlib.Path(out_dir) / "clip.mp4"
        export_to_video(frames, str(out), fps=FPS)
        return out
