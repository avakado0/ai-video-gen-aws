# 03: `quality` option (fast / high)

Status: FINISHED 2026-11-22. Built 2026-11-20 (steps 1 to 6) and in use since: the `ecs.tf` change was applied 2026-11-20 (see plan 02) and every AWS run used `fast` or `high`. The step counts and resolutions in the table are the 1.3B values of 2026-11-20; the current ones are in plan 06. The mock front-end `text.json` was left alone: it is a different mock model. Owner decision: ship two quality levels, chosen on the front end.

## Context
The Kaggle spike (`docs/wan_spike_findings.md`) showed generation is slow and resolution is the big lever: 640x368 takes 11.0 s per step, 832x480 takes 23.7 s. Owner wants both: a quick default and a sharper option. The UI is manifest-driven, so this is one new enum param, no front-end code.

| `quality` | Resolution | Steps | 3 s clip | Source |
|---|---|---|---|---|
| `fast` (default) | 640x368 | 20 | about 4.7 min | measured (E) |
| `high` | 832x480 | 30 | about 15 min | measured |

A 5 s clip is 2.6x longer (`high` about 39 min measured, `fast` about 12 min my estimate). Not blocked: the manifest cannot express a combined rule, and the heartbeat covers long jobs. A job lost to a Spot interruption restarts from step 0 (no resume).

## Changes (TDD, one commit each, tests first)
1. **Manifest and tests.** `model_manifest.json`: add param `quality`, enum `["fast","high"]`, default `"fast"`, after `duration_seconds`. Update `tests/manifest/test_manifest.py` (param order and the enum values) and `tests/worker/test_job.py` (default filled in, bad value rejected as `invalid_request`). `tests/api/test_submit.py` if it lists params.
2. **`worker/wan.py`.** `QUALITY = {"fast": (640, 368, 20), "high": (832, 480, 30)}` and a pure `preset(quality)` returning width, height, steps. `generate` reads `job.params["quality"]`. `steps` constructor argument becomes an optional override (the spike notebooks set it); `WIDTH/HEIGHT` constants go. Tests: mapping per level, both sizes are multiples of 16, unknown level raises `KeyError`, the override wins.
3. **Config cleanup.** Remove `Config.steps` and `STEPS` (no longer a deploy-time knob); `__main__.py` builds `WanGenerator(models_dir, model)`; update `tests/worker/test_config.py`.
4. **Usage log.** `runner.py`: add `quality=job.params.get("quality")` to the `job_attempt` line (a test checks it). `ecs.tf`: saved Logs Insights query also groups by `quality`. Terraform: fmt and validate only; the user applies it.
5. **Spike notebooks.** `make_spike_notebook.py` `job()` passes `quality: "high"` so the notebook keeps testing 832x480 at 30 steps; regenerate both notebooks; the notebook tests stay green.
6. **Docs.** Spec 5 (manifest note: params list and the `quality` table), `docs/wan_spike_findings.md` ("Decision" line), `_plans/01_build_order.md` (set-defaults item ticked), effort log. Mock front-end manifest `text.json` gets the same enum so the UI shows it with no backend.
7. **Artifact.** Next hourly batch: spike results, quality option, spec/plan changes.

## Verification
- `.venv/bin/pytest` (all offline), `terraform fmt -check && terraform validate`.
- Front end: serve `frontend/` with the mock manifest and check the dropdown appears and defaults to `fast`.
- Not verifiable here: the `high` and `fast` times on a real g4dn.2xlarge, quality of `fast` beyond one sample.

## Not changed
The 5 s option stays. `flow_shift` stays 3.0. `guidance_scale` stays 5.0.
