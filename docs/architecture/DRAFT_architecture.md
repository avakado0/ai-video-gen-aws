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
       │ (HTTPS + JWT)
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

* **Cognito User Pool (`aws_cognito_user_pool`) + App Client:** User creation restricted to administrators (`admin_create_user_config`), so only the owner, a friend, and whitelisted users (about 5) can sign in. The App Client is a **public client (no client secret)** using authorization code + PKCE, because it runs in a browser. Issues a JWT on login.
* **Web app:** Static site in a private S3 bucket served through CloudFront (origin access control). The login screen uses the User Pool.
* **API:** API Gateway HTTP API with a Cognito JWT authorizer, in front of a single Lambda that runs the whole FastAPI app through an ASGI adapter (Mangum); API Gateway routes every path to it. A browser cannot write to SQS directly, so the API is the only entry point.
  * The caller's `sub` always comes from the **verified JWT claims**, never from the request body. `job_id` is validated as a UUID before it is used in an S3 key. Otherwise a user could read or delete another user's `clips/{sub}/` prefix.
  * `POST /jobs`: creates a `job_id`, sends the generation request to SQS (includes the caller's `sub`).
  * `GET /jobs/{job_id}`: checks `clips/{sub}/{job_id}.mp4` and `clips/{sub}/{job_id}.failed` with HEAD. Returns `ready` plus a fresh pre-signed GET URL (6-hour expiry, see 3C), `failed`, or `pending`.
  * `DELETE /jobs/{job_id}`: called after the browser confirms the download. Deletes the clip.
  * CORS is enabled on the HTTP API for the CloudFront origin (and on the clips bucket if downloads use `fetch`).

### C. Storage & Data Management (`terraform/storage.tf`)

* **Model Weights S3 Bucket (`aws_s3_bucket`):**
  * Storage Class: S3 Standard.
  * Key layout: `models/<model-name>/...`. The runbook's `S3_PREFIX` and the `user_data` copy path must both use this prefix.
  * Contents: base model weights, copied once from Hugging Face (see `docs/huggingFace_to_s3_instructions.md`). The pipeline never writes to it.
* **Generated Clips S3 Bucket (`aws_s3_bucket`):**
  * Key layout: `clips/{sub}/{job_id}.mp4`, and `clips/{sub}/{job_id}.failed` (empty marker for failed jobs).
  * Lifecycle (`aws_s3_bucket_lifecycle_configuration`): **expire after 1 day** (the S3 minimum). Cheaper storage classes need 30+ days, so there is no transition rule. S3 runs lifecycle rules asynchronously, so removal can lag by up to a day. This is a backstop: the clip is normally deleted by the API as soon as the user's download is confirmed.
  * Access: private. Videos are exposed only through **pre-signed URLs generated by the API Lambda at download time**. The worker does not generate URLs: URLs signed with EC2 instance-role credentials die when those temporary credentials rotate (hours), so a long-lived URL is not possible from the worker.
  * URL expiry: `ExpiresIn=21600` (6 hours). This is best-effort: the Lambda role's temporary credentials can expire sooner, and the URL stops working with them. The API mints a fresh URL on every `GET /jobs/{job_id}`, so the client simply re-requests if a download returns 403.

### D. Messaging & Queueing (`terraform/sqs.tf`, `failure.tf`)

* **Amazon SQS Queue (`aws_sqs_queue`):**
  * Standard queue for task buffering.
  * Retention Period: 4 days.
  * Visibility Timeout: **3 minutes**, with a worker heartbeat that extends it (`ChangeMessageVisibility`) every minute while a job runs. A live worker keeps its job. A dead worker frees it within about 3 minutes.
  * Redrive policy: `maxReceiveCount = 5`, then the message moves to the DLQ. Every receive counts, including retries after a Spot release, so five in a row on one healthy job is needed to dead-letter it by mistake.
  * No results queue. The UI learns a clip is ready or failed by polling the API (section 3B).
* **Dead-letter queue (`aws_sqs_queue`):** Receives jobs that failed 5 times. A CloudWatch alarm watches it so failures are visible.
* **Failure-marker Lambda:** Triggered by the DLQ (SQS event source mapping, $0 when idle). Reads `sub` and `job_id` from the message body and writes `clips/{sub}/{job_id}.failed`. This covers deaths the worker could not report (out of memory, crash, Spot loss without warning).

### E. Compute & Orchestration (`terraform/ecr.tf`, `ecs.tf`, `compute.tf`)

* **Worker:** a Docker container on a Spot EC2 GPU instance, started by ECS. It is not a Lambda.
* **Amazon ECR (`aws_ecr_repository`):** Stores the Dockerized video generation worker image.
* **ECS Cluster, Task Definition, Service (`aws_ecs_cluster`, `aws_ecs_task_definition`, `aws_ecs_service`):**
  * Launch Type: **EC2** (required for GPU support). Capacity comes from a capacity provider tied to the ASG.
  * Task Parameters: requests one GPU, bind-mounts the instance-store path `/data` into the container.
  * `StopTimeout`: 120 seconds. This is not enough to finish a job, so the worker aborts and releases its message on SIGTERM (section 4).
* **Launch Template (`aws_launch_template`):**
  * AMI: official **Amazon ECS-optimized GPU AMI** (NVIDIA drivers and Docker runtime preinstalled).
  * Instance types: diversified (`g4dn.xlarge`, `g5.xlarge`).
  * `ecs.config`: `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` so ECS drains the instance and sends SIGTERM on a Spot interruption notice.
  * Metadata options: `http_put_response_hop_limit = 2` so the worker container can reach IMDS (to poll for the Spot notice).
  * `user_data`: on every boot, formats and mounts the local NVMe instance store at `/data` (it is not mounted automatically and is wiped on stop/terminate), copies `models/<model-name>/` from the S3 models bucket via the Gateway Endpoint, then starts the ECS agent. Weights always live in S3 and are never kept on EBS.
  * Size cap: weights plus working space must fit the instance store: `g4dn.xlarge` about 125 GB, `g5.xlarge` about 250 GB. Treat 125 GB as the limit while `g4dn` is in the mix. Larger weights mean changing the instance types, not adding EBS.
  * Cold start: every Spot boot re-copies the weights (free, but it adds time roughly equal to weights size divided by S3 throughput) and pulls the multi-GB worker image from ECR.
* **Auto Scaling Group (`aws_autoscaling_group`):**
  * Min Size `0`, Max Size per concurrency needs (e.g., `2` or `5`), two AZs.
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
* **ECS task role (worker):** `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility` on the queue; `s3:PutObject` on `clips/*` in the clips bucket; `ecs:UpdateTaskProtection`.
* **ECS task execution role:** pull from ECR, write CloudWatch Logs.
* **API Lambda role:** `sqs:SendMessage`; on the clips bucket `s3:GetObject`, `s3:DeleteObject`, and **`s3:ListBucket`**. Without `ListBucket`, a HEAD on a missing key returns 403 instead of 404, and "pending" would look like an error.
* **Failure-marker Lambda role:** `sqs:ReceiveMessage`/`DeleteMessage` on the DLQ; `s3:PutObject` on `clips/*`.

---

## 4. Application Logic & Worker Lifecycle

The worker handles one job at a time. Spot withdrawal is a **release, not a failure**: no failure marker is written.

* **With the 2-minute warning** (IMDS `spot/instance-action`, or SIGTERM from ECS via Spot draining): the worker stops generating and calls `ChangeMessageVisibility(VisibilityTimeout=0)`. The job is retried immediately on a replacement instance.
* **Without warning** (hardware failure, instance vanishes): the worker cannot act. The message reappears after the visibility timeout (about 3 minutes, since the heartbeat stops). The job is delayed, not lost.
* **Permanent job error** (e.g., invalid prompt): the worker writes `clips/{sub}/{job_id}.failed` and deletes the message (fast path).
* **Unexpected error or hard crash:** the message is released or reappears and is retried. After 5 receives it moves to the DLQ and the failure-marker Lambda writes the `.failed` marker (section 3D).

```python
# Pseudo-code for the containerized worker (illustrative only)
import signal
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
            QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20
        )
        if "Messages" not in resp:
            continue

        msg = resp["Messages"][0]
        handle = msg["ReceiptHandle"]
        job = parse_payload(msg["Body"])  # job_id, sub, prompt
        prefix = f"clips/{job['sub']}/{job['job_id']}"

        try:
            protect_task()  # ECS UpdateTaskProtection: no scale-in mid-job
            # 1. Generate video using local GPU model weights. A heartbeat extends
            #    visibility every minute; inference aborts early if `stopping`
            #    or a Spot notice appears.
            output_path = run_inference(job["prompt"], should_abort=lambda: stopping or spot_notice())

            # 2. Upload clip to S3 under the caller's prefix
            s3.upload_file(output_path, CLIPS_BUCKET, f"{prefix}.mp4")

            # 3. Done: the API will find the clip via S3 HEAD. Delete the message.
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=handle)
        except Aborted:
            # Spot warning or SIGTERM: release the job immediately for retry
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=handle, VisibilityTimeout=0)
        except PermanentJobError:
            # Retrying cannot help: mark failed and drop the message
            s3.put_object(Bucket=CLIPS_BUCKET, Key=f"{prefix}.failed", Body=b"")
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=handle)
        except Exception:
            # Unknown error: release for a quick retry; the DLQ catches repeats
            sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=handle, VisibilityTimeout=0)
        finally:
            unprotect_task()
```

---

## 5. Decisions, Pre-Deploy Checklist and Open Items

Decided:

* Region `us-west-2`; FastAPI backend; public subnets with public IPs and an Internet Gateway (no NAT).
* Result delivery: the UI polls the API, which checks S3 and returns a fresh pre-signed URL. No results queue.
* Clip deletion: API deletes after download confirmation; 1-day lifecycle is the backstop.
* Two S3 buckets (models, clips).
* Model weights always live in S3 and are copied to the EC2 instance store at boot. No EBS for weights.
* FastAPI runs as one Lambda behind an ASGI adapter (Mangum).
* Pre-signed GET URL expiry: 6 hours, best-effort (see 3C).
* Failures: DLQ (5 receives) + DLQ-triggered marker Lambda + worker fast path. Spot withdrawal is a release, not a failure.

Pre-deploy checklist:

* **Request a Spot service-quota increase** for "All G and VT Spot Instance Requests" in `us-west-2`. New accounts often have a limit of 0. `g4dn.xlarge` needs 4 vCPU each.
* Choose the model and confirm it fits the 125 GB instance-store cap.

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

### 3. Provider Version Locking

* Pin the AWS provider (e.g., `version = "~> 5.0"`) in `provider.tf`. Major provider updates can introduce breaking changes.

### 4. Consistent Tagging Strategy

* Define default tags at the provider level or through local variables (e.g., `Project = "AIVideoGen"`, `ManagedBy = "Terraform"`). Tags make ownership tracking and auditing simple.

### 5. Secure Variable Management

* Never hardcode sensitive values (credentials, personal tokens, any future secrets) in `.tf` files. The Cognito app client is public and has no secret.
* Use git-ignored `.tfvars` files or environment variables (`TF_VAR_...`).
