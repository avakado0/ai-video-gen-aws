#!/usr/bin/env python3
"""Generate spike/wan_spike.ipynb, the Kaggle T4 notebook that checks the worker's model code.

The notebook embeds worker/wan.py and worker/errors.py as they are now and installs the pins from
worker/requirements.txt (minus torch, which Kaggle ships, and boto3, which the spike does not
use). So the spike runs the exact class the worker will run. Re-run this script after changing
either file:   python3 scripts/make_spike_notebook.py
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEIGHTS_COMMIT = "0fad780a534b6463e45facd96134c9f345acfa5b"  # spec section 5, Wan2.1-T2V-1.3B-Diffusers

INTRO = """# Wan 2.1 T2V 1.3B on a T4: the GPU spike

Runs `worker/wan.py` (the class the worker ships) on Kaggle's free T4 and fills a pass/fail table.
Gate: decide the final `duration_seconds` list and the step count (spec section 5).

**Set up before "Run all"**
- Settings, Accelerator: **GPU T4 x2** (the notebook uses only one, like a g4dn.2xlarge).
- Settings, Internet: **On** (needs a phone-verified account). No token needed; the model is ungated.
- About 30 GB of weights download to `/tmp`; the whole run takes roughly 40 to 60 minutes.

At the end, copy the **REPORT** cell output and paste it back. It holds no secrets.
Kaggle has about 29 GB RAM; a g4dn.2xlarge has 32 GB, so a pass here is slightly conservative.
"""

CELL_ENV = '''import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # one T4, like the real instance (must be set before torch loads)
import json, shutil, subprocess, sys, time

REPORT = []
def check(name, status, detail=""):
    """status: PASS, FAIL or INFO"""
    REPORT.append({"check": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")

import psutil, torch
gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
vram = torch.cuda.get_device_properties(0).total_memory / 2**30 if torch.cuda.is_available() else 0
check("GPU is a T4", "PASS" if "T4" in gpu else "FAIL", f"{gpu}, {vram:.1f} GiB VRAM")
check("RAM", "INFO", f"{psutil.virtual_memory().total / 2**30:.1f} GiB total (g4dn.2xlarge has 32)")
free_tmp = shutil.disk_usage("/tmp").free / 2**30
check("Disk for weights in /tmp", "PASS" if free_tmp > 40 else "FAIL", f"{free_tmp:.0f} GiB free, need about 35")
check("torch", "INFO", torch.__version__ + " (cuda " + str(torch.version.cuda) + "); the image will carry torch 2.8.0")
'''

CELL_INSTALL = '''PINS = __PINS__
r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PINS], capture_output=True, text=True)
print(r.stdout[-2000:], r.stderr[-2000:])
import importlib.metadata as md
got = {p: md.version(p) for p in ("diffusers", "transformers", "accelerate", "safetensors", "sentencepiece", "ftfy", "imageio", "imageio-ffmpeg")}
check("pinned packages installed", "PASS" if r.returncode == 0 else "FAIL", json.dumps(got))
'''

CELL_WORKER = '''# The worker's own files, embedded by scripts/make_spike_notebook.py
import pathlib
FILES = __FILES__
root = pathlib.Path("/kaggle/working/spike")
(root / "worker").mkdir(parents=True, exist_ok=True)
for name, text in FILES.items():
    (root / "worker" / name).write_text(text)
sys.path.insert(0, str(root))
from worker.wan import WanGenerator, num_frames
from worker.errors import Aborted
print(sorted(p.name for p in (root / "worker").iterdir()))
'''

CELL_DOWNLOAD = '''from huggingface_hub import snapshot_download
WEIGHTS = "/tmp/wan-weights/wan2-1-t2v-1-3b"      # same layout as /data/models/<model> on the instance
t = time.time()
snapshot_download("Wan-AI/Wan2.1-T2V-1.3B-Diffusers", revision="__COMMIT__", local_dir=WEIGHTS,
                  allow_patterns=["model_index.json", "scheduler/*", "text_encoder/*", "tokenizer/*", "transformer/*", "vae/*"])
size = sum(f.stat().st_size for f in pathlib.Path(WEIGHTS).rglob("*") if f.is_file()) / 2**30
check("weights downloaded", "PASS" if 25 < size < 40 else "FAIL", f"{size:.1f} GiB in {time.time() - t:.0f} s (spec says about 29)")
os.environ["HF_HUB_OFFLINE"] = "1"                 # the image never reaches out to Hugging Face
os.environ["TRANSFORMERS_OFFLINE"] = "1"
'''

CELL_LOAD = '''gen = WanGenerator("/tmp/wan-weights", "wan2-1-t2v-1-3b", steps=30)
t = time.time()
try:
    gen.load()
    check("model loads (fp16 transformer and text encoder, fp32 VAE, cpu offload)", "PASS",
          f"{time.time() - t:.0f} s, RAM now {psutil.Process().memory_info().rss / 2**30:.1f} GiB")
except Exception as e:
    check("model loads", "FAIL", repr(e)[:400])
    raise
'''

CELL_HELPERS = '''import types
PROMPT_A = "A cat walking through a sunlit garden, cinematic, detailed fur"
PROMPT_B = "A red balloon floating up above a city street at sunset, soft light"

import numpy as np, imageio
from PIL import Image
from IPython.display import display

def job(prompt, seconds, seed):
    return types.SimpleNamespace(prompt=prompt, params={"seed": seed, "duration_seconds": seconds, "negative_prompt": ""})

def read_frames(path):
    rd = imageio.get_reader(str(path), "ffmpeg")
    frames = [np.asarray(f) for f in rd]
    rd.close()
    return frames

def contact_sheet(frames, name):
    idx = np.linspace(0, len(frames) - 1, 6).astype(int)
    thumbs = [Image.fromarray(frames[i]).resize((416, 240)) for i in idx]
    sheet = Image.new("RGB", (416 * 3, 240 * 2))
    for n, im in enumerate(thumbs):
        sheet.paste(im, ((n % 3) * 416, (n // 3) * 240))
    sheet.save(f"/kaggle/working/{name}.png")
    display(sheet)

def quality(frames):
    a = np.stack(frames).astype(np.float32)
    motion = float(np.abs(np.diff(a, axis=0)).mean())
    return {"mean": round(float(a.mean()), 1), "std": round(float(a.std()), 1), "motion": round(motion, 2),
            "black_frames": int(sum(f.max() < 8 for f in frames))}

def run(label, prompt, seconds, steps, seed, should_abort=lambda: False, show=True):
    """One generation. Returns (frames, seconds, peak_vram_gib) or raises."""
    gen.steps = steps
    out = pathlib.Path(f"/tmp/out-{label}"); out.mkdir(exist_ok=True)
    torch.cuda.reset_peak_memory_stats()
    t = time.time()
    path = gen.generate(job(prompt, seconds, seed), {}, should_abort, out)
    secs = time.time() - t
    peak = torch.cuda.max_memory_allocated() / 2**30
    frames = read_frames(path)
    if show:
        contact_sheet(frames, label)
    return frames, secs, peak

def judge(label, frames, secs, peak, want_frames, limit_s=900):
    q = quality(frames)
    clean = q["black_frames"] == 0 and q["std"] > 10 and q["mean"] > 8 and not np.isnan(q["mean"])
    moving = q["motion"] > 0.5
    ok = len(frames) == want_frames and clean and moving and secs <= limit_s
    why = f"{len(frames)} frames (want {want_frames}), {secs:.0f} s, peak VRAM {peak:.1f} GiB (contact sheet attached), quality {q}"
    if not clean: why += " | BLACK/FLAT FRAMES: try fp32 transformer (spec section 5)"
    if not moving: why += " | FRAMES BARELY MOVE"
    if secs > limit_s: why += f" | over {limit_s} s"
    check(label, "PASS" if ok else "FAIL", why)
'''

CELL_3S = '''try:
    f3, s3, p3 = run("clip-3s-30steps", PROMPT_A, "3", 30, 1234)
    judge("3 s clip (49 frames, 30 steps)", f3, s3, p3, 49)
except Exception as e:
    check("3 s clip (49 frames, 30 steps)", "FAIL", repr(e)[:400])
'''

CELL_5S = '''try:
    f5, s5, p5 = run("clip-5s-30steps", PROMPT_B, "5", 30, 99)
    judge("5 s clip (81 frames, 30 steps)", f5, s5, p5, 81)
except Exception as e:
    check("5 s clip (81 frames, 30 steps)", "FAIL", repr(e)[:400] + " (if out of memory: drop the 5 s option)")
'''

CELL_STEPS = '''# Is 20 steps good enough? Same seed, same prompt as the 3 s clip: compare the two contact sheets by eye.
try:
    f3b, s3b, p3b = run("clip-3s-20steps", PROMPT_A, "3", 20, 1234)
    check("3 s clip at 20 steps", "INFO", f"{s3b:.0f} s vs {s3:.0f} s at 30 steps; look at clip-3s-20steps.png against clip-3s-30steps.png")
except Exception as e:
    check("3 s clip at 20 steps", "FAIL", repr(e)[:300])
'''

CELL_ABORT = '''# should_abort flips true 20 s into a run; the generator must raise Aborted at the next step.
flip = {}
def abort_after_20s(_t0=[None]):
    if _t0[0] is None: _t0[0] = time.time()
    if time.time() - _t0[0] > 20:
        flip.setdefault("at", time.time())
        return True
    return False
try:
    run("abort", PROMPT_A, "3", 30, 5, should_abort=abort_after_20s, show=False)
    check("abort between steps", "FAIL", "the run finished instead of stopping")
except Aborted:
    lag = time.time() - flip["at"]
    freed = torch.cuda.memory_allocated() / 2**30
    check("abort between steps", "PASS" if lag < 60 else "FAIL", f"stopped {lag:.1f} s after the flag (one step is the limit); GPU memory still held {freed:.1f} GiB")
except Exception as e:
    check("abort between steps", "FAIL", repr(e)[:300])
'''

CELL_SEED = '''# Same seed twice should repeat (a hint, not a guarantee on GPUs); another seed must differ. 8 steps keeps it quick.
try:
    a, *_ = run("seed-a1", PROMPT_A, "3", 8, 7, show=False)
    b, *_ = run("seed-a2", PROMPT_A, "3", 8, 7, show=False)
    c, *_ = run("seed-b", PROMPT_A, "3", 8, 8, show=False)
    same = float(np.abs(np.stack(a).astype(float) - np.stack(b).astype(float)).mean())
    diff = float(np.abs(np.stack(a).astype(float) - np.stack(c).astype(float)).mean())
    check("same seed repeats", "INFO", f"mean pixel difference {same:.2f} (0 = identical)")
    check("different seed differs", "PASS" if diff > 2 * max(same, 0.5) else "FAIL", f"mean pixel difference {diff:.2f}")
except Exception as e:
    check("seed behaviour", "FAIL", repr(e)[:300])
'''

CELL_REPORT = '''# REPORT: copy everything this cell prints and paste it back.
verdict = "ALL PASS" if all(r["status"] != "FAIL" for r in REPORT) else "HAS FAILURES"
print(f"=== SPIKE REPORT: {verdict} ===")
for r in REPORT:
    print(f"{r['status']:5} | {r['check']} | {r['detail']}")
json.dump(REPORT, open("/kaggle/working/spike_report.json", "w"), indent=1)
print("\\nAlso saved: spike_report.json and the contact-sheet PNGs in /kaggle/working (Output tab).")
print("Questions this answers: loads on a T4 | seconds per 3 s and 5 s clip | black or distorted frames | VAE fp32 | 20 vs 30 steps | abort works")
'''


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.splitlines(keepends=True)}


def pins():
    keep = []
    for line in (ROOT / "worker" / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith(("torch==", "boto3==")):
            keep.append(line)
    return keep


def build():
    files = {n: (ROOT / "worker" / n).read_text() for n in ("__init__.py", "errors.py", "wan.py")}
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                     "language_info": {"name": "python"}},
        "cells": [
            md(INTRO),
            code(CELL_ENV),
            code(CELL_INSTALL.replace("__PINS__", repr(pins()))),
            code(CELL_WORKER.replace("__FILES__", repr(files))),
            code(CELL_DOWNLOAD.replace("__COMMIT__", WEIGHTS_COMMIT)),
            code(CELL_LOAD),
            code(CELL_HELPERS),
            md("## 3 s clip (49 frames)"), code(CELL_3S),
            md("## 5 s clip (81 frames)"), code(CELL_5S),
            md("## Step count: 20 vs 30"), code(CELL_STEPS),
            md("## Abort between steps"), code(CELL_ABORT),
            md("## Seeds"), code(CELL_SEED),
            md("## Report"), code(CELL_REPORT),
        ],
    }


if __name__ == "__main__":
    out = ROOT / "spike" / "wan_spike.ipynb"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {out}")
