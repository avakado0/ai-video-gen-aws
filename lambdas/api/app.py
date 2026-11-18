"""The FastAPI app. Routes stay thin; the rules live in the modules they call."""
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt, StrictStr

from .rules import RequestError


def _sub_from_claims(scope):
    event = scope.get("aws.event") or {}
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def current_sub(request: Request) -> str:
    """The caller's Cognito `sub`, set by the authentication middleware. Never from the body."""
    return request.state.sub


class UploadRequest(BaseModel):
    slot: StrictStr
    index: StrictInt
    mime: StrictStr
    size: StrictInt
    job_id: StrictStr | None = None


def create_app(manifest: dict, service) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestError)
    async def request_error(request: Request, exc: RequestError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def bad_body(request: Request, exc: RequestValidationError):
        # One readable string: the front end shows `detail` as the error message.
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"detail": problems}, status_code=400)

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

    @app.post("/uploads")
    def post_upload(body: UploadRequest, sub: str = Depends(current_sub)):
        return service.create_upload(sub, body.slot, body.index, body.mime, body.size, body.job_id)

    return app
