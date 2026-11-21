#!/usr/bin/env python3
"""Generate spike/wan_t4_quality_grid.ipynb, the third Kaggle T4 notebook: which settings give a usable picture?

Plan 05 (`_plans/05_quality_variants.md`). The first AWS run (2026-11-21) was fast and cheap but the
picture was judged unusable. Our steps (20 or 30), flow_shift (3.0) and negative prompt differ from
what Wan's own repo uses and recommends for the 1.3B model. This notebook runs nine variants, one cell
each, on one seed and one prompt, and saves every clip as an mp4 so they can be watched side by side.

Setup cells are shared with make_spike_notebook.py (weights commit, pins, the worker's own wan.py).
Re-run after changing worker/wan.py:   python3 scripts/make_quality_grid_notebook.py
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_spike_notebook", ROOT / "scripts" / "make_spike_notebook.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

INTRO = """# Wan 2.1 on a T4: which settings give a usable picture?

The AWS test clips looked bad. Our settings are below Wan's own defaults (steps 20 or 30 against 50,
flow_shift 3.0 against 5.0) and the Wan README recommends guidance 6 and flow_shift 8 to 12 for the 1.3B
model. This notebook runs nine variants of one 3 s clip (832x480, same seed, same prompt) and saves each
as an **mp4** in `/kaggle/working` (Output tab) so you can watch them side by side.

**Run 1 first, and compare it with the clip from AWS** (the `high` clip saved in `outputs/`): the AWS clip had
good anatomy but blown-out colour (flat cyan sea, orange cat clipped, white spray burned), while the earlier Kaggle
frames (a cat in a garden) looked natural. Run 1 uses the surfing prompt and the AWS settings. If run 1 is blown out too,
the prompt or settings cause it ("vivid colors" and "bright tropical sun" were in the prompt); if run 1 looks natural,
the cause is the AWS setup (torch 2.8.0 on an A10G against torch 2.11 on a T4).

Run the cells **one at a time with Shift+Enter, top to bottom**. Each run is its own cell; if one fails, go on.
A 30-step run takes about 15 minutes and a 50-step run about 25 (T4). Runs 2 and 3 tell you first which single
knob matters most; runs 5 and 8 can be skipped if time is short. Free, nothing on AWS.

Same settings as before: Accelerator **GPU T4 x2**, Internet **On**.
"""

CELL_GRID = '''import shutil
from diffusers import UniPCMultistepScheduler

PROMPT_SURF = ("A fluffy orange tabby cat surfing a glassy turquoise wave on a small white surfboard, paws spread for "
               "balance, sea spray flying, bright tropical sun, cinematic slow motion, side-tracking camera, sharp focus, vivid colors")
PROMPT_SIMPLE = "An orange cat riding a surfboard on a calm blue sea, sunny day, clear sky"
# What the site sent on AWS (the owner's short negative prompt) and Wan's own default, from the diffusers WanPipeline docstring.
NEG_OWNER = "blurry, distorted, extra limbs, deformed paws, text, watermark, low quality, jittery"
NEG_OFFICIAL = ("Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, "
                "worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, "
                "poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, "
                "three legs, many people in the background, walking backwards")
SEED = 1234
RESULTS = []

def set_shift(shift):
    """flow_shift is read when the scheduler is built, so rebuild it instead of reloading the model (about 2 min)."""
    gen.pipe.scheduler = UniPCMultistepScheduler.from_config(gen.pipe.scheduler.config, flow_shift=shift)

def grid_run(label, steps, guidance, shift, negative, prompt=PROMPT_SURF, seed=SEED):
    """One 3 s clip at 832x480 with the given settings. Saves /kaggle/working/<label>.mp4 and a contact sheet."""
    gen.steps, gen.guidance = steps, guidance
    set_shift(shift)
    j = types.SimpleNamespace(prompt=prompt, params={"seed": seed, "duration_seconds": "3", "quality": "high",
                                                     "negative_prompt": negative})
    out = pathlib.Path(f"/tmp/out-{label}"); out.mkdir(exist_ok=True)
    torch.cuda.reset_peak_memory_stats()
    t = time.time()
    path = gen.generate(j, {}, lambda: False, out)
    secs = time.time() - t
    shutil.copy(path, f"/kaggle/working/{label}.mp4")
    frames = read_frames(path)
    contact_sheet(frames, label)
    q = quality(frames)
    RESULTS.append({"run": label, "steps": steps, "guidance": guidance, "flow_shift": shift,
                    "negative": "official" if negative == NEG_OFFICIAL else ("owner" if negative == NEG_OWNER else "none"),
                    "seconds": round(secs), "mean": q["mean"], "std": q["std"], "motion": q["motion"]})
    print(RESULTS[-1], "- watch /kaggle/working/" + label + ".mp4")
'''

RUNS = [
    ("1. Baseline (what ran on AWS: bad)", 'grid_run("run1-baseline", steps=30, guidance=5.0, shift=3.0, negative=NEG_OWNER)'),
    ("2. flow_shift alone (README range 8 to 12)", 'grid_run("run2-shift8", steps=30, guidance=5.0, shift=8.0, negative=NEG_OWNER)'),
    ("3. Steps alone (Wan and diffusers default is 50)", 'grid_run("run3-steps50", steps=50, guidance=5.0, shift=3.0, negative=NEG_OWNER)'),
    ("4. The README recipe: 50 steps, guidance 6, shift 8", 'grid_run("run4-readme-recipe", steps=50, guidance=6.0, shift=8.0, negative=NEG_OWNER)'),
    ("5. Top of the README shift range (optional)", 'grid_run("run5-shift12", steps=50, guidance=6.0, shift=12.0, negative=NEG_OWNER)'),
    ("6. Wan's official negative prompt on the baseline", 'grid_run("run6-official-negative", steps=30, guidance=5.0, shift=3.0, negative=NEG_OFFICIAL)'),
]

CELL_BEST = '''# Look at runs 1 to 6 first, then set BEST to the settings you liked most (default: the README recipe).
BEST = dict(steps=50, guidance=6.0, shift=8.0)
print("BEST =", BEST)
'''

RUNS_AFTER = [
    ("7. Best settings plus Wan's official negative prompt", 'grid_run("run7-best-plus-negative", negative=NEG_OFFICIAL, **BEST)'),
    ("8. A simpler prompt (optional)", 'grid_run("run8-simple-prompt", negative=NEG_OFFICIAL, prompt=PROMPT_SIMPLE, **BEST)'),
    ("9. Second seed on the best settings (is it luck?)", 'grid_run("run9-second-seed", negative=NEG_OFFICIAL, seed=777, **BEST)'),
]

CELL_REPORT = '''# REPORT: copy everything this cell prints and paste it back, then download the mp4 files from the Output tab.
print("run | steps | guidance | shift | negative | seconds | mean | std | motion")
for r in RESULTS:
    print(f"{r['run']} | {r['steps']} | {r['guidance']} | {r['flow_shift']} | {r['negative']} | {r['seconds']} | {r['mean']} | {r['std']} | {r['motion']}")
json.dump(RESULTS, open("/kaggle/working/grid_report.json", "w"), indent=1)
print("\\nAlso saved: grid_report.json. Watch the mp4 files; the numbers only catch black or frozen clips, not ugly ones.")
'''


def build():
    nb = base.build()
    head = nb["cells"][1:7]      # env, install, worker files, weights download, model load, helpers
    cells = [base.md(INTRO), *head, base.code(CELL_GRID)]
    for title, call in RUNS:
        cells += [base.md("## " + title), base.code(call)]
    cells += [base.md("## Pick the best settings"), base.code(CELL_BEST)]
    for title, call in RUNS_AFTER:
        cells += [base.md("## " + title), base.code(call)]
    cells += [base.md("## Report"), base.code(CELL_REPORT)]
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": nb["metadata"], "cells": cells}


if __name__ == "__main__":
    out = ROOT / "spike" / "wan_t4_quality_grid.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {out}")
