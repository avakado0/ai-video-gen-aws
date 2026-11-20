# Build order

Canonical build plan, written 2026-11-16 from the grilling session. Items are in dependency order. Owner: **H** = human (Claude must not do it), **C** = Claude. Tick a box only when its gate passes.

Rules that apply to every phase:

- TDD for application code: failing test first (pytest, `TestClient`, moto; fake SQS/S3 for the worker). Terraform's gate is `terraform fmt -check`, `terraform validate`, and a reviewed `terraform plan`.
- Deploy everything with `gpu_max = 0` until phase 7.
- Nothing here upgrades the plan, creates an Organization, or launches GPU or large instances (see CLAUDE.md banned actions).
- One logical change per commit, added by path.

Target: GPU go-live in November 2026 (owner's choice). Expect it to slip: the upgrade stays after the stack is built (decision 2026-11-16), and the Spot quota wait starts after it.

## Phase 0: Human setup (Free plan safe)

Steps are in `human_todo/aws_setup.md`.

- [x] H. Create the $20 gross-spend budget in the console, Credits unchecked, 80% email, 100% email. Gate: Credits box verified in the console. Done 2026-11-16: the console has no Credits checkbox now, so the budget uses a Charge type Excludes Credit, Refund filter; email verified.
- [x] H. Create IAM user `terraform-deployer` (no console, AdministratorAccess, one key) with `aws configure --profile terraform-deployer`. Fix or delete the invalid local CLI profile. Gate: `aws sts get-caller-identity --profile terraform-deployer` works. Done 2026-11-16, with an inline Deny guard policy (Organizations, Control Tower, Identity Center, Marketplace, CloseAccount).
- [x] H. Keep `the-admin-user` console-only with MFA and no key.
- [x] H. Create the Terraform state bucket in CloudShell and run the four verify commands. Check `terraform version` is 1.10 or newer. Gate: `terraform init` succeeds with the existing `backend.tf`. Progress 2026-11-17: bucket `aivideogen-tfstate-477656043770` created and verified, Terraform v1.15.8. `terraform init -reconfigure` succeeded with the S3 backend (gate met).
- [ ] H. Verify the Free plan end date in the console and write it in `human_todo/aws_setup.md`.
- [ ] H. Free 25 GB of disk. Make a Kaggle account with phone verification.

## Phase 1: Free Terraform, validate only

Order follows resource dependencies. Gate for every file: `terraform fmt -check` and `terraform validate`; a reviewed `plan` once the profile works. No apply until phase 3.

- [ ] C. `vpc.tf`: VPC, two public subnets, Internet Gateway, route table, S3 Gateway Endpoint (ADR 0002).
- [ ] C. `storage.tf`: clips bucket, 1-day lifecycle on `clips/` and `inputs/`, read-only `data` lookup of the models bucket. Written, validate passes. The clips-bucket CORS resource is in `cloudfront.tf`, because its origin is the distribution domain.
- [ ] C. `sqs.tf`: queue (1 day since 2026-11-17, was 4 days; 3 min visibility, `maxReceiveCount = 8`), DLQ, DLQ alarm, SNS topic and email subscription. Written, validate passes. Needs `alert_email` in a git-ignored `.tfvars` before `plan`.
- [ ] C. `ecr.tf`: repository. Written, validate passes. Keeps the latest 3 images; tags are mutable, so `build_push.sh` may reuse a tag.
- [ ] C. `iam.tf`: instance profile, task role, execution role, API role (with `s3:ListBucket`), failure Lambda role. Written, validate passes. The budget Lambda role lives in `budget.tf` (spec 3G).
- [ ] C. `failure.tf`: DLQ-triggered marker Lambda (test the handler first, TDD). Written: 22 tests pass (`.venv/bin/pytest`), validate passes, zip checked in a scratch copy.
- [ ] C. `budget.tf`: SNS topic with a `budgets.amazonaws.com` publish policy, cut-off Lambda, subscription (ADR 0004). Test the handler first. Written: handler tests and validate pass. Amended 2026-11-17: also zeroes the ECS service and its autoscaling max (14 handler tests), because zeroing the ASG alone cannot stop a protected instance. The budget's own 100% alert still has to be pointed at this topic by hand after the first apply.
- [ ] C. `cloudfront.tf`: web bucket, distribution, then bucket policy as a separate resource. Written, validate passes. The clips-bucket CORS resource (browser `POST`/`GET`/`HEAD` from the distribution origin) and the upload of `frontend/` (minus the mock `config.json`) are in this file too.
- [ ] C. `cognito.tf`: user pool (admin-create-only), public PKCE client, prefix domain, callback from the distribution's `domain_name`. Written, validate passes.
- [ ] C. `api.tf`: HTTP API, JWT authorizer, Lambda with no function URL, resource policy for API Gateway only, log group (90 days). Written with a placeholder handler (`lambdas/api/`, returns 501); validate passes. Still to do in phase 2: the real FastAPI app, a dependency-packaging method for the zip (`archive_file` cannot `pip install`), and the `model_manifest.json` env var.
- [ ] C. `ecs.tf`, `compute.tf`, `scaling.tf`: cluster, capacity provider, task definition, launch template, ASG (`gpu_max`, `ignore_changes` on `max_size`), scaling alarms and policies, worker log group, saved Logs Insights query. Written (service in `ecs.tf`), validate passes. Untested until the first GPU boot: `aws` CLI on the AMI, agent starts after `user_data`, Spot service-linked role (if the first launch fails on it, run `aws iam create-service-linked-role --aws-service-name spot.amazonaws.com`).
- [ ] C. `outputs.tf`, and `config.json` upload last (generated in memory, never to disk). Written, validate passes. All 17 Terraform files now exist; none has been planned or applied.

## Phase 2: Application code (TDD, in parallel with phase 1)

- [x] C. Write `model_manifest.json` for v1: prompt required, no slots, `seed`, `duration_seconds` (3 or 5), `negative_prompt` (max 200). Gate: manifest validator test. Done 2026-11-18: `model_manifest.json`, `lambdas/api/manifest.py`, `tests/manifest/` (74 tests in the suite). Not yet passed to the Lambda or ECS task as an env var (that is `api.tf`/`ecs.tf` work with the API).
- [x] C. API (FastAPI + Mangum, OOP, modular). Tests pin the 3-active-job cap as spec 3B states it: only `.pending` markers with no `.mp4` and no `.failed` count, so ready-but-undownloaded and failed jobs never block a user: `GET /config`, `POST /uploads`, `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `DELETE /jobs/{id}`. Tests assert the pre-signed POST policy conditions (moto does not enforce `content-length-range`), the 3-active-job cap, UUID validation, `sub` from claims only, and a no-claims request rejected. Done 2026-11-18 (code, not yet deployed): all six routes plus the claims check, 164 tests in the suite, run through the Lambda handler with HTTP API v2 events and moto. `api.tf` zips `build/api` and passes `MODEL_MANIFEST`. Deploy: `./scripts/build_api.sh`, plan, apply. Deployed 2026-11-18: the page loads the manifest and the job list, and Generate queues a pending job (seen in the browser). Found and fixed on the way: the blank seed box blocked Generate in the form (`form.js`, 303c68d).
- [x] C. API packaging (decided 2026-11-18): `scripts/build_api.sh` installs the pinned runtime dependencies (`requirements-api.txt`: FastAPI, Mangum; no boto3, the runtime has it) into git-ignored `build/api/` for Lambda's platform (`pip install --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all: --target`), copies `lambdas/api/` in, and `api.tf` zips that folder (`archive_file` cannot run pip). Deploy order: `./scripts/build_api.sh`, then `terraform plan -out=tfplan`; add the build step to the README "Deploying" section. Chosen over a Lambda layer (one Lambda only) and a container image (Docker and ECR for no gain). Revisit a hand-written router only if the script proves painful. Zip stays under the 50 MB limit (check after the first build). Done 2026-11-18: `scripts/build_api.sh` (Linux wheels, 11 MB unzipped, smoke-tested in a clean venv) and `requirements-api.txt` pinned.
- [x] C. Worker: poll, skip a job whose `.pending` marker is gone (delete the message, no GPU work), heartbeat, release on SIGTERM and Spot notice, `.failed` fast path, delete message after upload, usage log line per attempt. Tests with fake SQS/S3, no GPU. Also (decided 2026-11-18): re-HEAD the marker at every heartbeat and between diffusion steps, and once before the upload; marker gone = cancelled, so abort, delete the message and inputs, upload nothing, log `outcome: cancelled`. Done 2026-11-18 (code and tests, nothing run on a GPU): `worker/` package, plan in `_plans/02_gpu_worker.md`, 87 worker tests (257 in the suite) with moto and a fake generator. `ecs.tf` now passes `MODEL_MANIFEST` and `AWS_DEFAULT_REGION` (validate only, not applied). `WanGenerator` is written but unverified until the Kaggle spike.
- [x] C. Front end cancel and confirm (2026-11-18): Cancel button on pending jobs (`jobs.js`), confirmation dialog before submit (`confirm.js`), and a 30-second undo window in the API (`SUBMIT_DELAY_SECONDS`). Deployed and checked on the live site 2026-11-18 (apply 1 added, 5 changed).
- [ ] C. Front end: show elapsed time and the "up to 20 minutes after idle" note on pending jobs (`frontend/js/jobs.js`). Tests in mock mode.
- [ ] C. CI: GitHub Actions running pytest, `terraform fmt -check`, `terraform validate`, no AWS credentials.

## Phase 3: First deploy at `gpu_max = 0`

- [x] H. Review the `terraform plan` output. Gate: no NAT, ALB or interface endpoint, no GPU instance. First plan 2026-11-18: 95 to add, 0 change, 0 destroy; gate passes (S3 endpoint is Gateway, ASG min/max 0, no NAT/ALB/instance). The `-out` plan of 2026-11-18 matched (text in `terraform/plans/2026-11-18_first_apply.txt`).
- [x] H. `terraform apply` with the `terraform-deployer` profile. Done 2026-11-18: 95 added. Smoke test: web URL and `config.json` return 200, API returns 401 without a token (authorizer works).
- [x] H. Confirm the SNS email subscription. Attach the Terraform SNS topic to the budget's 100% alert. Done 2026-11-18 (100% alert has EMAIL and SNS subscribers).
- [ ] H. Create Cognito users in the console. Progress 2026-11-18: owner's user created and login works (after fixes: login redirect in `auth.js`, CloudFront invalidation, an unauthenticated `OPTIONS` route that targets the Lambda, for CORS preflight; applied 2026-11-18). Friends later.
- [x] C. Run the front end in real mode against the deployed API. Jobs stay queued; that is expected. Done 2026-11-18: login works, the page shows HTTP 501 from the placeholder API (expected until phase 2).

## Phase 4: Weights and image

- [x] H. Run the models runbook on an `m7i-flex.large`: create the models bucket (done early 2026-11-17, empty), copy weights, verify the byte count, tear down the temporary role, key, security group and instance. Expect a fix or two. Done 2026-11-20: 22 objects, 28,929,158,777 bytes, local and S3 match; download 3.5 min; temporary instance, volume, key, security group and role all deleted. Fix found: `hf download` needs one `--exclude` per pattern.
- [x] C. Write `scripts/build_push.sh`. Done 2026-11-18, with `worker/Dockerfile` and pinned `worker/requirements.txt`. The CUDA base image idea was dropped: `python:3.12-slim` plus the `torch` wheel (bundles CUDA 12.8), so there is no digest to pin. Recipe is checked by tests; the image itself is not built yet and its dependency set resolves only at build time.
- [x] H. Build and push the worker image. Done 2026-11-20: 4.16 GB compressed in ECR (verified with describe-images), build 19 min. Built, not yet run.

## Phase 5: GPU spike (before the first real job)

- [x] C. Write the Kaggle T4 notebook with a pass/fail table. Done 2026-11-18: `spike/wan_t4_quality_spike.ipynb`, generated by `scripts/make_spike_notebook.py` from the real `worker/wan.py`, `errors.py` and the pins in `worker/requirements.txt` (re-run the script after changing them). Checks: T4 and RAM, weights download, load, 3 s and 5 s clips (time, peak VRAM, black or flat frames, motion), 20 vs 30 steps, abort between steps, seeds. Ends with a REPORT cell to paste back. Run 2026-11-20. A second notebook, `spike/wan_t4_speed_spike.ipynb` (`scripts/make_speed_notebook.py`), times variants at 5 steps.
- [x] H. Run it. Done 2026-11-20 (two Kaggle T4 runs). Results in `docs/wan_spike_findings.md`: the model loads and gives clean frames at fp16 with an fp32 VAE; peak VRAM 12.7 GiB; 3 s at 832x480 takes 885 s (30 steps) or 632 s (20 steps), 5 s takes 2342 s; 640x368 halves the time per step (11.0 s against 23.7 s). Found and fixed a bug: an aborted run left 2.7 GiB on the GPU and the next run ran out of memory (confirmed fixed). Gate still open (owner): choose resolution, steps and whether to keep the 5 s option.
- [x] H. Set the generation defaults from the spike. Done 2026-11-20: `quality` param, `fast` (640x368, 20 steps, default) and `high` (832x480, 30 steps); `duration_seconds` stays 3 or 5. Plan `_plans/03_quality_option.md`.

## Phase 6: Upgrade and quota (the critical path)

- [ ] H. Manually upgrade to Paid. One-way. Gate: Credits page still shows `10069756528`, expiry 2027-10-29. Only after phases 3-5 (decision 2026-11-16).
- [x] H. Request the Spot quota "All G and VT Spot Instance Requests" in us-west-2: 8 vCPU (one `g4dn.2xlarge`, decision 2026-11-18). Requested 2026-11-20 before the upgrade, status Pending, no Support case yet; the applied value was 0.
- [ ] H. Wait for approval. Do not raise the ASG max size before it.

## Phase 7: GPU go-live

- [ ] H. Purge the job queue first: `aws sqs purge-queue --queue-url <queue_url output> --profile terraform-deployer`. Test jobs queued during phases 3 to 6 would otherwise run at go-live (the worker's marker check is the second line of defence).
- [ ] H. Tell Claude the cost, then raise the ASG limit by hand: `aws autoscaling update-auto-scaling-group --auto-scaling-group-name aivideogen-gpu --max-size 1 --profile terraform-deployer` (Terraform ignores `max_size`, ADR 0004), and the matching service limit: `aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/aivideogen/aivideogen-worker --min-capacity 0 --max-capacity 1 --profile terraform-deployer` (the deployed value is 2; `scaling.tf` now says 1 and ignores changes). Stay at 1 while the owner is the only user (decided 2026-11-18; the Spot quota request can ask for 8 vCPU); raise to 2 when friends join. Run one job end to end. Measure cold start, idle time before scale-in, and cost per clip (spec gaps G7, G10).
- [ ] H. Test the budget cut-off Lambda with a job running and one queued: the task must stop, the instance must terminate, and an `apply` must not undo the zeros (ASG `max_size` and autoscaling `max_capacity` are ignored). Then restore both limits by hand (commands in ADR 0004). Also confirms whether the role needs CloudWatch alarm permissions.
- [ ] H. Test a Spot release path if possible, and check the usage log lines per user.
- [ ] H. Raise the ASG `--max-size` to 2 the same way.
- [ ] C. Update README and CLAUDE.md with real commands and measured numbers. Update the spec's cold-start and idle numbers.

## Off-path human items

- [ ] H. Update the draw.io diagram: input-upload path, SNS and Budgets (`human_todo/update_diagram.md`).
- [ ] H. Decide whether to close the idle account `308374654798` after the models migration works.
