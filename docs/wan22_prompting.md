# Writing prompts for Wan 2.2 (TI2V 5B)

Tips for the prompt field of the deployed model (`wan2-2-ti2v-5b`, text and image to video, 1280x704 at 24 fps). A short version of the first four sections ships in `model_manifest.json` as `prompt_hint`; the UI shows it under the prompt field with a "Show prompt tips" switch (plan 09, spec 3B). This file is the long version.

**Status: guidance, not tested.** The rules come from the vendor's prompt guides. Our own trial 1 (2026-11-22) only proved that the three job kinds run, not that these rules give better clips. Treat them as a starting point, and note in `docs/wan_spike_findings.md` when a rule proves right or wrong.

## Structure

Subject, scene, motion, then aesthetic control (light, light quality, shot size, angle, lens, camera move), then style. If the clip has a named style, put it first. Aesthetic words work best as plain labels, and the model does not need mood or feeling words.

## Length

| Job | Length |
|---|---|
| Text to video | 60 to 200 characters |
| Image to video | 100 characters or fewer |

Our manifest allows 500 characters (`limits.prompt_max_chars`). Longer is allowed, but the guides do not recommend it.

## With a start image (image to video)

Write motion and camera only. The image already shows the subject, scene and light; repeating them weakens the motion. For a still camera write "fixed shot".

## Camera and light words (the official list, in English)

- Shot size: close-up, medium close-up, medium shot, medium full shot, wide shot, extreme wide shot.
- Angle: over-the-shoulder, low angle, high angle, top-down, aerial, eye level.
- Composition: center, left-weighted, right-weighted, symmetrical.
- Light: soft light, hard light, side light, top light, rim light, backlight.
- Camera move: push in, pull out, pan left, tilt up, orbit, fixed shot.

If the prompt already says how the camera moves, do not add an angle label as well.

## What the model can't do

- One shot per clip (no multi-shot or scene cuts).
- No sound: do not write dialogue, sound effects or music.
- No reference tags (no "image 1" style references). The start image is the only reference.

## Negative prompt

The `negative_prompt` param is a separate field, so unwanted things go there, not into the prompt. If the user leaves it empty, the worker uses Wan's default (`DEFAULT_NEGATIVE_PROMPT` in `worker/wan.py`). The vendor's standard list, as Chinese originally, in English: oversaturated colours, overexposed, static, blurry details, subtitles, style, artwork, painting, still frame, overall grey, worst quality, low quality, JPEG artefacts, ugly, incomplete, extra fingers, badly drawn hands, badly drawn faces, deformed, disfigured, malformed limbs, fused fingers, motionless picture, cluttered background, three legs, many people in the background, walking backwards.

Not relevant to us: the vendor warns that the negative prompt has no effect with a speed-up (distillation) LoRA at CFG 1, and that such a LoRA flattens motion. We use no LoRA; the worker runs guidance 5.0.

## Source

Adapted from the `wan-2.2` block of `rea_video/capabilities/prompting.yaml` in https://github.com/JuneYaooo/rea-video (Apache-2.0, checked 2026-11-23), which cites Alibaba Cloud's text-to-video prompt guide (https://help.aliyun.com/zh/model-studio/text-to-video-prompt) and the Wan 2.2 repo (https://github.com/Wan-Video/Wan2.2). rea-video is not a dependency; only these rules were copied and reworded.
