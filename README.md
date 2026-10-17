# AI Video Generation Pipeline on AWS

A cost-optimized, serverless-front / GPU-back architecture for generating AI video clips on demand. The design goal is **near-zero cost when idle**: GPU capacity only exists while jobs are in the queue.

> **Status: design complete, infrastructure code not started.**
> The architecture spec and diagram are done. Terraform, the API and the worker are planned (see [Roadmap](#roadmap)). This README will be updated as each part lands.

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
| Compute | ECS on EC2 (Spot `g4dn.xlarge` / `g5.xlarge`) in an ASG, min size 0 | Worker is a Docker container from ECR |
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
- **Model weights live in S3 and are copied to instance-store NVMe on boot.** No EBS volumes to pay for or snapshot. The 125 GB `g4dn.xlarge` instance store is the size cap.
- **Pre-signed URLs are minted by the API Lambda, not the worker.** URLs signed with EC2 role credentials die when those credentials rotate, so the API signs a fresh 6-hour URL on every request.

## Cost model

Idle cost is a handful of small standing items: model weights in S3, ECR image storage, and a few CloudWatch alarms. There is no NAT gateway, ALB or interface endpoint. GPU cost applies only while jobs are being processed.

<!-- TODO: add measured numbers once deployed: idle $/month, $/clip, cold-start time. -->

## Repository layout

```
docs/
  architecture/DRAFT_architecture.md   Architecture spec (source of truth)
  huggingFace_to_s3_instructions.md    Runbook: copy model weights from Hugging Face to S3
System_Diagrams/                       draw.io diagrams (FINAL is current)
terraform/                             Planned, not yet created
```

## Roadmap

- [x] Architecture spec and diagram
- [ ] Terraform: networking, auth, API, storage, queue, compute, scaling, IAM
- [ ] FastAPI application (OOP, modular)
- [ ] GPU worker container
- [ ] Web front end
- [ ] Deploy, measure real cost and cold-start time, and update this README

## Pre-deploy notes

- Request a Spot service-quota increase for "All G and VT Spot Instance Requests" in `us-west-2`. New accounts often have 0.
- Choose the model and confirm it fits the instance-store size cap.
