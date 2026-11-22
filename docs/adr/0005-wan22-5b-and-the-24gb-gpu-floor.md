# The product model is Wan 2.2 TI2V 5B, which sets a 24 GB GPU floor

Decided 2026-11-22 (plan 06). The deployed model changes from Wan 2.1 T2V 1.3B to Wan 2.2 TI2V 5B (`Wan-AI/Wan2.2-TI2V-5B-Diffusers`, Apache-2.0, ungated, about 34 GB), which does text-to-video and image-to-video. Because the 5B model needs a GPU with at least 24 GB, the fleet drops the `g4dn` (T4, 16 GB) and starts on `g5.2xlarge` only (A10G, 24 GB); `g6.2xlarge` (L4, 24 GB) is added after the first trial has shown the model works. The Wan 2.1 1.3B code stays in the repo as a parked path, one manifest file away.

## Why

- The owner judged the 1.3B output "usable, not perfect" after plan 05 had fixed its settings, and asked for the next step up. The 5B model is newer and larger, uses the same `WanPipeline` family and the pinned diffusers 0.35.2, and adds image-to-video through a start image.
- The 14B model (plan 05's phase D) would need far more VRAM and money. The 5B is the smaller step.
- The talking avatar of plan 04 needs audio input, which the 5B does not have. That stays a separate, later decision (one model per deployment).

## Rejected

- Keeping the g4dn in the fleet: the 5B in bf16 does not fit a 16 GB T4, and a T4 has no bf16.
- Keeping both models' weights in S3: every boot syncs the whole `models/` prefix, so both would be copied each time (about 63 GB). The owner chose to delete the 1.3B weights by hand, but only after the 5B trial is accepted. Rolling back to the 1.3B after that needs a fresh copy from Hugging Face (the runbook, a temporary EC2 instance).
- A 480p tier for the 5B: not until it is shown to hold up.

## Consequences

- Cost per hour goes up: g5.2xlarge Spot was $0.69 to $0.90 an hour on 2026-11-22 against $0.29 to $0.32 for the g4dn. Plan 07 cut the billed idle wait to about 5 minutes, which softens it. Cost per clip is unmeasured on an A10G.
- One instance type means no Spot diversity. If g5 capacity is short, jobs wait, until the g6 joins.
- Unmeasured, and what trial 1 must show (plan 06, step B4): speed per step, peak VRAM in the VAE decode at 1280x704x121 (VAE tiling is on as cover), whether the container's 29 GiB memory limit holds about 24 GB of weights in RAM (my arithmetic from the repo's file sizes: the transformer is stored in fp32, about 20 GB, so about 10 GB in bf16; text encoder 11.4 GB; VAE 2.8 GB), cold start with a 34 GB copy, and whether the image-to-video wiring works (read from the diffusers 0.35.2 source, not run).
- Image input is the first real use of `POST /uploads` against real S3.
- Quality is not guaranteed. Plan 05's rule stands: only the owner's eyes decide, and settings are iterated on a clip, not on a spec.
