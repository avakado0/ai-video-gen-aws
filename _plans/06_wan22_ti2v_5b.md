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
   - dtypes: transformer and text encoder bf16 (A10G and L4 have bf16), VAE fp32 (changed to bf16 after trial 1, see below). `enable_model_cpu_offload`. Pin `Pillow` exactly in `worker/requirements.txt`.
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

**Done, owner said "yes cap":** a deterministic error used to cost every one of the 8 attempts (with a 6 to 20 minute job up to about an hour of GPU, about $0.8). The runner now gives up on an unexpected error from the third receive on (`gave_up`, `.failed` internal marker, message deleted; Spot releases count as receives; if the marker cannot be written the message goes back). Tested (362 tests), spec section 4 updated. The job in the queue has been received 3 times, so its next error would be its last.

**Second finding: the stop commands do not stop a running job.** The worker protects the task from scale-in while a job runs (`protection.py`, up to 120 minutes), and ECS ignores a lowered desired count for a protected task. After the owner's three stop commands, attempt 2 ran on to the same decode failure (about 12:35:45, another 6 minutes, about $0.08), then the worker released the protection, took the message again as attempt 3 (it could not re-protect: `DEPLOYMENT_BLOCKED`) and was released after 23.6 s when ECS stopped the task. So the job was received three times, not two. ADR 0004, the cut-off Lambda's docstring and the stop runbook claimed a running job gets SIGTERM and were corrected; the runbook now says to use `aws ecs stop-task` for a running job, untested.

## CPU check with the real diffusers classes (2026-11-22, `scripts/cpu_check_wan22.py`)

Free, about a minute, tiny random weights but the real `AutoencoderKLWan` (config of the 5B, 0.70 B parameters), the real pipelines and the real `Wan22Generator`, at 128x96. Results:
- `Wan22Generator.load()` runs; the image pipeline is built from the same loaded parts (`transformer` is the same object, `expand_timesteps` True).
- Text to video works for 3 s (9 frames at this size) and 5 s (13 frames); image to video works with a real PNG through `fit_image`; a text job after an image job works.
- **The trial 1 failure reproduces exactly** with tiling on (`The size of tensor a (2) must match the size of tensor b (4) at non-singleton dimension 2`, from `tiled_decode`), and the same decode works with tiling off. Root cause confirmed by reproduction, not only by reading the source.
- A detail that cost me one false "not reproduced": the VAE's own spatial compression is 8 (the other factor of 2 is patchify), so a tile of 64 pixels is an 8-cell latent tile; the tiny latent did not exceed it and tiling was never triggered. With 32 pixels it triggers.
- It says nothing about GPU memory, speed or quality: those stay for trial 1.

## Trial 1, second run (2026-11-22, g5.2xlarge Spot, 13:09 to about 13:33 UTC)

The retry ran with the tiling fix and the cap: cold start about 9 min again (instance launched 13:10:20, worker ready about 13:19:30), the same job received for the 4th time, 30 steps in 5 min 56 s (11.6 s per step, steady), 414 GPU seconds, then **the decode ran out of GPU memory**: `torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.60 GiB. GPU 0 has a total capacity of 22.06 GiB of which 1.45 GiB is free ... 18.28 GiB is allocated by PyTorch, and 1.98 GiB is reserved by PyTorch but unallocated`. The cap did its job: outcome `gave_up` after that single attempt, a `.failed` marker, no retry loop. The owner had meanwhile submitted a second job (`high`, 50 steps); it started at once and would have failed identically (the decode does not depend on the step count), so it was cancelled from the site while running: the first cancel of a running job on AWS (see below).

Cause, read from the decoder's structure: the Wan 2.2 VAE decoder (`decoder_base_dim` 256, `dim_mult` [1,2,4,4]) runs one latent frame at a time, but its last up block holds 512-channel tensors at 352x640 for 4 frames, its conv input is padded with 2 cached frames (6 frames: 512 x 6 x 352 x 640 x 4 bytes = 2.6 GiB, the failed allocation) and every layer keeps a 2-frame cache. All fp32. The A10G has 22.06 GiB usable, 1.5 GiB less than the 24 GiB of the card Wan's README quotes. The fix is the decoder's precision: bf16 halves the activations and the caches (my estimate: the decode peaks near 11 to 13 GiB instead of above 22; unmeasured). `Wan22Generator` now loads the VAE in bf16 by default (`vae_dtype` overrides it; both pipelines cast their latents to the VAE's dtype, checked in the diffusers source), and the Dockerfile sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` against the 1.98 GiB of reserved-but-unallocated fragmentation. Risk to watch: bf16 can show in the picture (banding, slightly different colour); the owner's eyes decide.

CPU check (`scripts/cpu_check_wan22.py`) cannot test bf16 (CPU bf16 convolutions take minutes per job); it records that `load()` asks for bf16 and tests the rest in fp32.

## Trial 1, third finding: the container was OOM-killed (2026-11-22, 13:35:08 UTC)

After the first job gave up, the owner's second job (`high`) was running in the same process. At step 40 of 50 the container vanished from the log with no outcome line, ECS started a new task, and the same message came back as attempt 2 and was dropped as `stale` (the owner's cancel had removed its marker): the cancel worked at the data level. `describe-tasks` for the vanished task: `EssentialContainerExited`, **exit code 137, `OutOfMemoryError: Container killed due to memory usage`**. So host RAM, not the cancel path. The load peak had been 27.5 GiB (first process) and 28.5 GiB (the replacement) against the 29 GiB limit; with the transformer (about 10 GB in bf16), the VAE and the 11.4 GB prompt encoder all resident, plus the CUDA libraries, the steady state sat on the limit and a second job in the same process pushed it over.

Fix, test first (373 tests): `text_encoder=None` when loading, so the 11.4 GB is never held; each job loads the UMT5 encoder straight to the GPU (`device_map`, bf16, the GPU is free then because the transformer is still on the CPU), encodes the prompt and the negative prompt, and drops the encoder before denoising; the pipelines get `prompt_embeds` and `negative_prompt_embeds`. Checked against the real diffusers 0.35.2 and transformers 4.57.6 on a CPU with tiny weights saved as a model folder (`scripts/cpu_check_wan22.py` now runs the real `load()` and the real on-demand load): the pipeline builds with `text_encoder=None`, the encoder loads and encodes, the pipeline runs from embeddings, the encoder is gone after each job. Expected effect (my estimate, unmeasured): about 11 GB less RAM during denoising, and no 11 GB host-side copy at load. Costs about 6 to 10 seconds per job to load the encoder from the NVMe.

Also confirmed in the same run: plan 07 works again. Last activity 13:36:57 (the `stale` drop), worker stopped 13:41:57 (5.0 min), ASG desired 0 within a minute, instance gone from the ASG at 13:48.

Trial 1 so far: about 1 h 20 min of g5 across two runs, about $0.85 (my estimate). No clip has been produced yet.

## Trial 1, first clip (2026-11-22, g5.2xlarge Spot, third run)

**A clip came out:** text, `fast`, 3 s, 1280x704, 73 frames. The owner saw it on the site (status Ready, download link); the quality verdict is the owner's.

How the third run went. The owner had submitted a job while the final image was still being built and pushed, so the task started (14:42 UTC) on the previous image, digest `265f5a7a`. **I told the owner that image already had the bf16 VAE. That was wrong:** the bf16 change was committed after that push and is first in the latest push, `12651cc0`. The identical OOM numbers (2.60 GiB requested, 18.29 GiB allocated, as in the fp32 run) showed it. Lesson: before saying what a running image contains, compare its digest and push time with the commits, or ask `describe-tasks` for the digest first.

Attempt 1 on the old image failed at the decode (413.5 GPU s); attempt 2 was at step 15 when the owner stopped the task with `aws ecs stop-task`. **That worked on a protected running task:** the call was accepted (`desiredStatus STOPPED`), the worker got SIGTERM, logged `outcome: released` after 279.7 s and the message went back; the service started a replacement task on the same instance (weights already on the NVMe, no new cold start), which pulled `:latest` = `12651cc0` and took the same job as attempt 3.

**Measured on `12651cc0` (attempt 3):**
- `model loaded, peak RAM so far` **24.2 GiB** (was 27.5 and 28.5; limit 29).
- `peak memory: GPU 12.6 GiB, RAM 24.4 GiB` after the job: the bf16 decode has about 9 GiB of GPU headroom on the A10G's 22.06 GiB (my estimate was 11 to 13 GiB), and RAM has 4.6 GiB of margin.
- 420.5 GPU seconds for `fast` 3 s: 30 steps at about 11.6 s (5 min 56 s), decode and export about a minute. Upload 3 s after the peak line.
- Cost of that clip's compute: about $0.08 to $0.10 (g5 Spot $0.69 to $0.90 an hour; my arithmetic).
- Plan 07 held for the third time: after the job the idle alarm, the service and the instance went down without a hand stop (service 0/0 and `shutting-down` at 15:12 UTC).

**Trial 1 cost so far:** about 1 h 45 min of g5 over three runs, about $1.3 to $1.5 (my estimate), one clip.

**Still to do in trial 1:** (c) image to video on a real GPU with a real upload from the site (first real use of `POST /uploads` on real S3); (b) a 5 s clip (the longest case: 121 frames, about 27k tokens, my estimate about 2x the time per step and a higher GPU peak, unmeasured); a second job in the same process (the case that got the container OOM-killed before the encoder change).

## Trial 1 result (2026-11-22, g5.2xlarge Spot, fourth run): accepted

Two jobs in one worker process, both `fast`: a 5 s text clip, then a 3 s image-to-video clip from a real photo uploaded on the site (the first real `POST /uploads` on real S3; it worked). Both succeeded on attempt 1, so the case that got the container OOM-killed before the per-job prompt encoder (a second job in the same process) is fixed.

| | 5 s text (121 frames) | 3 s image to video (73 frames) |
|---|---|---|
| seconds per step | 22.3 | 11.6 |
| GPU seconds (`gpu_seconds` in the log) | 763.7 | 389.6 |
| peak GPU memory | 14.6 GiB | 13.9 GiB |
| peak RAM | 28.1 GiB | 28.1 GiB |

- **Time per clip:** 5 s costs about 2x a 3 s clip, as estimated. At `high` (50 steps) a 5 s clip is about 18.6 min of GPU, about $0.25 of Spot compute (my estimate at about $0.8 an hour).
- **RAM is the tightest limit.** Peak 27.1 GiB right after loading the model (it was 24.2 GiB in the earlier run; not explained) and 28.1 GiB after the first job, against the 29 GiB container limit. It passed with about 1 GiB to spare; `g6.2xlarge` has the same 32 GiB. Open: find out what makes the load peak, and lower it before adding load.
- **Plan 07 held again:** the worker stopped 5.7 min after the last upload.
- **Image to video:** the person, hard hat, logo and background stayed the same from the first frame to the last, and the start image's idea followed the prompt. In the six frames I checked, the object that enters the scene changed colour partway; the 512 px minimum and the 16:9 centre crop mean a square photo loses about 45% of its height (the top and bottom), which can clip a chin or a hat. A user-facing note on the crop is worth adding to the upload form.
- **Cost of this run:** about $0.45 (my estimate). Trial 1 in total: about $1.8 to $2 over four runs (my estimate); the Billing console has the real number.
- **Owner's verdict:** the clips are okay. Decision: the 1.3B weights are deleted from S3 by the owner; `g6.2xlarge` is added next, with one confirmation run.
- **1.3B weights deleted from S3 by the owner, 2026-11-22.** Checked afterwards: `models/` holds only `wan2-2-ti2v-5b/`, 21 objects, 34,201,420,260 bytes, the same as the verified copy. Every boot now copies 34 GB instead of 63 GB. Next: add `g6.2xlarge` (Terraform), one confirmation run.

## Trial 1, extra jobs (2026-11-22, owner's own jobs on the same g5 instance)

- **`high` 5 s text clip (50 steps): 1209.6 GPU seconds** (about 20 min), peak GPU 14.6 GiB, RAM 28.1 GiB, same peaks as `fast`. About $0.27 of Spot compute (my estimate at about $0.8 an hour). My earlier figure of 18.6 min was a little low.
- **`stale` outcome seen:** a job the owner cancelled on the site before the worker took it: the worker found the `.pending` marker gone, deleted the message and used 0.0 GPU seconds (the spec's behaviour).
- **`failed` outcome seen:** a job whose start image had a shorter side under 512 px. The worker rejected it in 0.4 s with `invalid_input`, field `image`, and the site showed "The starting image is too small: its shorter side must be at least 512 pixels." The check worked, but it only happens after the job reaches the worker: on a cold start that is about 9 min of waiting and boot cost for a refusal. **Done 2026-11-22:** the manifest's image slot has `min_side` (512; a test pins it to the worker's `MIN_IMAGE_SIDE`) and the form reads the image size and refuses a small one before the upload (`frontend/js/imagesize.js`, tests in `tests/frontend/`). The worker check stays. Needs `terraform apply` to reach the site (front-end files and the manifest env var). Tried in Chrome against the mock page (local static server, `?manifest=text-image`): 300x300, 800x300 and 300x900 refused with the message; 512x512, 640x640 and 1920x1080 accepted; a file that is not an image refused with "could not read this image file"; the hint reads "at least 512 px on the shorter side". The real site needs the `terraform apply` first.

## g6.2xlarge held back (2026-11-22, owner's decision)

The plan was to add `g6.2xlarge` after trial 1 for Spot capacity diversity. The Spot prices the owner pulled (us-west-2, 2026-11-22) showed it is no cheaper in the two zones the fleet has subnets in: a/b g5 about $0.85 and $0.68, g6 about $0.90 and $0.74 (the g6 price of about $0.52 was in zone d, where there is no subnet). The L4 has about half the A10G's memory bandwidth, so a step is probably 1.5x to 2x slower (my estimate, unmeasured), which would make a clip cost roughly 1.6x to 2.2x more. The ASG chooses by price and capacity, not speed, so it could pick g6 even when g5 is free. g5 had capacity in every run, so the fleet stays g5-only. The change was committed (`7f4593d`), never applied, and reverted before the apply. Revisit if jobs wait for g5 capacity: add the override and run one job pinned to g6 to measure the real speed.
