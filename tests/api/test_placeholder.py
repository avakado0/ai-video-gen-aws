"""API placeholder Lambda: answers CORS preflight, 501 for everything else.

The OPTIONS /{proxy+} route (no authorizer) targets this Lambda, so a browser
preflight must get a 2xx or every fetch from the front end fails. The real
FastAPI app (phase 2) must keep this behaviour.
"""

from api import handler


def event(method, path="/config"):
    return {"rawPath": path, "requestContext": {"http": {"method": method, "path": path}}}


def test_preflight_gets_204_with_no_body():
    res = handler.lambda_handler(event("OPTIONS"), None)

    assert res["statusCode"] == 204
    assert res.get("body", "") == ""


def test_other_methods_still_get_501():
    for method in ("GET", "POST", "DELETE"):
        assert handler.lambda_handler(event(method), None)["statusCode"] == 501
