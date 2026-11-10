import json
import logging
import re
from pathlib import Path
from time import monotonic
from typing import NoReturn
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.analysis import AnalysisError, AnalysisService
from app.mock import MockAnalyzer
from app.schemas import AnalysisMeta, AnalysisRequest, AnalysisResponse


ANALYSIS_PATH = "/api/v1/analyses"
MAX_BODY_BYTES = 64 * 1024
_STATIC = Path(__file__).parent / "static"
_LOCAL_HOST = re.compile(r"(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?", re.IGNORECASE)
_LOGGER = logging.getLogger("uvicorn.error")


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("not a JSON number")


async def _read_analysis_request(request: Request) -> AnalysisRequest:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if content_type != "application/json" or encoding not in ("", "identity"):
        raise AnalysisError(415, "UNSUPPORTED_MEDIA_TYPE", "請送出未壓縮的 application/json。")

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_BODY_BYTES - len(body):
            raise AnalysisError(413, "PAYLOAD_TOO_LARGE", "請求內容超過 64 KiB。")
        body.extend(chunk)
    try:
        raw = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
    except (ValueError, RecursionError) as exc:
        raise AnalysisError(400, "INVALID_JSON", "請求內容不是有效的 UTF-8 JSON。") from exc

    try:
        return AnalysisRequest.model_validate(raw)
    except ValidationError as exc:
        details = []
        for issue in exc.errors(include_input=False, include_context=False, include_url=False)[:10]:
            path = issue["loc"]
            field = path[0] if path and path[0] in AnalysisRequest.model_fields else None
            detail = {"reason": issue["type"]}
            if field is not None:
                detail["field"] = field
            details.append(detail)
        raise AnalysisError(422, "VALIDATION_ERROR", "請檢查需求文字、系統名稱及案例上限。", details=details) from exc


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


def create_app(service: AnalysisService | None = None) -> FastAPI:
    service = service if service is not None else AnalysisService(MockAnalyzer())
    app = FastAPI(
        title="需求分析助手",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next) -> Response:
        request.state.request_id = uuid4()
        request.state.started_at = monotonic()
        is_analysis = request.scope["path"] == ANALYSIS_PATH
        hosts = request.headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        match = _LOCAL_HOST.fullmatch(host)
        origins = request.headers.getlist("origin")
        same_origin = f"{request.scope['scheme']}://{host}"
        if match is None or (match[2] is not None and not 1 <= int(match[2]) <= 65535):
            response = _error_response(request, AnalysisError(400, "INVALID_HOST", "請從本機位址存取。"))
        elif is_analysis and origins and origins != [same_origin]:
            response = _error_response(request, AnalysisError(403, "ORIGIN_NOT_ALLOWED", "請從本站送出分析。"))
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
        if is_analysis:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            _STATIC / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; object-src 'none'; base-uri 'none'; "
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
        payload = await _read_analysis_request(request)
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

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app


app = create_app()
