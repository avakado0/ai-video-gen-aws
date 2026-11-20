"""The speed spike notebook: shape, syntax, and that it asks the questions it is for."""
import ast
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def build():
    spec = importlib.util.spec_from_file_location("make_speed_notebook", ROOT / "scripts" / "make_speed_notebook.py")
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


def test_measures_each_speed_idea():
    text = "\n".join(sources(build()))
    for needle in ("baseline", "640", "368", "guidance_scale", "remove_all_hooks", "encode_prompt"):
        assert needle in text, needle
