# Plan 05: find a usable picture quality for Wan 2.1 1.3B

Status: proposed 2026-11-21, not started. Owner decides; nothing here spends AWS credit until Phase C.

## Context
First run on AWS (2026-11-21, `_effort_logs/2026-11-21_first_gpu_run_on_aws.md`) proved the pipeline, speed and cost. The picture is the problem: the `high` clip (832x480, 30 steps, guidance 5.0, flow_shift 3.0) was judged "definitely not" usable by the owner, and `fast` (640x368, 20 steps) was soft and over-saturated. Cat on a surfboard readable, but not a clip anyone would use.

Speed and cost are not the issue (a high clip is about 200 s and $0.04 to $0.05 of GPU on a `g5.2xlarge`). So the question is what to change in the model settings, and whether the 1.3B model can do better at all.

## What we know about the settings
- `worker/wan.py` says it: guidance and flow_shift "are the documented defaults, not tuned". Steps are 20 and 30; the diffusers default is 50.
- The 1.3B model is trained for 480p, so `fast` (640x368) runs below its training size.
- Levers not yet tried: more steps, guidance scale, flow_shift, prompt wording, negative prompt, seed.
- My recollection (unverified, must be checked in step 0): the Wan 2.1 README suggests guidance about 6 and a larger flow_shift (about 8 to 12) for the 1.3B model, and 50 sampling steps by default. Treat it as a hypothesis, not a fact.

## Where to test: free Kaggle T4 first, not AWS
The model and code are the same on a Kaggle T4 (already used for the 2026-11-20 spike, `spike/wan_t4_quality_spike.ipynb`, built by `scripts/make_spike_notebook.py` from the real `worker/wan.py`). Picture quality does not depend on the GPU, only speed does. A Kaggle run costs $0; an AWS boot costs about $0.4 to $0.5 and a 21 min image rebuild per code change. So: iterate on Kaggle, confirm the winner once on AWS.

Why not "several variants in one AWS boot" as first asked: steps, guidance and flow_shift are not job params, so it needs worker and manifest changes, an image rebuild, a Terraform apply (the manifest is an env var) and then paid GPU time per experiment. Rejected for now; revisit if Kaggle is blocked (weekly GPU quota is about 30 h).

## Phase 0: check the recommendations (Claude, no cost)
Look up the Wan 2.1 README and the diffusers `WanPipeline` docs and write the recommended guidance, flow_shift and steps for the 1.3B model at 480p into this plan, with the source. Replace the unverified recollection above.

## Phase A: Kaggle grid (owner runs the notebook, Claude prepares it)
Code, test first: `WanGenerator` already takes `steps`; add optional `guidance` and `flow_shift` the same way (defaults unchanged, so the worker behaves exactly as before). A test pins that the pipeline gets the overrides and that the defaults are today's values. Then regenerate the notebook with `scripts/make_spike_notebook.py` and a variant section.

Fixed for every run: 832x480, 3 s (49 frames), same seed, same prompt (the surfing cat), same negative prompt, contact sheet saved per run. One factor changes at a time:

| Run | steps | guidance | flow_shift | prompt | Why |
|---|---|---|---|---|---|
| 1 baseline | 30 | 5.0 | 3.0 | surfing cat | what we ran on AWS (bad) |
| 2 | 50 | 5.0 | 3.0 | same | more steps |
| 3 | 30 | 6.0 | 3.0 | same | guidance |
| 4 | 30 | 5.0 | 8.0 | same | flow_shift |
| 5 | 50 | 6.0 | 8.0 | same | all three together |
| 6 | 30 | 5.0 | 3.0 | simple (one subject, plain background) | prompt effect |
| 7 | best settings | | | second seed | is it luck? |

Time on the T4 (25.5 s per step at 832x480 plus about 2 min fixed): runs of 30 steps about 15 min, of 50 about 25 min, so the grid is about 2 h and fits one Kaggle session. Values for runs 3 to 5 follow Phase 0 if it disagrees with my recollection.

## Phase B: judge (owner)
Look at the contact sheets and videos side by side. Pass bar: the owner calls a clip usable for a prompt like "cat doing something cats do not do". Record the verdict per run in the effort log. Single samples are noisy, so the winner gets a second seed (run 7).

## Phase C: apply the winner (Claude code, owner AWS steps)
1. Change the presets and constants in `worker/wan.py` (`QUALITY` steps, `GUIDANCE_SCALE`, `FLOW_SHIFT`), update `tests/worker/test_wan.py`, spec section 5 and the `quality` text in the manifest if timings change. Keep `fast` only if it is acceptable; otherwise raise it or drop the option (decision for the owner).
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
