"""CPU check of worker/wan22.py against the REAL diffusers classes, with tiny random weights (plan 06).

Free and about a minute: it builds the real AutoencoderKLWan (random weights, 0.7 B parameters, about
3 GB of RAM) and tiny stand-ins for the transformer and text encoder, then runs the real
Wan22Generator.load(), the text path (3 s and 5 s), the image-to-video path and the decode, at 128x96.
It catches wiring and shape bugs before a GPU trial costs money (it found nothing wrong with the
image-to-video wiring and reproduced the tiled-decode bug of trial 1). It says nothing about GPU
memory, speed or quality.

One-off setup, in a scratch folder (the downloaded files are data, not code):

  python3 -m venv venv
  venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0
  venv/bin/pip install diffusers==0.35.2 transformers==4.57.6 accelerate==1.15.0 safetensors sentencepiece \
      ftfy regex imageio imageio-ffmpeg Pillow==12.3.0 huggingface-hub==0.36.2 numpy
  SHA=b8fff7315c768468a5333511427288870b2e9635      # the pinned 5B commit
  for f in model_index.json scheduler/scheduler_config.json text_encoder/config.json \
      tokenizer/special_tokens_map.json tokenizer/spiece.model tokenizer/tokenizer.json \
      tokenizer/tokenizer_config.json transformer/config.json vae/config.json; do
    mkdir -p cfg/$(dirname $f)
    curl -sSL --fail -o cfg/$f https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers/resolve/$SHA/$f
  done

Run:   venv/bin/python -I scripts/cpu_check_wan22.py cfg
"""
import json
import pathlib
import sys
import tempfile
import time
import types

REPO = str(pathlib.Path(__file__).resolve().parents[1])
CFG = pathlib.Path(sys.argv[1])
sys.path[:0] = [REPO, REPO + "/lambdas"]

import imageio
import torch
from diffusers import (AutoencoderKLWan, UniPCMultistepScheduler, WanImageToVideoPipeline, WanPipeline,
                       WanTransformer3DModel)
from PIL import Image
from transformers import AutoTokenizer, UMT5Config, UMT5EncoderModel

from worker import wan22

torch.manual_seed(0)


def cfg(name):
    return {k: v for k, v in json.loads((CFG / name).read_text()).items() if not k.startswith("_")}


t0 = time.time()
print("building real VAE (random weights) ...", flush=True)
vae = AutoencoderKLWan.from_config(cfg("vae/config.json"))
print("  vae params %.2f B, %.0fs" % (sum(p.numel() for p in vae.parameters()) / 1e9, time.time() - t0), flush=True)

tcfg = cfg("transformer/config.json")
tcfg.update(num_layers=1, num_attention_heads=2, ffn_dim=512, text_dim=64)      # tiny, same shape logic
transformer = WanTransformer3DModel.from_config(tcfg)
text_encoder = UMT5EncoderModel(UMT5Config(vocab_size=256384, d_model=64, d_kv=16, d_ff=128, num_layers=1, num_heads=4))
tokenizer = AutoTokenizer.from_pretrained(str(CFG / "tokenizer"))
scheduler = UniPCMultistepScheduler.from_config(cfg("scheduler/scheduler_config.json"))
tiny = WanPipeline(tokenizer=tokenizer, text_encoder=text_encoder, vae=vae, scheduler=scheduler,
                   transformer=transformer, transformer_2=None, boundary_ratio=None, expand_timesteps=True)

# Save the tiny pipeline as a model folder, then run the REAL Wan22Generator.load() on it: the real
# from_pretrained calls (text_encoder=None, bf16), the image pipeline built from the loaded parts, and, per job,
# the real on-demand UMT5 encoder load. Only things a CPU cannot do are patched out.
models = pathlib.Path(tempfile.mkdtemp())
tiny.save_pretrained(str(models / "wan2-2-ti2v-5b"))
del tiny, vae, text_encoder
WanPipeline.enable_model_cpu_offload = lambda self: None
WanImageToVideoPipeline.enable_model_cpu_offload = lambda self: None
torch.cuda.reset_peak_memory_stats = lambda: None
torch.cuda.max_memory_allocated = lambda *a: 0
torch.cuda.empty_cache = lambda: None

# Shrink: 128x96 is a multiple of 16 (VAE) x 2 (patch); 9 and 13 frames are 4n+1.
wan22.WIDTH, wan22.HEIGHT = 128, 96
wan22.FRAMES = {"3": 9, "5": 13}

# device="cpu": the encoder is loaded there instead of on the GPU. vae_dtype=float32: a CPU runs bf16 convolutions
# extremely slowly (minutes per job), and this check is about wiring; the unit tests pin bf16 as the default.
gen = wan22.Wan22Generator(models, "wan2-2-ti2v-5b", steps=2, device="cpu", vae_dtype=torch.float32)
gen.load()
vae = gen.pipe.vae
print("load() ok. text encoder not loaded:", gen.pipe.text_encoder is None and gen.image_pipe.text_encoder is None,
      "| image pipe shares the transformer:", gen.image_pipe.transformer is gen.pipe.transformer,
      "| transformer dtype:", gen.pipe.transformer.dtype, flush=True)
assert gen.pipe.text_encoder is None and gen.image_pipe.text_encoder is None
assert gen.pipe.transformer.dtype == torch.bfloat16

tmp = pathlib.Path(tempfile.mkdtemp())
job = types.SimpleNamespace(prompt="a cat on a surfboard", params={"duration_seconds": "3", "quality": "fast", "seed": 1})


def run(label, inputs=None, duration="3"):
    j = types.SimpleNamespace(prompt=job.prompt, params={**job.params, "duration_seconds": duration})
    out = tmp / label
    out.mkdir()
    t = time.time()
    path = gen.generate(j, inputs or {}, lambda: False, out)
    frames = imageio.mimread(str(path), memtest=False)
    assert gen.pipe.text_encoder is None, "the prompt encoder should be dropped after the job"
    print(f"{label}: ok, {len(frames)} frames of {frames[0].shape[1]}x{frames[0].shape[0]}, {time.time() - t:.1f}s", flush=True)


run("text_3s")
run("text_5s", duration="5")
photo = tmp / "photo.png"
Image.new("RGB", (900, 600), (200, 120, 60)).save(photo)
run("image_to_video", {"image": [photo]})
run("text_again_after_image")     # the offload hooks move back (a no-op here, but the call path runs)

print("--- tiling, to reproduce the failure seen on AWS ---", flush=True)
vae.enable_tiling(tile_sample_min_height=32, tile_sample_min_width=32,
                  tile_sample_stride_height=24, tile_sample_stride_width=24)
try:
    run("text_with_tiling")
    print("tiling: NO ERROR (diffusers may have fixed the tiled decode; recheck before turning it on)")
except RuntimeError as e:
    print("tiling: RuntimeError:", str(e)[:140])
vae.disable_tiling()
run("text_tiling_off_again")
print("ALL DONE, total %.0fs" % (time.time() - t0))
