"""The jobs queue, from the worker's side."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    body: str
    receipt_handle: str
    receive_count: int


class WorkerQueue:
    def __init__(self, sqs, url, wait_seconds=20):
        self.sqs = sqs
        self.url = url
        self.wait_seconds = wait_seconds

    def receive(self):
        """One message (long poll), or None when the queue stayed empty."""
        resp = self.sqs.receive_message(QueueUrl=self.url, MaxNumberOfMessages=1,
                                        WaitTimeSeconds=self.wait_seconds,
                                        AttributeNames=["ApproximateReceiveCount"])
        for m in resp.get("Messages", []):
            return Message(m["Body"], m["ReceiptHandle"], int(m["Attributes"]["ApproximateReceiveCount"]))
        return None

    def delete(self, message):
        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=message.receipt_handle)

    def extend(self, message, seconds):
        """Heartbeat: keep the message hidden for `seconds` more."""
        self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=message.receipt_handle,
                                           VisibilityTimeout=seconds)

    def release(self, message):
        """Give the job back at once so another worker can take it (Spot notice, SIGTERM, errors)."""
        self.extend(message, 0)
