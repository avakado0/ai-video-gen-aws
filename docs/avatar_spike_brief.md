# Avatar spike brief: LongCat-Video-Avatar 1.6

Prepared 2026-11-21 for the owner to read and think through. Nothing here has been run, and no AWS resource was created for it. Plan: `_plans/04_avatar_model.md`. Facts and sources: `docs/avatar_model_candidates.md`.

## 1. Where we stand

- The Wan 2.1 1.3B text-to-video trial proved the pipeline (cold start 6 min 17 s, no errors) but its picture quality was rejected. Wan is parked, not deleted.
- The product is now a **talking avatar**: a friend uploads one photo and a speech recording and gets a lip-synced clip, up to 30 s of audio.
- The owner plans to **earn money** from the videos, so every part must have a clear commercial-use licence.
- Chosen model: **LongCat-Video-Avatar 1.6** (Meituan). Code and weights MIT, audio encoder Whisper-large-v3 Apache-2.0. About 14 billion parameters.
- One part has no stated licence: the voice separator `Kim_Vocal_2.onnx`. Decision: our worker bypasses it and never ships it. The spike tests whether lip-sync still holds.

## 2. What the spike must answer

| Question | Why it matters | How we find out |
|---|---|---|
| Does it run on one 48 GB GPU, and with what settings (bf16, int8, encoders moved off the GPU)? | Decides the instance type and the cost per hour | Run it and read peak memory |
| How long does one 3.7 s segment take (8 steps)? A 30 s clip is about 9 segments. | Decides cost per clip, queue time, and the 30 s limit | Time one segment, then a 10 s clip, then 30 s |
| Does lip-sync and face quality hold without the voice separator, on clean speech? | Licence decision depends on it | Same photo and audio with and without the separator (the with-separator run is for comparison only, nothing shipped) |
| Does `--use_int8` hurt quality visibly? | int8 halves the transformer to 15.9 GB | Same inputs, compare by eye |
| Would the owner show a friend the result? | The only quality bar that matters | Owner watches |
| Cold start with about 63 GB of weights | The user waits this long after idle | Measure on the first real deployment, later |

## 3. What we need before running anything

- **Weights in S3** (only the files the model reads, about 63 GB in bf16 or about 47 GB with int8), under `models/` in the existing models bucket:
  - from `meituan-longcat/LongCat-Video-Avatar-1.6`: `base_model/` (31.7 GB), `base_model_int8/` (15.9 GB, optional), `loraA/` and `loraB/` (2.5 GB each), `scheduler/`, and `whisper-large-v3/` **only** `model.safetensors` plus its small config and tokenizer files (the folder lists 24.7 GB because of duplicate formats). Skip `vocal_separator/`.
  - from `meituan-longcat/LongCat-Video`: `text_encoder/` (22.7 GB), `vae/`, `tokenizer/`, `scheduler/`. Skip `dit/` (54 GB, not used by the avatar script) and `lora/`.
  - Method: the existing runbook, `docs/huggingFace_to_s3_instructions.md` (a temporary instance downloads from Hugging Face and uploads to S3, then is deleted). Draft command shape, to be verified before use: `hf download <repo> --include "<folder>/*"`. Last time `hf download` needed one `--exclude` per pattern; expect the same for `--include`.
- **A test photo and a clean speech recording** (10 s and 30 s). Use your own face and voice or a consenting person's. No real friend's photo without their consent.
- **The code:** the LongCat-Video repository (MIT), cloned on the GPU instance. Python 3.10, torch 2.6.0 (cu124), flash-attn 2.7.4.post1, `audio-separator` 0.30.2 (the demo imports it even if we bypass it; the spike may need a small local edit to skip the separator call).
- **A GPU instance:** `g6e.2xlarge` (8 vCPU, 64 GiB RAM, one L40S 48 GB, 450 GB local NVMe), launched **by hand in zone us-west-2a**, where Spot was $0.99 per hour on 2026-11-21 (about $2.21 in zones b, c and d, close to on-demand). It should count against the existing 8 vCPU "All G and VT Spot Instance Requests" quota; confirm in Service Quotas or by trying the launch.

## 4. Draft sequence and cost (my estimates, not measured)

| Step | Who | Cost |
|---|---|---|
| 1. Weights: temporary instance downloads about 63 GB from Hugging Face to the S3 models bucket, then is deleted | owner runs, Claude writes the commands | a few hours of a small instance (cents to about a dollar), plus S3 storage of about $1.5 per month for 63 GB |
| 2. Launch one `g6e.2xlarge` in zone a, copy weights from S3 to local disk | owner | about $1 per hour from launch |
| 3. Install the environment, run the README demo on 1 GPU with one segment, record memory and time | owner, Claude interprets | in the same hour |
| 4. Try `--use_int8`, then a 10 s clip, then a 30 s clip | owner | 1 to 2 more hours |
| 5. Compare with and without the voice separator, once | owner | short |
| 6. Terminate the instance, check nothing is running | owner | stops the cost |

Total, rough: **$3 to $5 of credit**, plus about $1.5 per month for the extra weights in S3. The spike should not run unattended. Use the stop commands in `docs/runbook_stop_gpu.md` for anything started through the ASG; a hand-launched instance is terminated in the console or with `aws ec2 terminate-instances`.

## 5. Things I would like the owner to decide or think about

1. **Go or no-go on the spike spend** of about $3 to $5, and whether to do it now or after the other pending work.
2. **Quality bar for the 30 s limit:** if one 3.7 s segment takes, say, 1 minute, a 30 s clip takes about 9 minutes of GPU. Is that an acceptable wait and cost per clip? What would you charge per clip, so we know the cost ceiling?
3. **Instance strategy later.** On Spot, g6e is cheap only in zone a ($0.99 per hour); elsewhere about $2.21. Our subnets cover zones a and b. Options at deployment time: restrict the ASG to zone a (cheap, but less capacity and no fallback), accept the higher price, or look at another instance type if the spike shows int8 fits 24 GB (unlikely).
4. **Voice separator bypass.** If clean speech works, we drop it. If lip-sync is poor on noisy uploads, we would need a separately licensed denoiser, or to ask users for clean audio.
5. **Consent and misuse.** A face-and-voice tool can impersonate people. Before friends use it: a plain consent notice in the app, uploads deleted after the job (already the design), and a note in the README. Platforms (YouTube, TikTok) require labelling realistic AI-made people, which matters for monetisation.
6. **Fallback if LongCat fails the spike:** InfiniteTalk (Apache-2.0, Aug 2025, Wan 14B base), which needs similar hardware, or a paid avatar API instead of self-hosting. Both cost more effort or money.

## 6. What happens after the spike

If the owner approves the result: write ADR 0005 (the model choice), switch the manifest to `face_image` plus `audio` (30 s limit), write `worker/avatar.py` test first (audio track in the output, no-face error, length limit), exercise the upload path on the real site for the first time, rebuild the image (the logging change and the `huggingface-hub` pin are already committed), and run the first real avatar job on AWS. See `_plans/04_avatar_model.md` steps C to H.
