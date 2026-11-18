"""The worker image is not built in tests (it is about 8 GB), but its recipe can be checked."""
import pathlib
import re
import stat

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQS = (ROOT / "worker" / "requirements.txt").read_text()
DOCKERFILE = (ROOT / "worker" / "Dockerfile").read_text()


def pins():
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)", REQS, re.M))


def test_every_requirement_is_pinned_exactly():
    lines = [l for l in REQS.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert lines and all("==" in l for l in lines)


def test_diffusers_stays_at_or_below_0_35():
    # A NaN regression was reported after 0.35 (diffusers issue #12612); spec section 5.
    major, minor = (int(x) for x in pins()["diffusers"].split(".")[:2])
    assert (major, minor) <= (0, 35)


def test_the_libraries_the_wan_pipeline_imports_are_listed():
    # regex, ftfy, sentencepiece (tokenizer), accelerate (cpu offload), imageio-ffmpeg (mp4 export)
    for name in ("torch", "diffusers", "transformers", "accelerate", "safetensors", "sentencepiece",
                 "ftfy", "regex", "imageio", "imageio-ffmpeg", "boto3"):
        assert name in pins(), name


def test_image_ships_the_worker_and_the_shared_code():
    assert re.search(r"^COPY worker/? ", DOCKERFILE, re.M)
    assert re.search(r"^COPY lambdas/common ", DOCKERFILE, re.M)
    assert 'CMD ["python", "-m", "worker"]' in DOCKERFILE


def test_image_never_downloads_weights_at_run_time():
    assert "HF_HUB_OFFLINE=1" in DOCKERFILE


def test_image_asks_for_the_gpu_driver_libraries():
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility" in DOCKERFILE


def test_build_script_is_executable_and_checks_the_account():
    script = ROOT / "scripts" / "build_push.sh"
    assert script.stat().st_mode & stat.S_IXUSR
    text = script.read_text()
    assert "477656043770" in text and "AWS_PROFILE" in text
    assert "--platform linux/amd64" in text
