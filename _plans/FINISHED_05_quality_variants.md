# Plan 05: find a usable picture quality for Wan 2.1 1.3B

Status: FINISHED 2026-11-22 (superseded by plan 06). Phases 0, A, B and C are done; the 1.3B model was accepted as "usable, not perfect" and then replaced by the 5B model. Phase D (the 14B) was not taken.

Update 2026-11-22 (later): the owner asked for the next step up and chose Wan 2.2 TI2V 5B instead of the 14B of phase D; see `_plans/06_wan22_ti2v_5b.md` and ADR 0005.

Status 2026-11-22: Phases 0, A, B done. Owner accepted run 9 (50 steps, guidance 6, flow_shift 8, official negative prompt; the Kaggle mp4 was lost when the session reset, the contact sheet survives in the executed notebook). Phase C step 1 done (`e6c6c26`): `worker/wan.py` now has flow_shift 8.0, guidance 6.0, `fast` 832x480 at 30 steps, `high` 832x480 at 50 steps, and Wan's official negative prompt as the default (a user negative prompt replaces it). `fast` was raised from 640x368 (below the 480p training size) rather than dropped. Phase C done 2026-11-22: image rebuilt and pushed, one `high` job (seed 777, the run 9 prompt, default negative prompt) ran on a g4dn.2xlarge: 1049.7 s, 19.4 s per step, natural colour, cat in frame, same quality as Kaggle run 9 (a small stray object on the board nose in every frame). The new log lines work. Runs 5, 6, 7, 8 were skipped. Open: whether "usable, not perfect" is enough long term (Phase D, the 14B model, only if the owner wants more).

## Context
First run on AWS (2026-11-21, `_effort_logs/2026-11-21_first_gpu_run_on_aws.md`) proved the pipeline, speed and cost. The picture is the problem: the `high` clip (832x480, 30 steps, guidance 5.0, flow_shift 3.0) was judged "definitely not" usable by the owner, and `fast` (640x368, 20 steps) was soft and over-saturated. Cat on a surfboard readable, but not a clip anyone would use.

Speed and cost are not the issue (a high clip is about 200 s and $0.04 to $0.05 of GPU on a `g5.2xlarge`). So the question is what to change in the model settings, and whether the 1.3B model can do better at all.

## What we know about the settings
Verified 2026-11-21 (Phase 0). Sources: the Wan 2.1 repo, `README.md`, `generate.py` and `wan/configs/` on GitHub (Wan-Video/Wan2.1, main), and the diffusers 0.35.2 `WanPipeline` code inside our own image.

| Knob | What we use | Wan code default (T2V 1.3B) | Wan README recommendation (1.3B) | diffusers 0.35.2 default |
|---|---|---|---|---|
| steps | 20 `fast`, 30 `high` | 50 | none stated for 1.3B | 50 |
| guidance scale | 5.0 | 5.0 | 6 | 5.0 |
| flow_shift | 3.0 | 5.0 | adjust within 8 to 12 | set by the caller; the docstring example (a 14B one) says 3.0 for 480p, 5.0 for 720p |
| resolution | 640x368 `fast`, 832x480 `high` | | 480p recommended; 720p "less stable" | 832x480 |
| negative prompt | the user's short one, or none | a long default list (oversaturated, overexposed, static, blurred, low quality, extra fingers, deformed and so on) | | none |

Colour finding (2026-11-21, by eye, owner's observation plus frames extracted from the saved AWS `high` clip): the AWS clip has good anatomy but blown-out colour and contrast (flat cyan sea, orange cat clipped, white spray burned). The earlier Kaggle frames (garden cat, same model and `wan.py`) were natural. So the first question is what differs: (1) the prompt (mine had "vivid colors", "bright tropical sun", "glassy turquoise"; the Kaggle one was "sunlit garden, cinematic, detailed fur"), (2) the environment (torch 2.8.0 on an A10G in the image against torch 2.11 on a T4 on Kaggle), (3) the short negative prompt. Grid run 1 (surfing prompt, AWS settings, on Kaggle) separates (1) from (2): blown out means prompt or settings, natural means the AWS setup.

Run 1 result (2026-11-21, Kaggle T4, owner ran it): the surfing prompt with the AWS settings is blown out on Kaggle too (flat cyan, orange cat clipped, white burned; mean 179.5, std 108, motion 7.11, 819 s). So the AWS environment (torch 2.8.0, A10G) is not the cause; the prompt or the settings are. No image change is needed for the colour problem. Next runs in this order: 6 (official negative prompt), then 8 with BEST set to the baseline settings (a pure prompt test without "vivid colors" and "bright tropical sun"), then 2, 3, 4.

Run 2 result (2026-11-21, Kaggle T4): flow_shift 8.0 alone, everything else as run 1, fixes the colour. Natural teal water, orange fur with texture, no clipped highlights, more motion. Run 1 (shift 3.0) against run 2 (shift 8.0): mean 179.5 to 128.1, std 108.2 to 78.8, motion 7.11 to 14.22, time 819 s to 808 s. So the wrong `FLOW_SHIFT = 3.0` (from a diffusers 14B docstring) was the main culprit, and the fix is one constant in `worker/wan.py` plus an image rebuild. Still open: the best value in the README range (8 to 12), and whether 50 steps and guidance 6 improve detail. Next: run 4 (README recipe), then run 5 (shift 12) if time allows; runs 6 and 8 matter less now.

Run 4 result (2026-11-21, Kaggle T4): the README recipe (50 steps, guidance 6.0, shift 8.0) gives natural colour and a slightly crisper picture than run 2 (sharper board edge, cleaner foam, clearer cat stripes), same motion. Mean 116.5, std 74.7, motion 14.59, 1267 s (run 2: 128.1, 78.8, 14.22, 808 s; 1.57x the time). On the g5 a high clip of about 200 s at 30 steps would be about 310 s at 50 steps, about 0.07 USD of GPU instead of 0.04 to 0.05 (my arithmetic). Run 5 (shift 12) skipped, run 3 skipped. Next: runs 9 (second seed) and 7 (official negative prompt) at the run 4 settings; the owner's verdict on whether the video is usable is the bar.

Run 4 verdict (2026-11-22, owner): "better but cant say perfect". Claude's frame check agrees: colour fixed, cat readable (stripes, whiskers), but the cat sits at the top edge with the tail or head cropped in some frames, the board and legs morph a little between frames, and the motion is mild (cat mostly stands). That is the 1.3B model's coherence limit plus a framing gap in the prompt; more steps or guidance will not fix it.

Run 9 result (2026-11-22, Kaggle T4): second seed (777) on the run 4 settings (50 steps, guidance 6.0, shift 8.0), with Wan's official negative prompt. Natural teal and blue, no clipped highlights, cat fully in frame on the board, real wave and spray, more motion than run 4. Mean 104.3, std 77.2, motion 14.23, 1265 s (run 4: 116.5, 74.7, 14.59, 1267 s). Same quality on a second seed, so run 4 was not luck. Mild remaining flaw: the cat looks chubby and soft in some frames. Run 9 also used the official negative prompt, so it is not a clean test of it (run 7 would be); nothing in the frames says it hurts. Owner's verdict on run 9: pending.

What this says:
- We are below Wan's own defaults on steps and on flow_shift (3.0 against 5.0), and the README recommends higher values for both on this model. The 3.0 came from a diffusers docstring for the 14B model; nobody tuned it (the `worker/wan.py` header says so).
- `fast` (640x368) is below the 480p the model was trained for. Expect it to be soft however the other knobs are set.
- The official default negative prompt lists "oversaturated" and "overexposed" first. The `fast` clip was over-saturated, and our worker sends no default negative prompt.
- The authors recommend LLM prompt extension (Qwen) for detail. We have no such step. Hand-written detailed prompts are the stand-in, so a "simple" prompt is not obviously better; run 8 tests it rather than assuming it.

## Where to test: free Kaggle T4 first, not AWS
The model and code are the same on a Kaggle T4 (already used for the 2026-11-20 spike, `spike/wan_t4_quality_spike.ipynb`, built by `scripts/make_spike_notebook.py` from the real `worker/wan.py`). Picture quality does not depend on the GPU, only speed does. A Kaggle run costs $0; an AWS boot costs about $0.4 to $0.5 and a 21 min image rebuild per code change. So: iterate on Kaggle, confirm the winner once on AWS.

Why not "several variants in one AWS boot" as first asked: steps, guidance and flow_shift are not job params, so it needs worker and manifest changes, an image rebuild, a Terraform apply (the manifest is an env var) and then paid GPU time per experiment. Rejected for now; revisit if Kaggle is blocked (weekly GPU quota is about 30 h).

## Phase 0: check the recommendations (done 2026-11-21)
Done; results are in the table above. The values for the grid below come from it.

## Phase A: Kaggle grid (owner runs the notebook, Claude prepares it)
Code, test first: `WanGenerator` already takes `steps`; add optional `guidance`, `flow_shift` and `negative_prompt` default the same way (defaults unchanged, so the worker behaves exactly as before). A test pins that the pipeline gets the overrides and that the defaults are today's values (guidance 5.0, flow_shift 3.0, no default negative prompt). Then regenerate the notebook with `scripts/make_spike_notebook.py` and a variant section.

Fixed for every run: 832x480, 3 s (49 frames), same seed, same prompt (the surfing cat), contact sheet saved per run. "Official negative" means Wan's own default negative prompt (English version, from the diffusers `WanPipeline` docstring); otherwise the owner's short negative prompt. One factor changes at a time until run 4:

| Run | steps | guidance | flow_shift | negative prompt | prompt | Why |
|---|---|---|---|---|---|---|
| 1 baseline | 30 | 5.0 | 3.0 | owner's short | surfing cat | what we ran on AWS (bad) |
| 2 | 30 | 5.0 | 8.0 | owner's short | same | flow_shift alone (README range) |
| 3 | 50 | 5.0 | 3.0 | owner's short | same | steps alone (Wan and diffusers default) |
| 4 README recipe | 50 | 6.0 | 8.0 | owner's short | same | everything the README recommends |
| 5 | 50 | 6.0 | 12.0 | owner's short | same | top of the README shift range |
| 6 | 30 | 5.0 | 3.0 | official | same | does the official negative prompt fix the saturation |
| 7 | best of 1 to 5 | | | official | same | best recipe plus official negative |
| 8 | same as 7 | | | official | simple (one subject, plain background) | does a simpler prompt help |
| 9 | same as 7 | | | official | surfing cat, second seed | is it luck |

Time on the T4 (25.5 s per step at 832x480 plus about 2 min fixed): runs of 30 steps about 15 min, of 50 about 25 min. The nine runs add up to roughly 3.5 h and fit one Kaggle session (12 h limit); runs 5 and 8 can be dropped if time or quota is short. Run 2 and 3 first: they say which single knob matters most.

## Phase B: judge (owner)
Look at the contact sheets and videos side by side. Pass bar: the owner calls a clip usable for a prompt like "cat doing something cats do not do". Record the verdict per run in the effort log. Single samples are noisy, so the winner gets a second seed (run 7).

## Phase C: apply the winner (Claude code, owner AWS steps)
1. Change the presets and constants in `worker/wan.py` (`QUALITY` steps, `GUIDANCE_SCALE`, `FLOW_SHIFT`, and a default negative prompt if run 6 or 7 shows it helps), update `tests/worker/test_wan.py`, spec section 5 and the `quality` text in the manifest if timings change. Keep `fast` only if it is acceptable; it runs at 640x368, below the model's 480p training size, so it may need 832x480 with fewer steps instead, or be dropped (decision for the owner).
2. Rebuild and push the image (`scripts/build_push.sh`, about 21 min).
3. One confirmation run on AWS: raise both limits to 1 by hand, one `high` job, check the log lines added on 2026-11-21 (first test of them), measure time. Estimate: 50 steps on the g5 is about 5 min of GPU, so the run is about $0.5 in total with the cold start (my estimate). Stop commands: `docs/runbook_stop_gpu.md`. The owner can also measure the idle wait then (spec gap G7) by leaving it alone for 20 minutes, about $0.25 more.

## Phase D: if nothing is good enough
Write a separate plan for the 14B model. Known cost of that road: much larger weights (a longer cold start copy and a bigger NVMe need), more VRAM than the 24 GB of a g5.2xlarge without heavy offload, so probably a larger GPU instance class with its own Spot quota request and a higher hourly price. Not a quick change. Option 3 stays open: keep the 1.3B as a demo model and say so in the README.

## Cost
Phase 0, A, B: $0 AWS. Phase C: about $0.5 to $0.75 of credit (my estimate, g5 Spot at $0.71 to $0.87 per hour). Credit available: $139.93 (2026-11-21).

## Risks
- Kaggle weekly GPU quota and the 12 h session limit; the weights download takes about 2 min per session (116 s measured).
- Single-sample judgement is noisy; the second seed reduces it.
- fp16 on a T4 and on an A10G can differ slightly; Phase C confirms on the real instance type.
- The 1.3B model may simply be too weak; then Phase D.

## Verification
- `.venv/bin/pytest -q` green after the `guidance` and `flow_shift` change (defaults unchanged).
- Notebook regenerated from the real `worker/wan.py` (no hand edit).
- Phase C: the new log lines appear (`received job`, `generating`, `step N/M`, `uploaded`), the clip passes the owner's bar, and `gpu_seconds` is logged.

## Order of work
Phase 0 and the test-first code change are Claude's and cost nothing. Running the Kaggle notebook and judging the clips are the owner's. Nothing here needs a push, Terraform or an AWS call before Phase C.
