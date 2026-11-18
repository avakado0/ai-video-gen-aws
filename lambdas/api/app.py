"""The FastAPI app. Routes stay thin; the rules live in the modules they call."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _sub_from_claims(scope):
    event = scope.get("aws.event") or {}
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def current_sub(request: Request) -> str:
    """The caller's Cognito `sub`, set by the authentication middleware. Never from the body."""
    return request.state.sub


def create_app(manifest: dict) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_claims(request: Request, call_next):
        # The `sub` comes only from the claims API Gateway verified. Its JWT authorizer normally
        # rejects bad tokens first, but the app does not rely on that, and checking here covers
        # every route, including ones added later and paths that do not exist.
        sub = _sub_from_claims(request.scope)
        if sub is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        request.state.sub = sub
        return await call_next(request)

    @app.get("/config")
    def get_config():
        return manifest

    return app
