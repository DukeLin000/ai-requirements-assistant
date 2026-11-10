import logging
import os
import re
from pathlib import Path
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.analysis import AnalysisError, AnalysisService
from app.mock import MockAnalyzer
from app.request_body import read_model
from app.schemas import AnalysisMeta, AnalysisRequest, AnalysisResponse
from app.workbench_api import workbench_router
from app.workbench_store import WorkbenchStore


ANALYSIS_PATH = "/api/v1/analyses"
MAX_BODY_BYTES = 64 * 1024
_STATIC = Path(__file__).parent / "static"
_LOCAL_HOST = re.compile(r"(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?", re.IGNORECASE)
_LOGGER = logging.getLogger("uvicorn.error")


def _error_response(request: Request, error: AnalysisError) -> JSONResponse:
    request_id = str(request.state.request_id)
    _LOGGER.warning("analysis rejected request_id=%s code=%s", request_id, error.code)
    headers = {"Retry-After": "3"} if error.code == "ANALYSIS_BUSY" else {}
    return JSONResponse(
        status_code=error.status,
        headers=headers,
        content={
            "request_id": request_id,
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "details": error.details,
            },
        },
    )


def create_app(service: AnalysisService | None = None, db_path: Path | None = None) -> FastAPI:
    service = service if service is not None else AnalysisService(MockAnalyzer())
    if db_path is None:
        db_path = Path(os.environ.get("REQUIREMENTS_DB_PATH") or _STATIC.parent.parent / ".data" / "requirements.sqlite3")
    store = WorkbenchStore(db_path)
    app = FastAPI(
        title="需求分析助手",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next) -> Response:
        request.state.request_id = uuid4()
        request.state.started_at = monotonic()
        path = request.scope["path"]
        is_analysis = path == ANALYSIS_PATH
        is_workbench = path == "/api/v1/requirements" or path.startswith("/api/v1/requirements/") or path == "/api/v1/dispatches"
        hosts = request.headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        match = _LOCAL_HOST.fullmatch(host)
        origins = request.headers.getlist("origin")
        same_origin = f"{request.scope['scheme']}://{host}"
        if match is None or (match[2] is not None and not 1 <= int(match[2]) <= 65535):
            response = _error_response(request, AnalysisError(400, "INVALID_HOST", "請從本機位址存取。"))
        elif (is_analysis or is_workbench) and origins and origins != [same_origin]:
            response = _error_response(request, AnalysisError(403, "ORIGIN_NOT_ALLOWED", "請從本站操作。"))
        else:
            try:
                response = await call_next(request)
            except AnalysisError as error:
                response = _error_response(request, error)
            except Exception as exc:
                _LOGGER.error(
                    "unexpected analysis error request_id=%s type=%s",
                    request.state.request_id,
                    type(exc).__name__,
                )
                response = _error_response(request, AnalysisError(500, "INTERNAL_ERROR", "分析服務發生錯誤。"))

        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if is_analysis or is_workbench:
            response.headers["Cache-Control"] = "no-store"
        elif request.scope["path"].startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            _STATIC / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'self'"
                ),
            },
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        ANALYSIS_PATH,
        response_model=AnalysisResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": AnalysisRequest.model_json_schema()}},
            },
        },
    )
    async def analyze(request: Request) -> AnalysisResponse:
        payload = await read_model(
            request, AnalysisRequest, MAX_BODY_BYTES, "請求內容超過 64 KiB。", "請檢查需求文字、系統名稱及案例上限。",
        )
        data, status = await service.analyze(payload)
        latency_ms = int((monotonic() - request.state.started_at) * 1_000)
        _LOGGER.info(
            "analysis completed request_id=%s model=%s status=%s latency_ms=%d features=%d tests=%d",
            request.state.request_id,
            service.analyzer.name,
            status,
            latency_ms,
            len(data.features),
            len(data.test_cases),
        )
        return AnalysisResponse(
            request_id=request.state.request_id,
            status=status,
            data=data,
            meta=AnalysisMeta(model=service.analyzer.name, latency_ms=latency_ms),
        )

    app.include_router(workbench_router(store, service))
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app


app = create_app()
