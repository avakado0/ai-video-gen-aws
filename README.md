# AI Video Generation Pipeline on AWS

[![ci](https://github.com/avakado0/ai-video-gen-aws/actions/workflows/ci.yml/badge.svg)](https://github.com/avakado0/ai-video-gen-aws/actions/workflows/ci.yml)

A cost-optimized, serverless-front / GPU-back architecture for generating AI video clips on demand. The design goal is **near-zero cost when idle**: GPU capacity only exists while jobs are in the queue.

Built end to end by one person on a $100 AWS credit: Terraform (17 files), a FastAPI Lambda, a GPU worker container, a no-build web front end, and 354 offline tests. Developed test-first.

> **Status (2026-11-20): everything except the first GPU run is built and deployed.**
> The stack is applied with the GPU limit at zero, so it costs almost nothing. The model code has run on a Kaggle T4, the worker image is built and pushed to ECR, and the model weights are in S3. What has not happened yet is the first job on a real AWS GPU instance. See [What is verified](#what-is-verified).

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
| Compute | ECS on EC2 (Spot `g5.2xlarge`, `g6.2xlarge` to follow) in an ASG, min size 0 | Worker is a Docker container from ECR |
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
| API, worker logic, failure paths, budget cut-off and scale-in handlers | 307 offline tests (pytest, moto, fakes for the queue, S3 and the model), run in CI on every push |
| Terraform stack | `fmt` and `validate` in CI; applied to a real account (95 resources), reviewed plans kept in [`terraform/plans/`](terraform/plans/) |
| Login, manifest-driven form, job submit through the real API | Used against the deployed stack |
| Model on a GPU | Wan 2.1 1.3B on a free Kaggle T4: clean frames, peak 12.7 GiB VRAM, measured speed. See [`docs/wan_spike_findings.md`](docs/wan_spike_findings.md) |
| A real bug found on that GPU | An aborted job left 2.7 GiB on the GPU, so the next job would run out of memory. Fixed and re-checked on the T4 |

| Not yet verified | Why |
|---|---|
| The worker image running on a g4dn Spot instance | Needs the Spot quota (requested) and a plan upgrade |
| Cold-start time, Spot interruption handling on real instances | Same |
| That the budget cut-off actually stops a running instance | Planned as a go-live test |

## Measured so far

**Next model (plan 06, `docs/adr/0005-wan22-5b-and-the-24gb-gpu-floor.md`):** Wan 2.2 TI2V 5B, text-to-video and image-to-video with an optional start image, 1280x704 at 24 fps, on a 24 GB GPU (`g5.2xlarge`; `g6.2xlarge` was held back, see plan 06). `fast` is 30 steps and `high` is 50. It ran on AWS on 2026-11-22 (trial 1, `g5.2xlarge` Spot): text to video 3 s and 5 s, and image to video from a real site upload, two jobs in one process; the owner accepted the clips. A `fast` 3 s clip takes about 6.5 min of GPU and a `fast` 5 s clip about 12.7 min (about 11.6 and 22.3 s per step). The numbers in the Wan 2.1 1.3B tables below are history. The 1.3B weights are deleted from S3.

Model measured so far: Wan 2.1 T2V 1.3B on a Tesla T4. Two quality levels are exposed as a manifest parameter:

| Level | Size | Steps | 3 s clip |
|---|---|---|---|
| `fast` (default) | 832x480 | 30 | about 13.5 min on the T4 (Kaggle, 808 s); 200 s on a g5.2xlarge (AWS, 2026-11-21) |
| `high` | 832x480 | 50 | 1050 s (17.5 min) on a g4dn.2xlarge T4 (AWS, 2026-11-22, 19.4 s per step); about 21 min on the Kaggle T4; about 5 min on a g5.2xlarge (my estimate, not measured) |

Both levels use Wan's own recommended settings for the 1.3B model (guidance 6, flow_shift 8, Wan's default negative prompt). The first AWS test used flow_shift 3.0, which blew the colours out; plan 05 (`_plans/05_quality_variants.md`) found and fixed that. The 640x368 size of the first spike was dropped: it is below the 480p the model was trained for. Cold start from launch to a ready worker was 6 min 17 s on a g5 and 6 min 34 s on a g4dn (measured). The worker image is 4.16 GB compressed. Weights are 28.9 GB. Spot prices checked on 2026-11-22 (us-west-2): g4dn.2xlarge $0.29 to $0.32 an hour, g5.2xlarge $0.69 to $0.90 an hour. GPU cost per `high` clip is about $0.09 on the g4dn (1050 s measured) and about $0.06 to $0.08 on the g5 (the g5 time is not measured), plus cold start (about 6.5 min, about $0.03 on the g4dn). The instance then stays up for an idle wait of about 5 minutes (measured after plan 07; it was about 33 billed minutes before), about $0.03 on the g4dn and $0.06 to $0.08 on the g5. A whole wake-up with a `fast` job on the g4dn (launch to termination start, about 23 min) came to about $0.11 (my arithmetic from the Spot price).

## Cost model

Idle cost is a handful of small standing items: model weights in S3, ECR image storage, and a few CloudWatch alarms. There is no NAT gateway, ALB or interface endpoint. GPU cost applies from the moment an instance launches until it is gone: cold start, the job, and the idle wait (about 5 minutes measured since plan 07).

Per-user cost is tracked through usage logs: the worker records GPU seconds per job, and the API records bytes per download. A saved CloudWatch Logs Insights query sums them per user, and each user's share is applied to the monthly bill. See section 3H of the spec.

Idle cost is estimated at about $1 to $3 a month (weights in S3 about $0.65, the image in ECR about $0.42). The real figures, and cold-start time, will replace these after the first run on AWS.

## Repository layout

```
docs/
  architecture/DRAFT_architecture.md   Architecture spec (source of truth)
  adr/                                 Decision records (no database, no NAT, weights on NVMe, budget cut-off)
  wan_spike_findings.md                GPU spike results and the bug it found
  huggingFace_to_s3_instructions.md    Runbook: copy model weights from Hugging Face to S3
_plans/                                Build order and feature plans
System_Diagrams/                       draw.io diagrams (FINAL is current)
terraform/                             The AWS stack (deployed 2026-11-18); plans/ holds reviewed plan output
tests/                                 Offline test suite (275 tests)
spike/                                 Kaggle notebooks used for the GPU spike
.github/workflows/ci.yml               CI: pytest, terraform fmt and validate
lambdas/                               API, failure-marker and budget cut-off Lambdas; common/ is shared with the worker
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
- [ ] Spot quota approved, plan upgraded, first job on a real GPU instance
- [ ] Measure real cost and cold-start time, and update this README

The detailed checklist is [`_plans/01_build_order.md`](_plans/01_build_order.md).

## Pre-deploy notes

- Request a Spot service-quota increase for "All G and VT Spot Instance Requests" in `us-west-2`. New accounts often have 0. (Requested for 8 vCPU, pending.)
- The GPU instance types are not available on the AWS Free plan, so go-live needs a plan upgrade.

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
