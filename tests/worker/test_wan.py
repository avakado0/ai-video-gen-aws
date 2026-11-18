"""WanGenerator's pure parts. The model itself needs a GPU and is checked by the Kaggle spike."""
import random
import subprocess
import sys

from worker import wan


def test_three_seconds_is_49_frames_and_five_is_81():
    assert wan.num_frames("3") == 49
    assert wan.num_frames("5") == 81


def test_frame_counts_are_the_4n_plus_1_the_model_needs():
    for seconds in ("3", "5"):
        assert (wan.num_frames(seconds) - 1) % 4 == 0


def test_given_seed_is_used_as_is():
    assert wan.pick_seed({"seed": 42}, random.Random(0)) == 42
    assert wan.pick_seed({"seed": 0}, random.Random(0)) == 0


def test_unset_seed_is_random_but_in_the_manifest_range():
    seed = wan.pick_seed({}, random.Random(1))
    assert 0 <= seed <= 2147483647


def test_output_shape_matches_the_spec():
    assert (wan.WIDTH, wan.HEIGHT, wan.FPS) == (832, 480, 16)


def test_importing_the_module_needs_no_torch():
    code = "import sys; sys.modules['torch'] = None; import worker.wan, worker.runner"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=".", env={"PYTHONPATH": "lambdas:."})
