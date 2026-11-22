# 04 Avatar model: switch the product from Wan 1.3B text-to-video to a talking-head avatar model

Canonical plan (approved 2026-11-21). Supersedes the Wan 1.3B text-to-video product; Wan is parked, not deleted. Housekeeping plan from earlier today is done (pin, ECR check, chmod).

## Context
The first AWS run (2026-11-21, `_effort_logs/2026-11-21_first_gpu_run_on_aws.md`) proved the pipeline: cold start 6 min 17 s, g5.2xlarge, fast clip 91.9 s, high clip 199.9 s, no errors. The picture quality of Wan 2.1 1.3B was rejected by the owner ("looks quite shitty"). The owner says Wan 1.3B was only a trial to see if the system works, and wants the product to be an avatar model from now on. Repo notes name no avatar model: they only say avatar is a future manifest shape (`CONTEXT.md` "Model shape", spec 3B, mock `frontend/js/mock/manifests/avatar.json`) and that the 14B Wan "would need g5.2xlarge or larger". This plan decides the product and how to choose the model.

## Decisions made with the owner (grill session 2026-11-21)
1. **Product:** talking avatar. A friend uploads one photo and a speech recording, and gets a lip-synced clip.
2. **Output type:** talking head, lip-synced (face and shoulders, near-still background). Not gestures, not full scenes.
3. **Audio source:** users upload audio. No text-to-speech, so one model per deployment still holds.
4. **Face input:** one photo (matches the mock manifest: `face_image` + `audio`). This rules out the video-in models (MuseTalk, LatentSync), which re-sync an existing video.
5. **Licence: commercial-safe, every part.** Decided 2026-11-21 after the owner said the plan is to make money from the videos. Code, weights and every bundled or base model must carry a clear grant that allows commercial use (MIT, Apache-2.0 or an equivalent written grant), and "not stated" does not pass. Rules out Wav2Lip, Sonic, EchoMimic V2 (weights unlicensed, "academic research"), and probably SadTalker and Hallo2 (see `docs/avatar_model_candidates.md`). Consent for faces and voices, and platform AI-labelling rules, matter once clips are published for money; the README says so. The older photo-driven models fail this rule, so the search moves to newer models, including Wan-based ones, and to a paid API as a fallback.
6. **Max clip length:** 30 s of audio (the mock allows 60 s; lower it).
7. **Model:** LongCat-Video-Avatar 1.6 (Meituan, MIT code and weights), chosen by the owner 2026-11-21 from the commercial-safe candidates (`docs/avatar_model_candidates.md`). It is about 14B parameters, so a free Kaggle T4 spike is not possible; the spike runs on AWS (see step B). Decided with it: our own `worker/avatar.py` calls the LongCat pipeline and bypasses the vocal separator `Kim_Vocal_2.onnx` (no licence found anywhere; it is not downloaded or shipped); the spike checks that lip-sync holds on raw clean speech. Open before building: whether one 48 GB GPU runs it (and with int8 or encoder offload), seconds per segment, quality, cost per clip. Spot reality 2026-11-21: g6e.2xlarge $0.99/h in zone a only, $2.21 elsewhere; our subnets are zones a and b.
8. **Wan 1.3B is parked:** keep code, tests and the 29 GB weights in S3 (about $0.65 a month, my estimate); only the deployed manifest and worker switch. (Update 2026-11-22, plan 06: the 1.3B weights are to be deleted from S3 after the 5B trial is accepted, because every boot copies the whole `models/` prefix; the code and tests stay.)

## Research so far (secondhand; verify against each project's own repo and LICENSE before any decision)
- Photo + audio, candidates: **SadTalker** (reported Apache-2.0, older, moderate quality), **Hallo2** (reported MIT, high quality, long audio, tested on A100s so memory is unknown). Others to check: EchoMimic V2, Sonic, Hallo3 (no data found yet).
- Heavy Wan-class avatar models (InfiniteTalk, LongCat-Video-Avatar 1.5, daVinci-MagiHuman 15B): best quality, probably far beyond the 24 GB g5 and the 8 vCPU quota. Out of scope unless the cheap tier fails.
- Not usable: Wav2Lip (commercial use restricted). MuseTalk and LatentSync need a video, not a photo.

## Steps
**A. Pin down the candidates (read-only, Claude).** For each of 3 to 4 models, read its repo and model card: licence (code and weights separately), inputs (photo and audio), peak VRAM and RAM, speed per second of audio, output resolution, weights size, dependencies (does it need a CUDA toolkit build the slim image lacks). Write `docs/avatar_model_candidates.md`. Drop any whose licence is not MIT or Apache-2.0 (weights included). Pick 2 or 3 for the spike.

**B. Paid spike on AWS (replaces the Kaggle spike; owner runs, Claude states the cost first).** Free steps first: (1) done 2026-11-21: Kim_Vocal_2 has no stated licence, so it is bypassed, not shipped (the spike tests that); (2) done: Spot prices looked up (g6e $0.99/h in zone a only); still to confirm: g6e counts against the 8 vCPU "All G and VT Spot Instance Requests" quota (check Service Quotas, or just try the launch). Then one short test: copy only the needed files (about 63 GB bf16, 47 GB with int8) from Hugging Face to the S3 models bucket (runbook `docs/huggingFace_to_s3_instructions.md`, one small temporary instance as before), then launch one `g6e.2xlarge` by hand (not through the ASG), run the README demo with `torchrun --nproc_per_node=1` on one photo and a 10 s audio, record peak VRAM, seconds per segment and quality, try `--use_int8`, then terminate the instance. Estimated cost: a few dollars (my estimate; Spot price not yet looked up). Record in `docs/avatar_spike_findings.md`. Exit test: one clip the owner would show a friend; 30 s of audio in a time and cost we can accept; fits one GPU.

**C. Decide.** Write an ADR (`docs/adr/0006-avatar-model.md` (0005 is taken by the Wan 2.2 5B decision, plan 06)): chosen model, why, what was rejected. Hard to reverse, surprising later, real trade-off: it passes the ADR test. Update the spec sections 3B, 5 (model bullet and manifest bullet), CONTEXT.md terms if any new, and the artifact. Pick the instance types: if the model fits 16 GB, restrict the ASG to g4dn.2xlarge (Spot about $0.29 to 0.32 an hour against g5 $0.71 to 0.87). Terraform change, owner applies.

**D. Build the avatar worker (TDD).**
- New `worker/avatar.py`: a `Generator` subclass (interface in `worker/generator.py`). Reads `inputs["face_image"][0]` and `inputs["audio"][0]` (paths from `ClipStore.fetch_inputs`), runs the model, returns the mp4.
- The mp4 must carry the audio track (Wan clips have none; `export_to_video` does not mux audio). Test first: output has an audio stream. Needs ffmpeg in the image (`imageio-ffmpeg` is already pinned).
- Permanent errors (the spec already lists "no face found in an avatar image"): `PermanentJobError` with code `invalid_input`, field `face_image`. Test with a fake model.
- Enforce the 30 s limit in the manifest (`max_seconds` on the audio slot); the worker re-checks (spec: API validates, worker re-checks).
- New `model_manifest.json` for the avatar (prompt `none`, `face_image`, `audio`, a few params), validated by `lambdas/api/manifest.py` and its tests. Keep the Wan manifest as `model_manifest.wan.json` (parked).
- Switch the generator by an env var or the manifest `model` name in `worker/__main__.py`; Wan code and tests stay.
- Dockerfile and `worker/requirements.txt`: new dependencies pinned exactly, built once with the logging change and the `huggingface-hub` pin already committed.

**E. Weights.** Copy the chosen model's weights from Hugging Face to the S3 models bucket with the existing runbook (`docs/huggingFace_to_s3_instructions.md`), under `models/<avatar-name>/`. Check the size against the 225 GB NVMe cap on g4dn.

**F. Exercise the upload path (new risk).** `POST /uploads` (pre-signed POST for image and audio) has only been tested with moto; real S3 enforcement of the size and type conditions is untested (artifact, "FastAPI Lambda" row). The avatar is the first real use. Test from the real site before the first GPU run. Check the front end's file inputs, the audio length check, and that inputs are deleted after the job.

**G. Rebuild, deploy, first avatar run (owner runs, Claude states the cost first).** `scripts/build_push.sh`; terraform plan and apply of the manifest and any instance-type change; raise both limits to 1 by hand (runbook in `docs/runbook_stop_gpu.md`); one 10 s clip first, then 30 s. Measure cold start, `gpu_seconds` per audio second, cost per clip, idle wait (G7, still unmeasured), then stop with the runbook.

**H. Docs.** Plan line 85 numbers into README, CLAUDE.md and the spec; update `_plans/01_build_order.md`; effort log; control-room artifact; draw.io diagram items (`human_todo/update_diagram.md`: add the avatar input path).

## Risks and open questions
- **Quality may still disappoint.** SadTalker-class output is stylised and can look uncanny; Hallo2-class is better but may need more memory than 16 GB. The Kaggle spike answers this for free before we build anything. If both fail, the fallback is a Wan-class avatar model on a bigger GPU: new quota request (8 vCPU allows one 8 vCPU instance only), higher cost, a separate decision.
- **Consent and privacy.** Friends upload faces and voices. Inputs are deleted after a job and by the 1-day lifecycle; clips are private. The README should say so. Do not use a real person's photo in tests without their consent.
- **Misuse.** A face-and-voice model can impersonate people. Friends-only app with Cognito admin-created users limits this; note it in the README.
- **Clip length vs Spot.** A 30 s job may run several minutes; a Spot interruption restarts it from the start (no resume), which the spec already accepts.
- **Instance choice** depends on the spike's memory numbers (see C).

## Verification
- Spike: measured numbers and a clip the owner approves, recorded in `docs/avatar_spike_findings.md`.
- Code: `.venv/bin/pytest -q` all green after each change (currently 280); tests for the audio track, the no-face error, the length limit, the manifest.
- End to end: one avatar clip from the real site on AWS, with sound, face recognisable, mouth in sync; then the stop runbook and a `describe-instances` check that nothing is running.

## Not doing now
No push, no `terraform apply`, no image build, no AWS calls by Claude. No GPU launch without stating the cost first.
