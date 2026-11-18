import json

from worker.jobqueue import WorkerQueue


def send(aws, **body):
    aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=json.dumps(body or {"a": 1}))


def attrs(aws):
    return aws.sqs.get_queue_attributes(QueueUrl=aws.queue_url, AttributeNames=["All"])["Attributes"]


def test_receive_returns_none_on_an_empty_queue(aws):
    assert WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0).receive() is None


def test_receive_gives_body_handle_and_receive_count(aws):
    send(aws, hello="x")
    msg = WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0).receive()
    assert json.loads(msg.body) == {"hello": "x"}
    assert msg.receive_count == 1 and msg.receipt_handle


def test_delete_removes_the_message(aws):
    send(aws)
    q = WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0)
    q.delete(q.receive())
    assert attrs(aws)["ApproximateNumberOfMessagesNotVisible"] == "0"
    assert attrs(aws)["ApproximateNumberOfMessages"] == "0"


def test_release_makes_the_message_visible_again_at_once(aws):
    send(aws)
    q = WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0)
    q.release(q.receive())
    again = q.receive()
    assert again is not None and again.receive_count == 2


def test_extend_keeps_the_message_hidden(aws):
    send(aws)
    q = WorkerQueue(aws.sqs, aws.queue_url, wait_seconds=0)
    q.extend(q.receive(), 180)
    assert q.receive() is None
