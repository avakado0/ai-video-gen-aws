# Plan 06: switch the product model from Wan 2.1 T2V 1.3B to Wan 2.2 TI2V 5B

Canonical plan (grilled with the owner 2026-11-22; owner approved implementing it the same day, phase A first). Status: phase A done 2026-11-22 (code, manifest, Terraform edit and docs, all offline; 354 tests). Phase B (owner steps) is next; nothing is deployed. Plan 05's "Phase D" road, taken with the 5B model instead of the 14B.

## Context
Plan 05 got Wan 2.1 1.3B to "usable, not perfect" (owner's words, 2026-11-22). The owner asks whether Wan 2.2 5B can replace it. Checked 2026-11-22 against the Hugging Face API and the Wan2.2 repo:

- `Wan-AI/Wan2.2-TI2V-5B-Diffusers`, Apache-2.0, ungated, 34.2 GB, commit `b8fff7315c768468a5333511427288870b2e9635`. `model_index.json` says `WanPipeline`, `expand_timesteps: true`, `AutoencoderKLWan` (new 16x16x4 VAE), no `transformer_2`.
- Wan's own config for it (`wan/configs/wan_ti2v_5B.py`): 24 fps, flow_shift (`sample_shift`) 5.0, 50 steps, guidance 5.0, 121 frames, bf16. README: 1280x704, "at least 24 GB VRAM" with offload (RTX 4090), "under 9 min for 5 s at 720p" on a 4090. These come from Wan's repo, not from a diffusers docstring (plan 05's lesson: the 14B docstring's flow_shift 3.0 was wrong for 1.3B).
- diffusers 0.35.2 (our pin) has `expand_timesteps` in both `WanPipeline` and `WanImageToVideoPipeline`, so the 5B works without bumping the pin.
- The 5B does text-to-video and image-to-video. It has no audio input, so it does not cover plan 04's talking avatar. Plan 04 stays pending as a separate, later decision (one model per deployment).

## Decisions (owner, grill session 2026-11-22)
1. **Role:** 5B replaces 1.3B as the deployed text(+image)-to-video product. Avatar (plan 04) stays pending.
2. **Fleet:** 24 GB floor. g4dn (T4, 16 GB, no bf16) is dropped. Trial 1 on `g5.2xlarge` only; add `g6.2xlarge` (L4, 24 GB) in a second Terraform change after trial 1 and confirm it with one more run. The L4's speed is unmeasured.
3. **Inputs v1:** prompt required, plus an optional `image` slot (png/jpeg, 10 MiB, as in the mock `text-image.json`). This is the first real use of `POST /uploads` against real S3 (plan 04 step F).
4. **Presets:** fixed 1280x704 at 24 fps. `quality` = steps: `fast` 30, `high` 50. `duration_seconds` "3" = 73 frames, "5" = 121 frames (both 4n+1). Guidance 5.0, flow_shift 5.0, Wan's default negative prompt (reuse `DEFAULT_NEGATIVE_PROMPT`, a user one replaces it). No 480p tier until shown to hold up.
5. **Start image fit:** resize and centre-crop to 1280x704. Reject an image whose short side is under 512 px, or that cannot be decoded, with `PermanentJobError` (`invalid_input`, field `image`).
6. **Code layout:** new `worker/wan22.py`; `worker/wan.py` and its tests stay as the parked 1.3B path. `worker/__main__.py` picks the generator by the manifest `model` name. Share helpers (`pick_seed`, `DEFAULT_NEGATIVE_PROMPT`, constants) by importing from `wan.py`; do not copy them.
7. **ADR 0005** "Wan 2.2 5B and the 24 GB GPU floor" (model choice, fleet, cost trade-off, unmeasured claims labelled). The avatar ADR becomes 0006 if written.
8. **Weights:** every boot syncs the whole `models/` prefix (`terraform/user_data.sh.tftpl:16`), so keeping both models would copy about 63 GB per boot. Owner chose to **delete the 1.3B weights from S3** by hand. Rollback to 1.3B then needs a fresh HF copy (temp EC2 runbook). **Order: delete only after the 5B trial is accepted**, or a failed 5B leaves no fallback. Claude never touches the models bucket.

## Steps

### A. Code, test first (Claude, no AWS)
1. Tests in `tests/worker/test_wan22.py` with fake pipelines (pattern of `tests/worker/test_wan.py`), then `worker/wan22.py` (`Wan22Generator(Generator)`):
   - presets and frame math (73/121 frames, 24 fps, 1280x704, 30/50 steps), defaults (guidance 5.0, flow_shift 5.0, default negative prompt, user one replaces it);
   - abort between steps frees the GPU (same traceback-drop + `maybe_free_model_hooks` + `empty_cache` order as `wan.py`);
   - no `image` input: text pipeline; with `image`: image-to-video pipeline built from the same loaded components (no second copy of the weights), `expand_timesteps=True`;
   - crop/resize helper (landscape, portrait, square inputs give exactly 1280x704); tiny or undecodable image raises `PermanentJobError` `invalid_input`, field `image`;
   - logs settings only, never the prompt.
   - dtypes: transformer and text encoder bf16 (A10G and L4 have bf16), VAE fp32. `enable_model_cpu_offload`. Pin `Pillow` exactly in `worker/requirements.txt`.
2. Test then change `worker/__main__.py`: registry `{manifest model name: generator class}`; unknown name fails at start with a clear error.
3. Manifest: `model_manifest.json` becomes `wan2-2-ti2v-5b` (prompt required, 500 chars; `image` slot optional; params `seed`, `quality`, `duration_seconds`, `negative_prompt` as today). Old file kept as `model_manifest.wan2-1.json`. `lambdas/api/manifest.py` tests and the mock-manifest tests must stay green. Check `terraform/ecs.tf:59` reads the right file.
4. Terraform (offline checks only): `terraform/compute.tf` overrides become `g5.2xlarge` only, comment updated; check `ecs.tf` task cpu 7168 / memory 29696 still fits a g5.2xlarge (8 vCPU, 32 GB). `terraform fmt -check && terraform validate`.
5. Docs: ADR 0005; spec section 5 (model bullet, manifest bullet, "Open" fp16-copy item, NVMe cap wording); README model tables and the "g4dn" lines (27, 44, 61, 71-74); CLAUDE.md "Repo state" and model line; plan 04 one-line note (0005 taken, Wan 1.3B no longer parked-with-weights); plan 05 status line; `_plans/01_build_order.md` if it lists the model; effort log; `docs/huggingFace_to_s3_instructions.md` (new repo, commit, prefix `models/wan2-2-ti2v-5b/`); control-room artifact (once an hour, batch). No new `CONTEXT.md` term: "Model shape: text+image" already exists.

### B. Owner steps (Claude states cost first; every command message starts with the `AWS_PROFILE=terraform-deployer` reminder and the account `477656043770` check)
1. Copy 5B weights HF -> `s3://<models bucket>/models/wan2-2-ti2v-5b/` with the runbook (34.2 GB, pinned commit). Temp free-eligible EC2, small cost. **Done 2026-11-22:** 21 objects, 34,201,420,260 bytes, local and bucket byte counts identical; temporary instance, 60 GB disk, key, security group and role all deleted and checked; both `models/` prefixes now in the bucket (the 1.3B one stays until the trial is accepted). Runbook fix found on the way: the launch command still said 40 GB. A pasted `aws` output opened the pager and garbled a typed command, so `export AWS_PAGER=""` is worth setting first.
2. `scripts/build_push.sh` (about 21 min, needs 15-25 GB free disk). **Done 2026-11-22:** image pushed as `:latest` (4 new layers; the heavy ones already existed). A local CPU check inside the pushed image (no weights, no GPU, no AWS) confirmed with the real libraries (Pillow 12.3.0, diffusers 0.35.2, torch 2.8.0+cu128): the image pipeline's constructor takes every part the generator passes, its call takes every keyword, `enable_tiling` exists, `fit_image` gives exactly 1280x704, and the registry lists both models. That rules out signature mistakes; it does not show that the real 5B pipelines run.
3. `terraform plan`, review, `apply` (manifest env var, g5-only ASG, launch-template version). **Done 2026-11-22:** plan reviewed (1 add, 3 change, 1 destroy: ASG, ECS service and API Lambda in place, task definition replaced; plan text saved as `docs/terraform_plans/2026-11-22_wan22_5b_plan.txt`), applied by the owner. The site now serves the 5B manifest; the limits stay at 0 until trial 1.
   - Added before the trial: the worker logs `model loaded, peak RAM so far N GiB` after load and `peak memory: GPU N GiB, RAM N GiB` after each generation (357 tests). Without them, peak VRAM and RAM could not be read from the instance (no SSH). Needs one more `scripts/build_push.sh`.
4. Trial 1 on g5 (raise limits to 1 by hand, `docs/runbook_stop_gpu.md` to stop): (a) T2V `fast` 3 s, (b) T2V `high` 5 s, (c) I2V with a real upload from the site. Record cold start (now 34 GB copy), seconds per step, peak VRAM (the VAE decode at 1280x704x121 is the OOM risk; offload does not cover it, so `vae.enable_tiling()` may be needed), `gpu_seconds`, cost, and the owner's verdict on the clips. My estimate for the whole trial: about $0.5 to $1 of credit at g5 Spot $0.71-0.87/h; the first run decides the real numbers.
5. If accepted: delete the 1.3B weights from S3 (by hand), then a Terraform change adding `g6.2xlarge`, one confirmation run on it.

## Risks
- **Image-to-video path is the biggest unknown.** The checkpoint's `model_index.json` names `WanPipeline`; image input goes through `WanImageToVideoPipeline`. Wiring it from shared components is read from the 0.35.2 source, not run. Fakes cannot prove it; trial 1(c) does.
- **VRAM peak at 1280x704x121** (VAE decode) is unmeasured; 24 GB may not hold. Tiling is not an option in diffusers 0.35.2 (see trial 1 findings); Wan's README says 24 GB is enough for 720p without it.
- **Speed and cost per clip unmeasured** on an A10G. 5 s clips at 50 steps may take 10 to 20 min (my estimate; README gives under 9 min on a 4090). A Spot interruption restarts a job from zero, so longer jobs waste more credit.
- **g5-only fleet** has no Spot diversity until g6 is added; if g5 capacity is short, jobs wait.
- **Quality is not guaranteed.** Newer and bigger than 1.3B, but only the owner's eyes decide. Plan 05's rule stands: iterate settings on a clip, not a spec.
- **Destructive step** (1.3B weights delete) is deliberately last and the owner's.
- Upload path on real S3 untested (plan 04 step F); check size/type enforcement and that inputs are deleted after the job.

## Phase A result (2026-11-22)

Done, test first, one commit per step: Pillow 12.3.0 pinned (worker and dev requirements; 13.0.0 is a new major and diffusers 0.35.2 was not checked against it); `worker/wan22.py` with 41 tests; the generator registry in `worker/__main__.py` (4 tests; an unknown model name stops the worker at start); the live manifest is `wan2-2-ti2v-5b` with an optional `image` slot and the old file is kept as `model_manifest.wan2-1.json`; `compute.tf` is g5-only; ADR 0005; spec, README, CLAUDE.md, plans 04 and 05, and the weights runbook updated. The suite went from 307 to 354 tests.

Findings and deviations from what the plan said, from reading the diffusers 0.35.2 source and the Hugging Face file list:
- `pipe.components` of `WanPipeline` leaves the transformer out, so the image pipeline is built from named parts (`tokenizer`, `text_encoder`, `vae`, `scheduler`, `transformer`, `transformer_2`) with `expand_timesteps=True`.
- The image pipeline's own `video_processor.preprocess` stretches the image to the target size, which is why our crop happens first. The 5B transformer has `image_dim` None, so no CLIP image encoder is needed.
- Two pipelines on shared modules cannot both keep CPU-offload hooks (the second `enable_model_cpu_offload` replaces the first's). The generator moves the hooks only when the kind of job changes. Tested with fakes; real behaviour is trial 1(c).
- **VAE tiling stays off.** My phase A deviation (tiling on by default as insurance) was wrong: see the trial 1 findings below. It cost a failed first attempt.
- Weights in RAM with CPU offload: about 24 GB (my arithmetic from the repo's file sizes: transformer stored fp32, about 20 GB, so about 10 GB in bf16; text encoder 11.4 GB; VAE 2.8 GB). The task's memory limit is 29 GiB, so this is tight. Unmeasured; trial 1 shows it. If it does not fit, the fix is a Terraform change to the task's memory or a lighter text-encoder load, not a guess now.
- The weights runbook's temporary EBS volume went from 40 GB to 60 GB (the 5B is 34.2 GB), and its variables now point at the 5B repo and commit.

## Verification
- `.venv/bin/pytest -q` green after each step (354 tests after phase A).
- `terraform fmt -check && terraform validate` in `terraform/`.
- Manifest validates in `lambdas/api/manifest.py` tests; the mock UI still runs with `config.json` `"mock": true`.
- Trial 1: new log lines appear (`received job`, `generating`, `step N/M`, `uploaded`), clip passes the owner's bar, `gpu_seconds` logged, then the stop runbook and a `describe-instances` check that nothing is running.

## Not doing now
No push, no `terraform apply`, no image build, no AWS call, no edits to the models bucket by Claude. No 480p tier, no g6 until trial 1 is accepted, no avatar work.

## Trial 1 findings (2026-11-22, g5.2xlarge Spot, first job: text `fast` 3 s)

Cold start with both models in S3 (63 GB copy): ASG launch about 12:13:50 UTC to `worker ready` 12:23:00, about 9 min; model load 24 s. `model loaded, peak RAM so far 27.5 GiB` against the container's 29 GiB hard limit: it survived, margin about 1.5 GiB (a loading transient; the generation's own peak was never reached). Denoising: 12.4 s per step at first, then 11.6 s (30 steps in about 6 min), faster than my estimate.

**Attempt 1 failed after the denoising, in the VAE decode:** `RuntimeError: The size of tensor a (2) must match the size of tensor b (4) at non-singleton dimension 2`, `gpu_seconds` 405, outcome `error`. Root cause, read in the diffusers 0.35.2 source: `AutoencoderKLWan.tiled_decode` calls `self.decoder(...)` for every frame without `first_chunk=True`, while the plain `_decode` passes it for the first frame; the Wan 2.2 VAE's `DupUp3D` shortcut needs it. The tiling was turned on by my phase A deviation, so the failure was mine. Fix: tiling off (test `test_load_keeps_vae_tiling_off_...`), image rebuild.

SQS handed the message straight back, so attempt 2 started at once and was heading for the same failure. The owner ran the stop commands (service autoscaling max 0, service desired 0, ASG 0): that also releases the message, so the same job stays in the queue for the retry (received twice so far; the DLQ is at 8). About 22 minutes of g5 were billed, about $0.30 (my estimate).

**Open design point, for the owner:** a deterministic error costs every one of the 8 attempts. With a 6 to 20 minute job that is up to about an hour of GPU (about $0.8) for one doomed job. Options: lower `maxReceiveCount` for generator errors, or have the worker write the `.failed` marker and delete the message after an error that repeats. Not done; needs a decision.
