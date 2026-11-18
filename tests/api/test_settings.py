"""Settings read from the Lambda environment."""
import json

import pytest

from api.settings import Settings

MANIFEST = json.dumps({"model": "m", "limits": {"prompt_max_chars": 5}, "inputs": []})
BASE = {"CLIPS_BUCKET": "b", "QUEUE_URL": "q", "MODEL_MANIFEST": MANIFEST}


def test_defaults_are_3_active_jobs_and_a_30_second_undo_window():
    s = Settings.from_env(BASE)

    assert s.max_active_jobs == 3
    assert s.submit_delay_seconds == 30


def test_values_come_from_the_environment():
    s = Settings.from_env({**BASE, "MAX_ACTIVE_JOBS": "5", "SUBMIT_DELAY_SECONDS": "10"})

    assert (s.max_active_jobs, s.submit_delay_seconds) == (5, 10)


@pytest.mark.parametrize("delay", ["-1", "901", "soon"])
def test_the_delay_must_be_a_whole_number_SQS_accepts(delay):
    with pytest.raises(ValueError):
        Settings.from_env({**BASE, "SUBMIT_DELAY_SECONDS": delay})
