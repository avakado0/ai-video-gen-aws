# 02: GPU worker (code, tests, Dockerfile)

Status: built 2026-11-18 (code, tests, Dockerfile). Nothing run on a GPU, image not built, `ecs.tf` change not applied.

## Context
The front half works (API deployed, jobs queue as pending). Nothing reads the queue. The worker takes the SQS message, checks the job is still wanted, runs the model, uploads the clip, and reports failure or cancel correctly. Spec: section 4 and 3D/3E; the cancel re-checks were decided 2026-11-18.

Owner's decisions (2026-11-18):
- Write a real `WanGenerator`, marked unverified until the Kaggle spike.
- Write the Dockerfile and `scripts/build_push.sh` now, build later (disk is short).
- Move the shared rules to `lambdas/common/`.

TDD throughout. Claude does not run the real GPU, `docker build`, Terraform apply or any AWS call. `gpu_max` stays 0, so nothing can start a GPU after this lands.

## Layout
```
lambdas/common/{__init__,manifest,rules}.py   moved from lambdas/api/ (shared with the worker)
worker/                                        repo root, not a Lambda; run as `python -m worker`
  config.py       Config.from_env (QUEUE_URL, CLIPS_BUCKET, MODELS_DIR, MODEL_MANIFEST, intervals, steps)
  errors.py       Aborted, Cancelled, PermanentJobError(code, field, message).to_json(), BadMessage
  job.py          Job.from_message(body, receive_count): parse, UUID via common.rules, params via normalize_params
  jobqueue.py     receive / delete / change_visibility (thin SQS wrapper)
  store.py        marker_exists (HEAD; only 404 = gone, other errors raise), put_failed, upload_clip, fetch_inputs, delete_inputs
  watcher.py      JobWatcher thread: Spot poll every few s; every 60 s heartbeat (visibility 180) + marker HEAD; exposes should_abort()
  imds.py         IMDSv2: spot_notice(), instance_type()
  protection.py   ECS task scale-in protection (protect/release) via task metadata
  generator.py    Generator interface: generate(job, input_paths, should_abort) -> Path
  wan.py          WanGenerator (lazy torch/diffusers imports), UNVERIFIED until the spike
  runner.py       Worker.handle(message) per spec section 4, Worker.run_forever()
  __main__.py     wiring; SIGTERM sets `stopping`
  Dockerfile, requirements.txt
scripts/build_push.sh                           ECR login, buildx linux/amd64, push :latest (user runs it)
tests/worker/                                   moto S3+SQS; fake generator, spot notice, protection, clock
```
`pytest.ini`: `pythonpath = lambdas .` so tests import `common` and `worker`.

## Order (one commit per step, tests first)
1. **Refactor.** `git mv` manifest.py and rules.py into `lambdas/common/`; update API and test imports; `build_api.sh` copies `lambdas/common` to `build/api/common`. The 170 tests stay green; re-smoke the bundle in a clean venv.
2. **errors, config, job.** Message parsing and re-check against the manifest. An unparseable body is a BadMessage.
3. **jobqueue and store.**
4. **watcher.** Small intervals injected in tests.
5. **runner (the core).** Each test starts red:
   - happy path: clip at `clips/{sub}/{job_id}.mp4`, message deleted, inputs deleted, protection on then off, usage line `{event: job_attempt, sub, job_id, attempt, instance_type, gpu_seconds, outcome}`
   - stale: marker 404 before work: message deleted, generator never called, outcome `stale`
   - marker HEAD 403 or other error: released, not deleted (never silently drop)
   - cancel mid-run (marker deleted while generating): abort, delete message and inputs, no upload, outcome `cancelled`
   - cancel just before upload: no orphan clip
   - Spot notice mid-run and SIGTERM: `ChangeMessageVisibility(0)`, no `.failed`, outcome `released`
   - PermanentJobError: `.failed` JSON written, message deleted
   - unexpected exception: released for retry, no marker
   - bad message body: logged and deleted
   - heartbeat extends visibility while running
6. **wan.py.** 3 s = 49 frames, 5 s = 81, 832x480, 16 fps, fp16 transformer and text encoder, VAE fp32, `enable_model_cpu_offload`, step callback raises Aborted, unset seed = random, steps from config (default 30). Only the pure mapping (duration to frames, seed handling) is unit tested.
7. **imds.py, protection.py, __main__.py.** Small and mostly thin; tested with stubs where there is logic.
8. **ecs.tf.** Add `MODEL_MANIFEST` to the task environment (same `jsonencode(jsondecode(file(...)))` as api.tf). `fmt` and `validate` only. The task role already covers SQS, S3 put on `clips/*`, `.pending` HEAD plus ListBucket, input read/delete and task protection.
9. **Dockerfile, requirements.txt, build_push.sh.** Versions looked up on PyPI; diffusers pinned at or below 0.35 (spec's NaN note). Not built; marked untested.
10. **Docs.** Spec module notes, ticks in `_plans/01_build_order.md`, effort log, CLAUDE.md repo state and test command. Artifact in the next hourly batch.

## Not verifiable by Claude
Real model output and speed (the Kaggle spike runs the same `WanGenerator`), docker build and image size, IMDS and ECS metadata calls on a real instance, task protection, S3 403 behaviour, cold start. All stay on the spec's "unverified until first GPU boot" list.

## Verification
- `.venv/bin/pytest` (170 existing plus the worker suite)
- `terraform fmt -check && terraform validate` in `terraform/`
- `./scripts/build_api.sh` plus the clean-venv smoke test after the refactor
- `python -m py_compile` on the worker package
- import `worker.runner` with no torch installed (proves lazy imports)

## Owner follow-ups
- Free about 25 GB of disk (image build).
- Create a Kaggle account for the spike.
- Run `scripts/build_push.sh` with `AWS_PROFILE=terraform-deployer` (profile reminder and account check first).
- Deploying the `ecs.tf` change is one `terraform plan` / `apply` by the owner.

## What changed while building (2026-11-18)
- `usage.py` also moved to `lambdas/common/`, so the API and the worker share one log helper.
- No `Cancelled` exception: the watcher's `reason` ("cancelled" or "interrupted") decides, and the runner acts after the watcher thread has stopped (a race found by a test: a late heartbeat re-hid a released message).
- `gpu_seconds` is receive-to-finish wall-clock and the success outcome is `ok`, as spec 3H says. Other outcomes were added: `cancelled`, `stale`, `error`.
- `ecs.tf` also gets `AWS_DEFAULT_REGION` (boto3 needs one in the container).
- Image: `python:3.12-slim` plus the PyPI `torch` wheel instead of a CUDA base image. Requirements pinned from PyPI metadata (newest release at least 30 days old for fast-moving packages).
- Test count: 257 (170 before).

## Kaggle spike (2026-11-20)
Findings: `docs/wan_spike_findings.md`. `WanGenerator` runs on a T4. Bug found and fixed (`b539a62`): an aborted run left the model on the GPU (2.7 GiB) and the next run ran out of memory. The generator now drops the traceback, calls `maybe_free_model_hooks()`, `gc.collect()` and `torch.cuda.empty_cache()` on abort; confirmed on a real GPU. Open: defaults (resolution, steps, durations).
