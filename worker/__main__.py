"""Entry point: `python -m worker`. Wires the real clients to the Worker and runs until SIGTERM."""
import logging
import os
import pathlib
import signal
import threading

import boto3

from .config import Config
from .imds import Imds
from .jobqueue import WorkerQueue
from .protection import protection_from_env
from .runner import Worker
from .store import ClipStore
from .wan import WanGenerator


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("worker")

    config = Config.from_env()
    stopping = threading.Event()
    # ECS sends SIGTERM when it stops the task (Spot drain, scale-in). The running job sees the
    # flag through its watcher, hands its message back and the loop ends.
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    imds = Imds()
    generator = WanGenerator(config.models_dir, config.manifest["model"])
    log.info("loading model from %s", generator.path)
    generator.load()

    worker = Worker(
        config,
        WorkerQueue(boto3.client("sqs"), config.queue_url, config.poll_wait_seconds),
        ClipStore(boto3.client("s3"), config.clips_bucket),
        generator,
        protection_from_env(os.environ, boto3.client("ecs")),
        spot_notice=imds.spot_notice,
        stopping=stopping.is_set,
        instance_type=imds.instance_type,
        work_dir=pathlib.Path(os.environ.get("WORK_DIR", "/data/work")),
    )
    log.info("ready")
    worker.run_forever()
    log.info("stopped")


if __name__ == "__main__":
    main()
