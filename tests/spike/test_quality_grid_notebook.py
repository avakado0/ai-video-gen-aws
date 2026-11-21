"""The quality-grid notebook (plan 05): shape, syntax, and that it runs the nine planned variants."""
import ast
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def build():
    spec = importlib.util.spec_from_file_location("make_quality_grid_notebook",
                                                  ROOT / "scripts" / "make_quality_grid_notebook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def sources(nb):
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_valid_notebook_with_parsable_cells():
    nb = build()
    assert nb["nbformat"] == 4
    json.dumps(nb)
    for i, src in enumerate(sources(nb)):
        ast.parse(src, filename=f"cell {i}")


def test_shares_setup_with_the_first_spike_so_the_weights_and_pins_match():
    text = "\n".join(sources(build()))
    assert "0fad780a534b6463e45facd96134c9f345acfa5b" in text
    assert "diffusers==0.35.2" in text
    assert "WanGenerator" in text


def test_embeds_the_worker_code_that_has_the_tunable_options():
    text = "\n".join(sources(build()))
    assert "default_negative_prompt" in text and "flow_shift" in text and "guidance" in text


def test_every_planned_run_is_present_with_its_settings():
    text = "\n".join(sources(build()))
    for label in ("run1-baseline", "run2-shift8", "run3-steps50", "run4-readme-recipe", "run5-shift12",
                  "run6-official-negative", "run7-best-plus-negative", "run8-simple-prompt", "run9-second-seed"):
        assert label in text, label
    for needle in ("50", "6.0", "8.0", "12.0"):
        assert needle in text, needle


def test_uses_wans_official_negative_prompt_and_the_owners_short_one():
    text = "\n".join(sources(build()))
    assert "Bright tones, overexposed" in text
    assert "blurry, distorted, extra limbs" in text


def test_saves_each_clip_so_it_can_be_watched_not_just_a_contact_sheet():
    text = "\n".join(sources(build()))
    assert "/kaggle/working/" in text and ".mp4" in text


def test_every_run_is_its_own_cell_so_one_failure_does_not_stop_the_rest():
    text_cells = [s for s in sources(build()) if "grid_run(" in s and "def grid_run" not in s]
    assert len(text_cells) >= 9
