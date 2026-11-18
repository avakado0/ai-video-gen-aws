from worker.imds import Imds

TOKEN = ("PUT", "/latest/api/token")
ACTION = ("GET", "/latest/meta-data/spot/instance-action")
TYPE = ("GET", "/latest/meta-data/instance-type")


def test_no_notice_is_404_and_means_false(http):
    http.routes[TOKEN] = (200, "tok")
    assert Imds(http.url).spot_notice() is False


def test_notice_present_means_true(http):
    http.routes[TOKEN] = (200, "tok")
    http.routes[ACTION] = (200, '{"action": "terminate", "time": "2026-11-18T10:00:00Z"}')
    assert Imds(http.url).spot_notice() is True


def test_uses_imdsv2_token_and_reuses_it(http):
    http.routes[TOKEN] = (200, "tok")
    imds = Imds(http.url)
    imds.spot_notice()
    imds.spot_notice()
    gets = [h for m, p, h in http.seen if m == "GET"]
    assert all(h.get("X-Aws-Ec2-Metadata-Token") == "tok" for h in gets)
    assert sum(1 for m, p, h in http.seen if m == "PUT") == 1


def test_expired_token_is_fetched_again(http):
    http.routes[TOKEN] = (200, "tok")
    answers = iter([(401, ""), (200, "{}")])
    http.routes[ACTION] = (None, lambda: next(answers))
    assert Imds(http.url).spot_notice() is True
    assert sum(1 for m, p, h in http.seen if m == "PUT") == 2


def test_unreachable_metadata_service_means_no_notice():
    # Nothing listens here. A flaky check must never look like an interruption.
    assert Imds("http://127.0.0.1:9", timeout=0.2).spot_notice() is False


def test_instance_type(http):
    http.routes[TOKEN] = (200, "tok")
    http.routes[TYPE] = (200, "g4dn.2xlarge")
    assert Imds(http.url).instance_type() == "g4dn.2xlarge"


def test_instance_type_falls_back_to_unknown(http):
    assert Imds(http.url).instance_type() == "unknown"
