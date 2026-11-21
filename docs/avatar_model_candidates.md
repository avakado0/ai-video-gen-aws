# Avatar model candidates (photo + audio, talking head)

Step A of `_plans/04_avatar_model.md`. Read 2026-11-21 from each project's own GitHub and Hugging Face pages (README, LICENSE, model card). "Not stated" means the page does not say; nothing here is measured by us. The Kaggle spike (step B) gives the real numbers.

Owner's filter (decided 2026-11-21): one photo + one audio file in, talking head out, permissive licence (MIT or Apache-2.0, weights included), clips up to 30 s of audio.

## Summary

| Model | Code licence | Weights licence | Photo + audio | Stated memory | Last visible activity | Verdict against the filter |
|---|---|---|---|---|---|---|
| SadTalker | Apache-2.0 (with unnamed third-party exceptions) | not stated separately | yes | not stated | changelog 2023-06 | licence ok on paper; very old stack |
| Hallo2 | MIT | tag `mit`, but it is a bundle of other models | yes (WAV, English only) | not stated; tested on A100 | news 2025-01 | code ok; bundled components need a licence check |
| EchoMimic V2 | Apache-2.0 | not stated; README says "academic research" | yes, pose input unclear | tested on V100 16 GB, RTX 4090 24 GB, A100 | README 2025-01 | weights unlicensed; fails a strict reading |
| Sonic | CC BY-NC-SA 4.0 (non-commercial) | afl-3.0 tag, conflicts with code | yes | "tested on a single 32G GPU" | 2026-01 (README only) | fails: non-commercial |

Not candidates: MuseTalk and LatentSync (need a video, not a photo), Wav2Lip (commercial use restricted). Heavy newer models (InfiniteTalk, LongCat-Video-Avatar 1.5, daVinci-MagiHuman 15B) were seen only in secondary sources, not read here; they are Wan-class and likely need far more than 24 GB.

## Per model

### SadTalker (OpenTalker/SadTalker)
- Licence: Apache-2.0; the file says "except for the third-party components listed below", and the list was not in the fetched text. README says the non-commercial restriction was removed. Weights licence not stated separately.
- Input: one portrait image (or a video) plus audio. Output: face-render checkpoints at 256 and 512; optional GFPGAN enhancer.
- Stack: Python 3.8, PyTorch 1.12.1 with CUDA 11.3, ffmpeg. That is very far from our image (Python 3.12, torch 2.8). Using it means a separate, old environment or porting.
- Maintenance: last changelog entry 2023-06-12; 632 open issues. Memory and speed not stated.

### Hallo2 (fudan-generative-vision/hallo2)
- Licence: code MIT. HF model tag `mit`, no separate text. The pretrained bundle pulls in Stable Diffusion v1.5, AnimateDiff motion module, sd-vae-ft-mse, wav2vec2-base-960h, insightface models, mediapipe face landmarker, CodeFormer, RealESRGAN and Kim_Vocal_2, each with its own licence, none checked here. The optional high-resolution step is a modified CodeFormer under the S-Lab License 1.0 (not read). From my own recollection, not from the pages: insightface's pretrained models are non-commercial, and CodeFormer's licence is non-commercial. Verify both before relying on Hallo2.
- Input: one square, front-facing image (face 50 to 70% of the frame, rotation under 30 degrees) plus WAV audio, English only. Long audio is supported (a showcase says up to one hour). Base output resolution not stated; the upscaler gives 2x or 4x.
- Stack: Python 3.10, torch 2.2.2 with CUDA 11.8, ffmpeg, Ubuntu 20.04 or 22.04. Tested on A100. No minimum VRAM or speed stated.
- Maintenance: news items 2024-10 and 2025-01; 13 commits shown; inference speedup marked TBD.

### EchoMimic V2 (antgroup/echomimic_v2)
- Licence: code Apache-2.0. Weights: no licence field on the Hugging Face card. README disclaimer: intended for academic research. Other bundled models (sd-vae-ft-mse, sd-image-variations-diffusers, Whisper tiny) keep their own terms, unchecked.
- Input: reference image plus audio; whether a pose sequence is mandatory is not stated (a pose encoder ships with the weights).
- Stack: Python 3.10, torch 2.5.1 (cu124), xformers 0.0.28, a nightly torchao build, ffmpeg-static 4.4, Git LFS.
- Memory and speed: tested on A100 80 GB, RTX 4090 24 GB, V100 16 GB; no minimum stated. An accelerated version is quoted at about 50 s per 120 frames on an A100 (7 min before).
- Maintenance: README entries 2025-01 (accelerated version), 2025-08 (a different project, EchoMimic V3). 76 open issues.

### Sonic (jixiaozhong/Sonic)
- Licence: code CC BY-NC-SA 4.0, README says non-commercial. Weights tagged afl-3.0, which conflicts. Depends on Stable Video Diffusion XT (its own community licence). Fails the permissive filter.
- Input: one image plus audio; "tested on a single 32G GPU", which a T4 and a 24 GB g5 do not have.
- Maintenance: last commit 2026-01 but README-only; code changes in 2025.

## What this means
- Every photo-based model here has a licence caveat once weights and bundled components are counted. Strictly applying "permissive including weights" may leave no model, or only SadTalker on a 2023 stack.
- None of the three photo + audio candidates stated a memory figure we can plan around. Only the spike answers that.
- All three are old (2023 to early 2025). Newer, larger Wan-class avatar models exist but were not read here.

## Decision (owner, 2026-11-21)
The plan is to make money from the videos, so the strict rule applies: code, weights and every bundled or base model need a clear commercial grant. The three older photo-driven candidates above do not pass it cleanly. The search moves to newer models (see the second table below once filled in) and to a paid API as a fallback.
