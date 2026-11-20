#!/usr/bin/env python3
"""Generate spike/wan_t4_speed_spike.ipynb, the second Kaggle T4 notebook: why is a clip so slow, and what helps?

First spike: 25.5 s per denoising step (3 s clip). Only measures time per step and peak VRAM for
a handful of variants, with 5 steps each. Setup cells are shared with make_spike_notebook.py, so the
weights commit, the pins and the worker code are the same.   python3 scripts/make_speed_notebook.py
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_spike_notebook", ROOT / "scripts" / "make_spike_notebook.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

INTRO = """# Wan 2.1 on a T4: what makes a clip slow?

First spike: 25.5 s per denoising step, 15 min for a 3 s clip, 39 min for 5 s. This notebook times
**5 steps** of several variants (about 4 minutes each) and prints seconds per step and peak VRAM.

Run the cells **one at a time with Shift+Enter, top to bottom** (not "Run all"; the last two are optional).
Same settings as before: Accelerator **GPU T4 x2**, Internet **On**. Free, nothing on AWS.
"""

CELL_TOOLS = '''PROMPT = "A cat walking through a sunlit garden, cinematic, detailed fur"

def timed(label, seconds="3", steps=5, width=832, height=480, guidance=5.0, embeds=None, seed=1234):
    """Run the pipeline for a few steps. Returns (seconds per step, total seconds, peak VRAM GiB, frames)."""
    from worker.wan import num_frames
    stamps = []
    def on_step(pipe, i, t, kw):
        stamps.append(time.time())
        return kw
    kwargs = dict(width=width, height=height, num_frames=num_frames(seconds), num_inference_steps=steps,
                  guidance_scale=guidance, generator=torch.Generator("cpu").manual_seed(seed),
                  callback_on_step_end=on_step, output_type="np")
    kwargs.update(embeds if embeds else {"prompt": PROMPT})
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    out = gen.pipe(**kwargs)
    total = time.time() - t0
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]      # skip the first step, it includes warm-up
    per_step = sorted(gaps)[len(gaps) // 2] if gaps else float("nan")
    peak = torch.cuda.max_memory_allocated() / 2**30
    check(label, "INFO", f"{per_step:.1f} s per step, {total:.0f} s total for {steps} steps, peak VRAM {peak:.1f} GiB")
    return per_step, total, peak, out.frames[0]
'''

CELL_BASELINE = '''# A. baseline: exactly what the worker does now (enable_model_cpu_offload)
try:
    base_step, *_ = timed("A baseline: model cpu offload, 832x480, with guidance")
except Exception as e:
    check("A baseline", "FAIL", repr(e)[:300])
'''

CELL_RES = '''# B. smaller picture: 640x368 has about 0.6x the tokens, attention cost drops faster than that
try:
    timed("B 640x368, with guidance", width=640, height=368)
except Exception as e:
    check("B 640x368", "FAIL", repr(e)[:300])
'''

CELL_NOCFG = '''# C. no classifier-free guidance (guidance_scale 1): one model pass per step instead of two
try:
    timed("C guidance_scale 1.0 (no CFG), 832x480", guidance=1.0)
except Exception as e:
    check("C no CFG", "FAIL", repr(e)[:300])
'''

CELL_RESIDENT = '''# D. transformer and VAE stay on the GPU; only the text encoder is moved out after encoding.
# (The whole pipeline does not fit: the text encoder alone is about 11 GiB in fp16.)
try:
    gen.pipe.remove_all_hooks()
    gen.pipe.text_encoder.to("cuda")
    with torch.no_grad():
        pe, ne = gen.pipe.encode_prompt(PROMPT, negative_prompt=None, do_classifier_free_guidance=True, device="cuda")
    gen.pipe.text_encoder.to("cpu"); torch.cuda.empty_cache()
    gen.pipe.transformer.to("cuda"); gen.pipe.vae.to("cuda")
    timed("D resident transformer and VAE, 832x480, with guidance", embeds={"prompt_embeds": pe, "negative_prompt_embeds": ne})
except Exception as e:
    check("D resident", "FAIL", repr(e)[:300])
'''

CELL_QUALITY = '''# OPTIONAL, about 10 minutes: does 640x368 still look fine at 20 steps? Compare with clip-3s-20steps.png from the first run.
try:
    gen.pipe.enable_model_cpu_offload()
    _, secs, peak, frames = timed("E 640x368 at 20 steps", steps=20, width=640, height=368)
    from diffusers.utils import export_to_video
    frames = [(f * 255).astype("uint8") for f in frames]
    contact_sheet(frames, "clip-640x368-20steps")
except Exception as e:
    check("E 640x368 quality", "FAIL", repr(e)[:300])
'''

CELL_REPORT = '''print("=== SPEED REPORT ===")
for r in REPORT:
    print(f"{r['status']:5} | {r['check']} | {r['detail']}")
json.dump(REPORT, open("/kaggle/working/speed_report.json", "w"), indent=1)
'''


def build():
    nb = base.build()
    cells = nb["cells"]
    # cells 0..6 are intro, env, install, worker, download, load, helpers: keep them, swap the intro
    head = cells[1:7]
    return {
        "nbformat": 4, "nbformat_minor": 5, "metadata": nb["metadata"],
        "cells": [
            base.md(INTRO), *head, base.code(CELL_TOOLS),
            base.md("## A. Baseline"), base.code(CELL_BASELINE),
            base.md("## B. Smaller picture"), base.code(CELL_RES),
            base.md("## C. No guidance"), base.code(CELL_NOCFG),
            base.md("## D. Transformer stays on the GPU"), base.code(CELL_RESIDENT),
            base.md("## Report"), base.code(CELL_REPORT),
            base.md("## Optional: quality at the smaller picture"), base.code(CELL_QUALITY),
            base.code(CELL_REPORT),
        ],
    }


if __name__ == "__main__":
    out = ROOT / "spike" / "wan_t4_speed_spike.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {out}")
