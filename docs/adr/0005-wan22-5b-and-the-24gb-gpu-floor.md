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
- Found in trial 1 (2026-11-22): two memory limits bind on a g5.2xlarge. The GPU's 22.06 GiB usable ran out in the fp32 VAE decode (now bf16), and the container was OOM-killed at its 29 GiB RAM limit with the transformer, VAE and the 11.4 GB prompt encoder all in RAM (now the encoder is loaded per job straight to the GPU and dropped before denoising). VAE tiling must stay off: diffusers 0.35.2's tiled decode breaks the 2.2 VAE. Speed was 11.6 s per step, cold start about 9 min. Measured after both fixes: peak GPU 12.6 GiB and peak RAM 24.4 GiB for a `fast` 3 s clip, 420 GPU seconds. Still unmeasured: a 5 s clip, a second job in one process and image-to-video on a real GPU.
- Image input is the first real use of `POST /uploads` against real S3.
- Quality is not guaranteed. Plan 05's rule stands: only the owner's eyes decide, and settings are iterated on a clip, not on a spec.
