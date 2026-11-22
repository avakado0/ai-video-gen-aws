# AI Video Generation Pipeline on AWS

[![ci](https://github.com/avakado0/ai-video-gen-aws/actions/workflows/ci.yml/badge.svg)](https://github.com/avakado0/ai-video-gen-aws/actions/workflows/ci.yml)

A cost-optimized, serverless-front / GPU-back architecture for generating AI video clips on demand. The design goal is **near-zero cost when idle**: GPU capacity only exists while jobs are in the queue.

Built end to end by one person on a $100 AWS credit: Terraform (17 files), a FastAPI Lambda, a GPU worker container, a no-build web front end, and 383 offline tests plus 10 front-end tests. Developed test-first.

> **Status (2026-11-22): built, deployed, and run on a real AWS GPU.**
> The stack is applied with the GPU limit at zero between tests, so it costs almost nothing while idle. The worker has run on Spot `g5.2xlarge` instances on 2026-11-21 and 2026-11-22. The current model, Wan 2.2 5B (text and image to video), produced clips the owner accepted. Not yet proven: a real Spot interruption, and the budget cut-off against a live job. See [What is verified](#what-is-verified).

![Architecture diagram](System_Diagrams/FINAL-AI%20Video%20Gen%20System.drawio.png)

## What it does

A small, invite-only group of users logs in to a web app, submits a text prompt, and receives a generated video clip. Jobs are queued, picked up by a GPU worker running on a Spot instance, and the finished clip is downloaded directly from S3.

## Architecture at a glance

| Layer | Services | Notes |
|---|---|---|
| Web app | CloudFront + private S3 (OAC) | Static app served over HTTPS |
| Auth | Cognito User Pool | Admin-create-only, public client, auth code + PKCE |
| API | API Gateway (HTTP API) + one Lambda running FastAPI via Mangum | JWT authorizer; the only entry point |
| Queue | SQS standard queue + DLQ | 3-minute visibility timeout with worker heartbeat |
| Failure reporting | DLQ-triggered Lambda | Writes a `.failed` marker to S3, no database needed |
| Compute | ECS on EC2 (Spot `g5.2xlarge`; `g6.2xlarge` held back, see plan 06) in an ASG, min size 0 | Worker is a Docker container from ECR |
| Storage | Two S3 buckets: model weights, generated clips | Weights copied to instance-store NVMe on each boot |
| Scaling | CloudWatch alarms -> ECS Service Auto Scaling -> capacity provider -> ASG | Scales from and to zero |

Full detail: [`docs/architecture/DRAFT_architecture.md`](docs/architecture/DRAFT_architecture.md).

## Key design decisions

These are the tradeoffs the design is built around.

- **ECS on EC2, not Fargate.** Fargate has no GPU support, so the worker runs on Spot GPU instances behind an ECS capacity provider.
- **Public subnets, no NAT gateway.** A NAT gateway costs roughly $32/month before any traffic. Instances get public IPs with no inbound security-group rules, the Internet Gateway carries API traffic, and an S3 Gateway Endpoint keeps S3 traffic free.
- **Scaling from zero needs two policies.** Pure target tracking divides by the running-instance count, so it can't wake from 0. A step-scaling alarm on visible messages `>= 1` handles 0 -> 1, and target tracking with metric math handles 1 -> N.
- **Scale-in counts in-flight messages.** A message being processed is not visible, so a visible-only metric reads 0 mid-job and would kill the running worker. The metric sums visible + not-visible messages, and ECS task scale-in protection covers the running job.
- **Spot interruption is a release, not a failure.** On the 2-minute warning the worker aborts and sets the message visibility to 0, so another worker retries immediately. Interruptions never write a failure marker. With no warning, the message reappears after the visibility timeout: delayed, not lost.
- **Failure reporting without a database.** A permanent error makes the worker write `clips/{sub}/{job_id}.failed` itself. Crashes and repeated failures end up in the DLQ, and a Lambda writes the same marker. `GET /jobs/{id}` reports `ready`, `failed` or `pending` from S3 alone.
- **Per-user isolation.** The API takes the user's `sub` from verified JWT claims, never from the request body, and validates `job_id` as a UUID, so users can't reach each other's `clips/{sub}/` prefix.
- **Model weights live in S3 and are copied to instance-store NVMe on boot.** No EBS volumes to pay for or snapshot. The `g5.2xlarge` instance store (about 450 GB) is the size cap.
- **Pre-signed URLs are minted by the API Lambda, not the worker.** Any pre-signed URL dies when the credentials that signed it expire, and the Lambda's temporary credentials can expire before the 6-hour `ExpiresIn` too. What makes it work is that the API signs a fresh URL on every `GET /jobs/{job_id}`, and the client re-requests on a 403. A worker signs once, at upload, and cannot refresh.

## What is verified

Stated plainly, because a design is only worth what has been run.

| Verified | How |
|---|---|
| API, worker logic, failure paths, budget cut-off and scale-in handlers | 383 offline tests (pytest, moto, fakes for the queue, S3 and the model) and 10 front-end tests (`node --test`), run in CI on every push |
| Terraform stack | `fmt` and `validate` in CI; applied to a real account (95 resources at first), reviewed plans kept in [`terraform/plans/`](terraform/plans/) and [`docs/terraform_plans/`](docs/terraform_plans/) |
| Login, manifest-driven form, job submit through the real API | Used against the deployed stack |
| The worker on real Spot GPU instances | Ran on `g5.2xlarge` (2026-11-21, 2026-11-22) and `g4dn.2xlarge` (2026-11-22): jobs end to end, clips downloaded from the site |
| Wan 2.2 5B, text and image to video | Trial 1 (plan 06): 3 s and 5 s clips, an image-to-video clip from a real site upload, two jobs in one process; the owner accepted the clips |
| Scaling back to zero | Plan 07: an own idle alarm and a scale-in Lambda; the instance is on its way down about 5 minutes after the last upload (measured) |
| Start-image size check | A start image under 512 px is refused in the browser before the upload, and again by the worker; seen working on the real site |
| RAM margin of the 5B worker | Measured on a `g5.2xlarge`, see [Memory](#memory-measured-on-the-g5) |
| Real bugs found on real GPUs | Aborted job left 2.7 GiB on the GPU (T4); tiled VAE decode failed on the 2.2 VAE; fp32 VAE decode ran out of GPU memory; container OOM-killed with the prompt encoder kept in RAM. All fixed and re-run |

| Not yet verified | Why |
|---|---|
| Spot interruption handling on a real instance | Needs a real Spot withdrawal; release and retry are tested with fakes only |
| That the budget cut-off stops a running job's instance | Tested with moto; a live-job test is planned (it releases a running job, it does not finish it) |
| The DLQ alarm firing on a real dead-lettered job | Wired, not triggered yet |
| `g6.2xlarge` speed | Held back; one pinned job would measure it if `g5` capacity ever runs short |
| Whether memory grows over many jobs in one process | Anonymous RAM rose from 13.3 to 15.5 GiB between a text and an image job (probably the job type); a third job would tell |

## Measured so far

Current model (plan 06, [ADR 0005](docs/adr/0005-wan22-5b-and-the-24gb-gpu-floor.md)): Wan 2.2 TI2V 5B, text to video and image to video with an optional start image, 1280x704 at 24 fps, on a 24 GB GPU (`g5.2xlarge`, A10G). Two quality levels, both at 1280x704: `fast` is 30 steps and `high` is 50.

Measured on a `g5.2xlarge` Spot instance, 2026-11-22 (plan 06 trial 1 and the RAM run):

| Clip | Steps | Seconds per step | GPU seconds | Peak GPU memory | GPU cost (my arithmetic at about $0.8 an hour) |
|---|---|---|---|---|---|
| 3 s text, `fast` | 30 | 11.6 | 420.5 | 12.6 GiB | about $0.09 |
| 3 s image to video, `fast` | 30 | 11.6 | 389.6 | 13.9 GiB | about $0.09 |
| 5 s text, `fast` | 30 | 22.3 | 763.7 | 14.6 GiB | about $0.17 |
| 5 s image to video, `fast` | 30 | 22.3 | 731.9 | 15.9 GiB | about $0.16 |
| 5 s text, `high` | 50 | 22.3 | 1209.6 | 14.6 GiB | about $0.27 |

GPU memory is of the A10G's 22.06 GiB usable. A whole wake-up also pays the cold start and the idle wait: launch to a ready worker took about 7 minutes with only the 5B weights in S3 (about 9 minutes when 63 GB of weights were there), and the instance stays up about 5 minutes after the last upload. Two 5 s clips in one wake-up came to about 40 minutes of `g5` and about $0.5 (my estimate). Spot prices checked on 2026-11-22 (us-west-2): `g5.2xlarge` $0.68 to $0.90 an hour.

### Memory (measured on the g5)

The worker container has a 29 GiB limit (the instance has 32 GiB). The worker's log line "peak RAM" read 27 to 28 GiB, which looked like a margin of 1 GiB. It is not: that number counts memory-mapped weight files (the transformer is stored as fp32 shards and read as bf16), which the kernel can drop at any time. The worker now logs a split of anonymous memory, which is the part that can get a container killed, and file pages, for the process and for the container's own counters. Two 5 s jobs in one process (a text clip, then an image-to-video clip):

| Moment | Anonymous RAM | File pages in the container |
|---|---|---|
| After loading the model | 11.2 GiB | 10.5 GiB |
| After the first job (text) | 13.3 GiB | 14.1 GiB |
| After the second job (image to video) | 15.5 GiB | 12.2 GiB |

Real memory use peaked at 15.5 GiB, about 13 GiB under the limit. The container's own peak counter reached 29.0 GiB because file cache fills up to the limit, and the kernel reclaims it; nothing was killed. The earlier out-of-memory kill happened when the 11.4 GB prompt encoder was held in RAM; it is now loaded for each job straight to the GPU and dropped before denoising. Details: [`_plans/06_wan22_ti2v_5b.md`](_plans/06_wan22_ti2v_5b.md).

History: the first model was Wan 2.1 T2V 1.3B at 832x480 (a Kaggle T4, then `g5` and `g4dn` on AWS). Its settings problem (flow_shift 3.0 blew the colours out) was found and fixed in plan 05. Its weights are deleted from S3, and the code stays in `worker/wan.py` as a parked path.

## Cost model

Idle cost is a handful of small standing items: model weights in S3, ECR image storage, and a few CloudWatch alarms. There is no NAT gateway, ALB or interface endpoint. GPU cost applies from the moment an instance launches until it is gone: cold start (about 7 minutes), the job, and the idle wait (about 5 minutes since plan 07; it was about 33 before).

Per-user cost is tracked through usage logs: the worker records GPU seconds per job, and the API records bytes per download. A saved CloudWatch Logs Insights query sums them per user, and each user's share is applied to the monthly bill. See section 3H of the spec.

Idle cost is estimated at about $1 to $3 a month (weights in S3 about $0.8 for 34 GB, the image in ECR about $0.42; my arithmetic). These are estimates; they have not been checked against the Billing console.

## Repository layout

```
docs/
  architecture/DRAFT_architecture.md   Architecture spec (source of truth)
  adr/                                 Decision records (no database, no NAT, weights on NVMe, budget cut-off, Wan 2.2 5B)
  runbook_stop_gpu.md                  How to stop the GPU now, including a running job
  terraform_plans/                     Reviewed plan output for later changes (redacted)
  wan_spike_findings.md                GPU spike results and the bug it found
  huggingFace_to_s3_instructions.md    Runbook: copy model weights from Hugging Face to S3
_plans/                                Build order and feature plans
System_Diagrams/                       draw.io diagrams (FINAL is current)
terraform/                             The AWS stack (deployed 2026-11-18); plans/ holds reviewed plan output
tests/                                 Offline test suite (383 pytest tests, 10 front-end node tests)
spike/                                 Kaggle notebooks used for the GPU spike
.github/workflows/ci.yml               CI: pytest, terraform fmt and validate
lambdas/                               API, failure-marker, budget cut-off and scale-in Lambdas; common/ is shared with the worker
worker/                                The GPU worker container (Dockerfile, pinned requirements, `python -m worker`)
scripts/build_api.sh                   Builds the API Lambda bundle
scripts/build_push.sh                  Builds the worker image and pushes it to ECR
model_manifest.json                    What the model accepts; drives the form and the API checks
frontend/                              Static web app (vanilla JS, mock mode)
```

## Roadmap

- [x] Architecture spec and diagram
- [x] Terraform: networking, auth, API, storage, queue, compute, scaling, IAM (17 files, applied)
- [x] FastAPI application (OOP, modular)
- [x] GPU worker container, built and pushed to ECR
- [x] Web front end
- [x] GPU spike on a Kaggle T4, model weights in S3
- [x] Spot quota approved, plan upgraded, first jobs on a real GPU instance
- [x] Cold start, idle wait and per-clip GPU time measured
- [x] Wan 2.2 5B, text and image to video, accepted
- [x] Fast scale-in (plan 07), start-image size check, RAM margin measured
- [ ] Spot interruption test on a real instance
- [ ] Budget cut-off with a live job
- [ ] Portrait and aspect-ratio option (outputs are fixed landscape 1280x704)
- [ ] Friends' Cognito users and a go-live check of the real bill

The detailed checklist is [`_plans/01_build_order.md`](_plans/01_build_order.md).

## Account notes

- GPU instance types are not available on the AWS Free plan, so the account was upgraded to Paid on 2026-11-21. The Spot quota for "All G and VT Spot Instance Requests" in `us-west-2` covers one `g5.2xlarge` (8 vCPU) at a time.
- The GPU limits (ASG max size and the service's autoscaling max) stay at 0 between tests and are raised by hand; Terraform ignores them on purpose so an apply cannot undo a budget cut-off.

## Deploying

From `terraform/`, with `export AWS_PROFILE=terraform-deployer` (check `aws sts get-caller-identity` shows the right account):

```bash
cd .. && ./scripts/build_api.sh && cd terraform   # only when lambdas/api/ or requirements-api.txt changed
terraform plan -out=tfplan
terraform apply tfplan
```

`build_api.sh` installs the API's dependencies (FastAPI, Mangum) for the Lambda runtime into the git-ignored `build/api/`, and Terraform zips that folder. Skip it and `terraform plan` stops with an "archive" error, or ships an old API.

After a front-end change, clear the CloudFront cache, or browsers can get the old files for a while (two cache layers, not just the 5-minute TTL). Up to 1,000 paths a month are free:

```bash
aws cloudfront create-invalidation --distribution-id "$(terraform output -raw cloudfront_distribution_id)" --paths "/*"
```

## License

MIT. See [LICENSE](LICENSE). Reuse is allowed, including commercially, as long as the copyright notice is kept.
