"""The Kaggle spike notebook is generated from the real worker files. It can't run here (no GPU),
but it can be checked for shape, embedded code and syntax."""
import ast
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def build():
    spec = importlib.util.spec_from_file_location("make_spike_notebook", ROOT / "scripts" / "make_spike_notebook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def code_cells(nb):
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_is_a_valid_v4_notebook():
    nb = build()
    assert nb["nbformat"] == 4 and nb["cells"]
    json.dumps(nb)
    for c in nb["cells"]:
        assert c["cell_type"] in ("markdown", "code") and isinstance(c["source"], list)


def test_every_code_cell_is_valid_python():
    for i, src in enumerate(code_cells(build())):
        ast.parse(src, filename=f"cell {i}")


def test_embeds_the_worker_files_exactly():
    text = "\n".join(code_cells(build()))
    for name in ("wan.py", "errors.py"):
        assert (ROOT / "worker" / name).read_text() in text.replace("\\n", "\n") or repr((ROOT / "worker" / name).read_text()) in text


def test_installs_the_pinned_requirements_except_torch_and_boto3():
    text = "\n".join(code_cells(build()))
    assert "diffusers==0.35.2" in text
    assert "torch==" not in text and "boto3==" not in text


def test_uses_the_pinned_weights_commit_and_one_gpu():
    text = "\n".join(code_cells(build()))
    assert "0fad780a534b6463e45facd96134c9f345acfa5b" in text
    assert 'CUDA_VISIBLE_DEVICES' in text


def test_covers_the_spec_questions():
    text = "\n".join(code_cells(build()))
    for needle in ("duration_seconds", "Aborted", "peak", "contact", "report"):
        assert needle in text, needle
