# AWS Architecture Specification: Serverless-Triggered AI Video Generation Pipeline

## 1. System Overview & Objectives

* **Core Purpose:** An automated, highly cost-optimized video generation pipeline driven by user requests, leveraging GPU Spot Instances, containerization, and serverless messaging.
* **Cost Constraints:**
  * Near-zero baseline infrastructure cost when idle (scale-to-zero). No compute runs when idle. Small standing costs remain: model weights in S3 (about $2-3/month per 100 GB), ECR image storage, a few CloudWatch alarms. While GPU instances run, a public IPv4 address costs about $0.005/hr each; this is accepted in place of a NAT Gateway.
  * No data transfer fees for S3-to-EC2 traffic, via a VPC Gateway Endpoint. (The user's clip download over a pre-signed URL is internet egress, which is billed above the AWS free tier but is small at this scale.)
  * Free-tier or pay-per-request services for the front end and auth (CloudFront, Cognito, API Gateway HTTP API, Lambda) and zero control-plane fees for container orchestration (ECS on EC2).
* **Engineering Constraints:**
  * Terraform best practices apply (section 6).
  * Application code follows OOP principles and is modular.
* **Region:** `us-west-2` (Oregon). Large GPU Spot pools and low interruption rates for g4dn/g5.
* **Backend language:** Python. FastAPI for the API, and the same language for the GPU worker.

---

## 2. High-Level Architecture Diagram Flow

```text
[Browser: web app on CloudFront + S3] ──(login)──► [Cognito User Pool] ──► JWT
       │ (HTTPS + JWT)           └─(pre-signed POST: image/audio inputs)──► [Clips bucket: inputs/]
       ▼
[API Gateway HTTP API (JWT authorizer)] ──► [Lambda: FastAPI]
       │ SendMessage                          │ HEAD / presign / delete
       ▼                                      ▼
[Amazon SQS Queue] ──(after 5 receives)──► [DLQ] ──► [Lambda: failure marker] ──► writes .failed to Clips bucket
       │ (CloudWatch alarms ──► ECS Service Auto Scaling ──► ECS Capacity Provider ──► ASG)
       ▼
[ECS Cluster (EC2 Launch Type)] ──► [Auto Scaling Group, min 0] (spans 2 AZs, public subnets)
       │                              │
       │ (Pulls Image)                ▼
[Amazon ECR]               [Spot Instances (g4dn / g5)]  (worker = Docker container, not a Lambda)
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼ (copies weights to NVMe, via S3 endpoint)   ▼ (uploads clip, same endpoint)
      [S3 Bucket: Models]                          [S3 Bucket: Clips]  ◄── (user downloads via pre-signed URL)
               ▲
      [Hugging Face] ──(one-time manual copy, see huggingFace_to_s3_instructions.md)
```

Other AWS API traffic (ECS agent, ECR API, SQS, CloudWatch Logs) leaves the instances through the Internet Gateway.

---

## 3. Detailed Component Specifications

### A. Networking & Security (`terraform/vpc.tf`)

* **VPC:** Custom VPC with **public subnets** across two Availability Zones (e.g., `us-west-2a`, `us-west-2b`).
* **Internet Gateway (`aws_internet_gateway`):** Free. Route `0.0.0.0/0` points to it, which carries ECS, ECR API, SQS and CloudWatch traffic. No NAT Gateway.
* **S3 VPC Gateway Endpoint (`aws_vpc_endpoint`):**
  * Service: `com.amazonaws.<region>.s3`, type Gateway.
  * Associated with the public route table. S3 traffic uses the endpoint's prefix-list route and stays on the AWS backbone for **$0.00/GB**.
* **Instances:** `associate_public_ip_address = true` in the launch template. Security group has **no inbound rules**; outbound only.

### B. Authentication, Front End & API (`cognito.tf`, `cloudfront.tf`, `api.tf`)

* **Cognito User Pool (`aws_cognito_user_pool`) + App Client:** User creation restricted to administrators (`admin_create_user_config`), so only the owner, a friend, and whitelisted users (about 5) can sign in. The App Client is a **public client (no client secret)** using authorization code + PKCE, because it runs in a browser. Issues a JWT on login. The login page uses a free Cognito prefix domain (`<prefix>.auth.us-west-2.amazoncognito.com`). Users are created by hand in the Cognito console, not in Terraform, so their emails stay out of the public repo and the Terraform state.
* **Web app:** Static site in a private S3 bucket served through CloudFront (origin access control), on the default `*.cloudfront.net` address. Terraform uploads the files and writes `config.json` from its outputs (section 5). Short cache TTL (about 5 minutes), so updates show without an invalidation. No custom domain, Route 53 or ACM certificate. The login screen uses the User Pool.
* **API:** API Gateway HTTP API with a Cognito JWT authorizer, in front of a single Lambda that runs the whole FastAPI app through an ASGI adapter (Mangum); API Gateway routes every path to it. A browser cannot write to SQS directly, so the API is the only entry point.
  * The caller's `sub` always comes from the **verified JWT claims**, never from the request body. `job_id` is validated as a UUID before it is used in an S3 key. Otherwise a user could read or delete another user's `clips/{sub}/` prefix.
  * `GET /config`: returns the capability manifest (see "Capability manifest" below). The front end builds its form from it.
  * `POST /uploads`: body names one manifest input slot and the file's position in it (`slot`, `index`, plus MIME type and size). The API checks it against that slot's definition and returns a **pre-signed POST** (not PUT, so the policy can carry `content-length-range` and `Content-Type` conditions) for `inputs/{sub}/{job_id}/{slot}/{index}`. The first upload of a job also mints the `job_id`, which the client reuses for later uploads and for `POST /jobs`. The browser uploads straight to S3.
  * `POST /jobs`: body carries `job_id`, `prompt`, optional `params`, and how many files were uploaded per slot (`{slot: count}`). The API validates everything against the manifest (prompt present when required, every required slot present, no unknown slots, file counts within `max_files`, param values clamped to their ranges), HEADs each input under `inputs/{sub}/{job_id}/` (which also yields its size), writes the `clips/{sub}/{job_id}.pending` marker, then sends the request to SQS (includes the caller's `sub`). It rejects the request with 429 if the caller already has 5 active jobs (`.pending` markers without a `.mp4` or `.failed`). The cap is soft: concurrent requests can overshoot it by one; no lock or counter. It logs the input `bytes` for cost attribution (3H).
  * `GET /jobs`: lists `clips/{sub}/` and groups the objects by `job_id`. `.mp4` means `ready`, `.failed` means `failed`, a bare `.pending` means `pending`. Each entry carries the creation time and prompt read from the marker. Because there is no database, this listing is how a reload or another device finds its jobs.
  * `GET /jobs/{job_id}`: checks `clips/{sub}/{job_id}.mp4`, `.failed` and `.pending` with HEAD. Returns `ready` plus a fresh pre-signed GET URL (6-hour expiry, see 3C), `failed` plus the failure reason (read from the marker), or `pending`. If none exist it returns 404.
  * `DELETE /jobs/{job_id}`: called after the browser confirms the download. Deletes the clip and the `.pending` marker.
  * CORS is enabled on the HTTP API for the CloudFront origin, and on the clips bucket for browser `POST` uploads to `inputs/` (and for downloads if they use `fetch`).
* **Capability manifest (`model_manifest.json`, repo root of the deployment):** one deployment serves exactly one model, so one manifest describes it. Terraform reads the file and passes it as an environment variable to the API Lambda and the ECS task, so there is a single source of truth and no extra IAM. Contents:
  * `prompt`: `required` (default), `optional` or `none`. Avatar and lip-sync models often take no prompt.
  * `inputs`: a list of named **input slots**. Each slot has `name` (lowercase, digits, `_`; used in the S3 key), `label` (shown in the UI), `kind` (`image`, `audio` or `video`), `required`, `max_files` (default 1; more than 1 makes it a repeatable slot, e.g. reference images), `mime` (allowed types), `max_bytes` (per file), and for `audio`/`video` an optional `max_seconds`. The prompt is not a slot. Examples: text-to-video has no slots; image-to-video has one optional `image` slot; first/last-frame has two image slots; video-to-video has a required `source_video` slot; a reference-images model has one `references` slot with `max_files` 4; lip-sync has `source_video` + `audio`; an avatar model has a required `face_image` + `audio`. New model shapes need no code change.
  * `limits`: `prompt_max_chars`.
  * `params`: optional list of generation parameters, each with `name`, `type` (`int`, `float`, `enum`, `bool`, `string`), `default`, and `min`/`max`, allowed `values`, or `max_chars` (strings, e.g. a negative prompt). May be empty.
  * The front end renders the form generically from the manifest and has no model-specific code. The API enforces the manifest on `POST /uploads` and `POST /jobs`, and the worker re-checks it, so calling the API directly cannot bypass it. Changing models means editing this file and the `models/<name>/` prefix.
* **Front end source (`frontend/`):** vanilla JS ES modules, no build step and no npm. Cognito PKCE uses WebCrypto. All backend calls go through one `ApiClient` interface with a real implementation (`fetch` + JWT) and a mock implementation (in-memory state, fake login, sample clip). `config.json` selects which one, so the same files run with no backend.

### C. Storage & Data Management (`terraform/storage.tf`)

* **Model Weights S3 Bucket (`aws_s3_bucket`):**
  * Storage Class: S3 Standard.
  * Key layout: `models/<model-name>/...`. The runbook's `S3_PREFIX` and the `user_data` copy path must both use this prefix.
  * Contents: base model weights, copied once from Hugging Face (see `docs/huggingFace_to_s3_instructions.md`). The pipeline never writes to it.
* **Generated Clips S3 Bucket (`aws_s3_bucket`):**
  * Key layout:
    * `clips/{sub}/{job_id}.mp4`: the generated clip.
    * `clips/{sub}/{job_id}.pending`: small JSON marker written by `POST /jobs` (creation time, prompt, params). It is what makes an in-progress job visible to `GET /jobs`.
    * `clips/{sub}/{job_id}.failed`: JSON marker for failed jobs: `{"code", "field"?, "message"}`. The worker writes `invalid_input` (with the offending `field`) for permanent input errors. The DLQ Lambda writes `{"code": "internal"}`. The message is sanitised, because the UI shows it to the user.
    * `inputs/{sub}/{job_id}/{slot}/{index}`: the user's uploaded inputs, one object per file (a slot can hold several) (3B).
  * Lifecycle (`aws_s3_bucket_lifecycle_configuration`): **expire after 1 day** (the S3 minimum) on both the `clips/` and `inputs/` prefixes. Cheaper storage classes need 30+ days, so there is no transition rule. S3 runs lifecycle rules asynchronously, so removal can lag by up to a day. This is a backstop: the clip is normally deleted by the API as soon as the user's download is confirmed, and inputs are deleted by the worker after a successful job (section 4). Orphaned uploads, failed jobs and dead-lettered jobs are cleaned by the rule. A job still queued after a day finds its inputs expired and fails with `invalid_input` ("input expired").
  * CORS: the bucket allows browser `POST` from the CloudFront origin, for the pre-signed upload.
  * Access: private. Videos are exposed only through **pre-signed URLs generated by the API Lambda at download time**. The worker does not generate URLs: URLs signed with EC2 instance-role credentials die when those temporary credentials rotate (hours), so a long-lived URL is not possible from the worker.
  * URL expiry: `ExpiresIn=21600` (6 hours). This is best-effort: the Lambda role's temporary credentials can expire sooner, and the URL stops working with them. The API mints a fresh URL on every `GET /jobs/{job_id}`, so the client simply re-requests if a download returns 403.

### D. Messaging & Queueing (`terraform/sqs.tf`, `failure.tf`)

* **Amazon SQS Queue (`aws_sqs_queue`):**
  * Standard queue for task buffering.
  * Retention Period: 4 days.
  * Visibility Timeout: **3 minutes**, with a worker heartbeat that extends it (`ChangeMessageVisibility`) every minute while a job runs. A live worker keeps its job. A dead worker frees it within about 3 minutes.
  * Redrive policy: `maxReceiveCount = 5`, then the message moves to the DLQ. Every receive counts, including retries after a Spot release, so five in a row on one healthy job is needed to dead-letter it by mistake.
  * No results queue. The UI learns a clip is ready or failed by polling the API (section 3B).
* **Dead-letter queue (`aws_sqs_queue`):** Receives jobs that failed 5 times. A CloudWatch alarm watches it and notifies an SNS topic with an email subscription to the owner, so failures are visible. The subscription must be confirmed from the email once after the first apply.
* **Failure-marker Lambda:** Triggered by the DLQ (SQS event source mapping, $0 when idle). Reads `sub` and `job_id` from the message body and writes `clips/{sub}/{job_id}.failed` containing `{"code": "internal"}`. This covers deaths the worker could not report (out of memory, crash, Spot loss without warning).

### E. Compute & Orchestration (`terraform/ecr.tf`, `ecs.tf`, `compute.tf`)

* **Worker:** a Docker container on a Spot EC2 GPU instance, started by ECS. It is not a Lambda.
* **Amazon ECR (`aws_ecr_repository`):** Stores the Dockerized video generation worker image.
* **ECS Cluster, Task Definition, Service (`aws_ecs_cluster`, `aws_ecs_task_definition`, `aws_ecs_service`):**
  * Launch Type: **EC2** (required for GPU support). Capacity comes from a capacity provider tied to the ASG.
  * Task Parameters: requests one GPU, bind-mounts the instance-store path `/data` into the container.
  * `StopTimeout`: 120 seconds. This is not enough to finish a job, so the worker aborts and releases its message on SIGTERM (section 4).
* **Launch Template (`aws_launch_template`):**
  * AMI: official **Amazon ECS-optimized GPU AMI** (NVIDIA drivers and Docker runtime preinstalled).
  * Instance types: diversified (`g4dn.2xlarge`, `g5.2xlarge`). Both have 8 vCPU and 32 GB RAM, which the model needs (section 5), so the worker has one code path. The `.xlarge` sizes have only 16 GB RAM.
  * `ecs.config`: `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` so ECS drains the instance and sends SIGTERM on a Spot interruption notice.
  * Metadata options: `http_put_response_hop_limit = 2` so the worker container can reach IMDS (to poll for the Spot notice).
  * `user_data`: on every boot, formats and mounts the local NVMe instance store at `/data` (it is not mounted automatically and is wiped on stop/terminate), copies `models/<model-name>/` from the S3 models bucket via the Gateway Endpoint, then starts the ECS agent. Weights always live in S3 and are never kept on EBS.
  * Size cap: weights plus working space must fit the instance store: `g4dn.2xlarge` about 225 GB, `g5.2xlarge` about 450 GB. Treat 225 GB as the limit while `g4dn` is in the mix. Larger weights mean changing the instance types, not adding EBS.
  * Cold start: every Spot boot re-copies the weights (free, but it adds time roughly equal to weights size divided by S3 throughput) and pulls the multi-GB worker image from ECR.
* **Auto Scaling Group (`aws_autoscaling_group`):**
  * Min Size `0`, Max Size `2`, two AZs. Max Size is the GPU cost ceiling: at most two clips render at once.
  * `mixed_instances_policy` over the g4dn/g5 types, Spot allocation `price-capacity-optimized`, on-demand base capacity 0.
  * `protect_from_scale_in = true` (required for the capacity provider's **managed termination protection**). The capacity provider uses managed scaling.
  * Group metrics enabled (`GroupInServiceInstances`).

### F. Scaling Chain (`terraform/scaling.tf`)

```text
SQS depth ─► CloudWatch alarm / metric math ─► ECS Service Auto Scaling (desired_count)
          ─► ECS capacity provider ─► ASG ─► Spot GPU instance
```

* The Application Auto Scaling target for the ECS service has `min_capacity = 0`.
* **0 → 1 (wake-up):** CloudWatch alarm on `ApproximateNumberOfMessagesVisible >= 1`. A step scaling policy sets the ECS service desired count from 0 to 1.
* **1 → N:** target tracking with CloudWatch metric math: (`ApproximateNumberOfMessagesVisible` + `ApproximateNumberOfMessagesNotVisible`) divided by the ASG's `GroupInServiceInstances` (one task per GPU instance), targeting a small number of jobs per task. No custom Lambda. The ECS `RunningTaskCount` metric is not used because it needs paid Container Insights. (Pure target tracking cannot wake up from 0 because of division by zero, hence the step policy above.)
* **Scale-in to 0:** the metric must use visible **+ not-visible** messages. A message being processed is not visible, so visible alone reads 0 while a job is running and would scale the service to zero mid-job. Also protect running tasks with ECS task scale-in protection (`UpdateTaskProtection`) while a job is active.

### G. IAM (`terraform/iam.tf`)

* **EC2 instance profile:** ECS agent permissions, plus `s3:GetObject` and `s3:ListBucket` on the models bucket (for the `user_data` copy).
* **ECS task role (worker):** `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility` on the queue; `s3:PutObject` on `clips/*` in the clips bucket; `s3:GetObject` and `s3:DeleteObject` on `inputs/*`; `ecs:UpdateTaskProtection`.
* **ECS task execution role:** pull from ECR, write CloudWatch Logs.
* **API Lambda role:** `sqs:SendMessage`; on the clips bucket `s3:GetObject`, `s3:PutObject` (the `.pending` marker and the pre-signed upload POST), `s3:DeleteObject`, and **`s3:ListBucket`**. Without `ListBucket`, a HEAD on a missing key returns 403 instead of 404, and "pending" would look like an error.
* **Failure-marker Lambda role:** `sqs:ReceiveMessage`/`DeleteMessage` on the DLQ; `s3:PutObject` on `clips/*`.

### H. Per-User Cost Attribution (`terraform/ecs.tf`, `api.tf`, `failure.tf`)

All users share the same instances, queue, Lambdas and buckets, so the AWS bill has no per-user dimension and cost-allocation tags cannot split it. Attribution comes from structured usage logs in CloudWatch Logs instead. No database and no always-on resources are added.

* **What drives cost:**
  * GPU Spot time (with the instance's public IPv4 and root volume) is most of the bill. Attributable per job.
  * Clip download egress. Attributable per clip.
  * Input upload storage and transfer (image, audio, video): small at this scale, but the size is logged on `POST /jobs` so it stays attributable.
  * Overhead that no job owns: cold boot, weight copy, image pull, idle time before scale-in. Spread across users in proportion to their GPU time (see the formula below).
  * Lambda, API Gateway, SQS, CloudWatch and S3 request costs are negligible at this scale and are not attributed.
* **Worker usage log:** one JSON line to stdout (ECS ships it to CloudWatch Logs) per job **attempt**, written in the `finally` block (section 4):
  `{"event": "job_attempt", "sub", "job_id", "attempt", "instance_type", "gpu_seconds", "outcome"}`
  * `attempt` is the message's `ApproximateReceiveCount` (request it with `MessageSystemAttributeNames=["ApproximateReceiveCount"]`).
  * `instance_type` comes from IMDS (`meta-data/instance-type`), because g4dn and g5 are priced differently.
  * `gpu_seconds` is wall-clock time from receive to delete or release.
  * `outcome` is one of `ok`, `failed`, `released`. Work lost to a Spot release still counts toward the user whose job it was.
  * A worker killed without warning writes no line; that time falls into overhead.
* **API usage log:** on `DELETE /jobs/{job_id}` (download confirmed), the API HEADs the clip before deleting it and logs:
  `{"event": "download", "sub", "job_id", "bytes"}`
  This approximates egress. Repeat downloads (e.g., after a 403 re-request) and abandoned downloads are not counted. `POST /jobs` likewise logs `{"event": "inputs", "sub", "job_id", "bytes"}` from the HEAD results on the uploaded inputs.
* **Log groups:** declared explicitly in Terraform with `retention_in_days = 90`, so logs do not accumulate forever (Lambda's auto-created groups never expire). Worker group in `ecs.tf`, API Lambda group in `api.tf`, failure-marker Lambda group in `failure.tf`.
* **Saved query (`aws_cloudwatch_query_definition`, free) in `ecs.tf`:** a Logs Insights query over the worker and API log groups that sums `gpu_seconds` per `sub` and `instance_type`, and `bytes` per `sub`, for a chosen time range (normally one calendar month).
* **Monthly allocation (manual, from the query plus Cost Explorer):**
  * For each instance type: user's GPU cost = (user's `gpu_seconds` on that type / everyone's `gpu_seconds` on that type) × that type's EC2 Spot cost for the month (Cost Explorer, grouped by instance type), plus the public IPv4 and EBS costs split the same way.
  * User's egress cost = user's `bytes` × the data-transfer-out rate (after the free tier).
  * Fixed standing costs (model weights in S3, ECR storage, alarms) are split equally between users.
* **Cost of this feature:** a few hundred bytes of logs per job (CloudWatch Logs ingestion is $0.50/GB). Per-user custom metrics (Embedded Metric Format with a `sub` dimension) are not used because each costs about $0.30/month.

---

## 4. Application Logic & Worker Lifecycle

The worker handles one job at a time. Spot withdrawal is a **release, not a failure**: no failure marker is written.

* **With the 2-minute warning** (IMDS `spot/instance-action`, or SIGTERM from ECS via Spot draining): the worker stops generating and calls `ChangeMessageVisibility(VisibilityTimeout=0)`. The job is retried immediately on a replacement instance.
* **Without warning** (hardware failure, instance vanishes): the worker cannot act. The message reappears after the visibility timeout (about 3 minutes, since the heartbeat stops). The job is delayed, not lost.
* **Permanent job error** (e.g., invalid prompt, corrupt image or video, no face found in an avatar image, expired input): the worker writes `clips/{sub}/{job_id}.failed` with a JSON reason (`{"code": "invalid_input", "field", "message"}`) and deletes the message (fast path).
* **Unexpected error or hard crash:** the message is released or reappears and is retried. After 5 receives it moves to the DLQ and the failure-marker Lambda writes the `.failed` marker (section 3D).

```python
# Pseudo-code for the containerized worker (illustrative only)
import signal
import time
import boto3

sqs = boto3.client("sqs")
s3 = boto3.client("s3")

QUEUE_URL = ...       # from task environment
CLIPS_BUCKET = ...    # from task environment

stopping = False
signal.signal(signal.SIGTERM, lambda *_: globals().update(stopping=True))

def poll_queue():
    while not stopping:
        resp = sqs.receive_message(
            QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        if "Messages" not in resp:
            continue

        msg = resp["Messages"][0]
        handle = msg["ReceiptHandle"]
        job = parse_payload(msg["Body"])  # job_id, sub, prompt, params, inputs
        prefix = f"clips/{job['sub']}/{job['job_id']}"
        input_prefix = f"inputs/{job['sub']}/{job['job_id']}/"
        started = time.monotonic()
        outcome = "released"

        try:
            protect_task()  # ECS UpdateTaskProtection: no scale-in mid-job
            # 1. Generate video using local GPU model weights. A heartbeat extends
            #    visibility every minute; inference aborts early if `stopping`
            #    or a Spot notice appears.
            #    Inputs (files per manifest slot) are fetched from S3 and re-checked against the
            #    manifest; a bad or expired input raises PermanentJobError.
            local_inputs = fetch_inputs(s3, CLIPS_BUCKET, input_prefix)
            output_path = run_inference(
                job["prompt"], local_inputs, job["params"],
                should_abort=lambda: stopping or spot_notice(),
            )

            # 2. Upload clip to S3 under the caller's prefix
            s3.upload_file(output_path, CLIPS_BUCKET, f"{prefix}.mp4")

            # 3. Done: the API will find the clip via S3 HEAD. Delete the message,
            #    then the inputs (kept until now so a retry can re-read them).
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=handle)
            delete_inputs(s3, CLIPS_BUCKET, input_prefix)
            outcome = "ok"
        except Aborted:
            # Spot warning or SIGTERM: release the job immediately for retry
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=handle, VisibilityTimeout=0)
        except PermanentJobError as err:
            # Retrying cannot help: mark failed (with a sanitised reason) and drop the message
            s3.put_object(Bucket=CLIPS_BUCKET, Key=f"{prefix}.failed", Body=err.to_json())
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=handle)
            outcome = "failed"
        except Exception:
            # Unknown error: release for a quick retry; the DLQ catches repeats
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=handle, VisibilityTimeout=0)
        finally:
            unprotect_task()
            # Per-user cost attribution (section 3H): one JSON line per attempt
            log_usage(
                sub=job["sub"], job_id=job["job_id"],
                attempt=int(msg["Attributes"]["ApproximateReceiveCount"]),
                instance_type=instance_type(),  # IMDS meta-data/instance-type
                gpu_seconds=round(time.monotonic() - started, 1),
                outcome=outcome,
            )
```

---

## 5. Decisions, Pre-Deploy Checklist and Open Items

Decided:

* Region `us-west-2`; FastAPI backend; public subnets with public IPs and an Internet Gateway (no NAT).
* Result delivery: the UI polls the API, which checks S3 and returns a fresh pre-signed URL. No results queue.
* Clip deletion: API deletes after download confirmation; 1-day lifecycle is the backstop.
* Two S3 buckets (models, clips). User inputs go in the clips bucket under `inputs/`.
* One model per deployment. The front end is driven by a capability manifest (`model_manifest.json`, section 3B), so text-to-video, image-to-video, video-to-video and avatar models share one UI.
* Job visibility without a database: `.pending` marker written at submit, `GET /jobs` lists the user's prefix.
* Front end: vanilla JS, no build step; testable with no backend through a mock `ApiClient`.
* Model weights always live in S3 and are copied to the EC2 instance store at boot. No EBS for weights.
* FastAPI runs as one Lambda behind an ASGI adapter (Mangum).
* Pre-signed GET URL expiry: 6 hours, best-effort (see 3C).
* Failures: DLQ (5 receives) + DLQ-triggered marker Lambda + worker fast path. Spot withdrawal is a release, not a failure.
* Inference: Hugging Face `diffusers` (`WanPipeline`), not the original Wan repo code.
* Worker instances: `g4dn.2xlarge` / `g5.2xlarge` (32 GB RAM needed by the model). ASG Max Size 2. DLQ alarm → SNS email; AWS Budgets email alert.
* Worker image: built and pushed to ECR by hand with a script (`scripts/build_push.sh`). No CI deploy for v1.
* CI: GitHub Actions runs `pytest` and `terraform fmt -check` / `terraform validate` on every push. It has no AWS credentials.
* Front-end deploy: Terraform uploads `frontend/` to the web app bucket (`aws_s3_object`) and writes `config.json` from its own outputs (API URL, Cognito domain and client ID, CloudFront URL), so one `terraform apply` deploys everything. CloudFront caches for a short time (default TTL about 5 minutes) instead of needing invalidations.
* Cognito users are created in the console. No custom domain: default `*.cloudfront.net` plus a Cognito prefix domain.
* Per-user cost: structured usage logs (worker GPU seconds, API download bytes) in CloudWatch Logs with 90-day retention, summed per `sub` by a saved Logs Insights query and allocated against the monthly bill (see 3H). No database.

Pre-deploy checklist:

* **Test doubles:** moto for AWS calls in pytest. Spike first: confirm moto supports pre-signed POST and `ChangeMessageVisibility`. If not, use a hand-written fake for only those calls.
* **Worker CUDA base image:** chosen to match the NVIDIA driver on the deployed ECS GPU AMI, and pinned by digest. Re-check the match whenever the AMI changes.
* **JWT verification:** the API Gateway JWT authorizer verifies the token. The Lambda takes `sub` from the claims and does not re-verify. The Lambda must have no public function URL, and its resource policy allows only API Gateway. A test asserts that a request with no claims is rejected.
* **Single environment:** one deployment, no dev or staging stack. Risky changes are planned windows. Add a `dev` stack later if needed.
* **Terraform state bootstrap:** `terraform/bootstrap/` with local state, applied once by hand. Creates the state bucket (versioning on, public access blocked) and the DynamoDB lock table. The main config uses them as remote state.
* **Request a Spot service-quota increase** for "All G and VT Spot Instance Requests" in `us-west-2`. New accounts often have a limit of 0. `g4dn.2xlarge` and `g5.2xlarge` need 8 vCPU each, so Max Size 2 needs **16 vCPU**. The GPU spike also needs "Running On-Demand G and VT instances" quota if it runs on-demand.
* **AWS Budgets alert:** a monthly cost budget (free for the first two budgets) with an email alert to the owner, in Terraform.
* Model for v1: Wan 2.1 T2V 1.3B in Diffusers format (`Wan-AI/Wan2.1-T2V-1.3B-Diffusers`, Hugging Face commit `0fad780a534b6463e45facd96134c9f345acfa5b`, Apache-2.0, about 29 GB), text-to-video, no input slots, 480p (832x480) output, on `g4dn.2xlarge` (16 GB T4, 32 GB RAM) with `enable_model_cpu_offload`. In fp16 the components need about 13.4 GB (text encoder, with `wo` in fp32) + 2.8 GB (transformer) + 0.5 GB (VAE in fp32), about 16.7 GB. That fits neither the T4's usable VRAM nor the 16 GB RAM of the `.xlarge` sizes; with offload, each component moves to the GPU only while it runs, and 32 GB RAM holds all of them. The `.2xlarge` costs about $0.03–0.05/hr more on Spot than the `.xlarge` (about half a cent per clip). The worker runs it with `diffusers.WanPipeline`, with the `diffusers` version pinned (a NaN regression was reported after 0.35, diffusers issue #12612). The spike measures T4 generation time; an external T4 benchmark reports about 330 s for 33 frames and about 510 s for 81 frames at 30 steps. Precision: transformer and text encoder in fp16 (the T4 has no bf16), VAE in fp32. Transformers keeps the UMT5 `wo` layers in fp32 when loading in fp16, which avoids the known UMT5 fp16 overflow. Switch the transformer to fp32 only if the spike shows black or distorted frames. Frames: 16 fps and 4n+1 frames, so 3 s is 49 frames and 5 s is 81 frames. Image-to-video and avatar shapes follow later as manifest changes; the 14B model would need `g5.2xlarge` (24 GB VRAM) or larger.
* Write `model_manifest.json` for the chosen model: `prompt` required, no input slots, and three user params: `seed` (int, optional), `duration_seconds` (enum, fixed list: 3 or 5; longer clips wait until the spike measures T4 speed). Resolution is fixed at 480p and not exposed.

Open:

* None at present.

---

## 6. Terraform Best Practices To Be Applied

### 1. Remote State Management & Locking

* Never keep `.tfstate` local past initial testing.
* Store state in a dedicated **Amazon S3 bucket** with a **DynamoDB table** for locking, to prevent conflicting concurrent applies.

### 2. Clean Directory Structure & Separation of Concerns

Do not use a single large `main.tf`. Organize by service domain, under `terraform/`:

* `backend.tf`: remote S3 backend and DynamoDB lock.
* `provider.tf`: pins AWS provider and Terraform core versions.
* `vpc.tf`: VPC, public subnets, Internet Gateway, route table, S3 Gateway Endpoint.
* `cognito.tf`: User Pool and App Client.
* `cloudfront.tf`: web app bucket and CloudFront distribution.
* `api.tf`: API Gateway HTTP API, JWT authorizer, API Lambda.
* `storage.tf`: S3 buckets (models, clips) and lifecycle configuration.
* `sqs.tf`: SQS queue, DLQ and access policies.
* `failure.tf`: failure-marker Lambda and its DLQ trigger.
* `ecr.tf`: ECR repository.
* `ecs.tf`: ECS cluster, task definition, service, capacity provider.
* `compute.tf`: launch template and Auto Scaling Group.
* `scaling.tf`: CloudWatch alarms and scaling policies.
* `iam.tf`: roles and policies (section 3G).
* `variables.tf` & `outputs.tf`: inputs and outputs (bucket names, VPC IDs, etc.).

`model_manifest.json` sits outside `terraform/` and is read by it (`file()` + `jsonencode`) to set the API Lambda and ECS task environment (section 3B). The static app lives in `frontend/` and is uploaded to the web app bucket.

### 3. Provider Version Locking

* Pin the AWS provider (e.g., `version = "~> 5.0"`) in `provider.tf`. Major provider updates can introduce breaking changes.

### 4. Consistent Tagging Strategy

* Define default tags at the provider level or through local variables (e.g., `Project = "AIVideoGen"`, `ManagedBy = "Terraform"`). Tags make ownership tracking and auditing simple.

### 5. Secure Variable Management

* Never hardcode sensitive values (credentials, personal tokens, any future secrets) in `.tf` files. The Cognito app client is public and has no secret.
* Use git-ignored `.tfvars` files or environment variables (`TF_VAR_...`).
