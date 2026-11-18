"""Entry point behaviour: CORS preflight, authentication, routing, and the config route.

The OPTIONS /{proxy+} route has no authorizer, so a browser preflight reaches the Lambda
without a token and must get a 2xx, or every fetch from the front end fails.
"""
import json
import pathlib

from api import handler
from conftest import make_event

V1 = json.loads((pathlib.Path(__file__).resolve().parents[2] / "model_manifest.json").read_text())


def test_preflight_gets_204_with_no_body_and_needs_no_setup():
    # No AWS, no environment: the preflight must never depend on either.
    res = handler.lambda_handler(make_event("OPTIONS", "/jobs", claims=False), None)

    assert res["statusCode"] == 204
    assert res.get("body", "") == ""


def test_request_without_jwt_claims_is_rejected(call):
    # API Gateway's authorizer normally stops these; the app must not trust that alone.
    for method, path in [("GET", "/config"), ("GET", "/jobs"), ("POST", "/jobs"), ("DELETE", "/jobs/x")]:
        assert call(method, path, claims=False).status == 401


def test_get_config_returns_the_manifest(call):
    res = call("GET", "/config")

    assert res.status == 200
    assert res.json() == V1


def test_unknown_route_is_404_with_a_detail(call):
    res = call("GET", "/nope")

    assert res.status == 404
    assert "detail" in res.json()


def test_broken_configuration_is_500_without_internals(call, monkeypatch):
    # A bad manifest env var is the realistic way every request blows up.
    monkeypatch.setenv("MODEL_MANIFEST", "{not json")
    handler.reset()

    res = call("GET", "/config")

    assert res.status == 500
    assert res.json() == {"detail": "Internal error"}
