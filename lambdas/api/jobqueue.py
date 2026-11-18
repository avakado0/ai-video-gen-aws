"""The jobs queue. (Not `queue.py`: that name belongs to the standard library.)"""
import json


class JobQueue:
    def __init__(self, sqs, url):
        self.sqs = sqs
        self.url = url

    def send(self, message, delay_seconds=0):
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps(message), DelaySeconds=delay_seconds)
