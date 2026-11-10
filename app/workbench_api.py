from time import monotonic
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.analysis import AnalysisError, AnalysisService
from app.ba_document import WORD_TYPE
from app.request_body import read_model
from app.schemas import AnalysisMeta, AnalysisRequest, AnalysisResponse
from app.workbench_models import REQUEST_LIMIT, AnalyzeSaved, Dispatch, Export, Intake, SaveReview, Scope, Ticket, Transition
from app.workbench_store import WorkbenchStore


def _body(request, model):
    return read_model(
        request, model, REQUEST_LIMIT, "工作台請求內容超過 3 MiB。",
        "請檢查工作台欄位與檔案大小。",
    )


def _download(data: bytes, media_type: str, filename: str) -> Response:
    return Response(
        data, media_type=media_type,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe="")},
    )


def workbench_router(store: WorkbenchStore, service: AnalysisService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/requirements")
    def list_requirements(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
        return store.list_requirements(offset, limit)

    @router.post("/requirements", status_code=201)
    async def intake(request: Request):
        return await run_in_threadpool(store.create, await _body(request, Intake))

    @router.get("/requirements/{number}")
    def detail(number: Ticket):
        return store.detail(number)

    @router.get("/requirements/{number}/source")
    def source_file(number: Ticket):
        filename, content = store.source_file(number)
        return _download(content, "application/octet-stream", filename)

    @router.post("/requirements/{number}/analysis")
    async def analyze(number: Ticket, request: Request):
        payload = await _body(request, AnalyzeSaved)
        record = await run_in_threadpool(store.detail, number)
        store.require_revision(record["revision"], payload.revision)
        if record["analysis"] is not None:
            raise AnalysisError(409, "ALREADY_ANALYZED", "這筆需求已有分析結果，請繼續 BA 審閱。")
        analysis_request = AnalysisRequest(
            requirement_text=record["requirement_text"], system_name=record["system_name"],
            max_test_cases=payload.max_test_cases,
        )
        data, status = await service.analyze(analysis_request)
        response = AnalysisResponse(
            request_id=request.state.request_id, status=status, data=data,
            meta=AnalysisMeta(model=service.analyzer.name, latency_ms=int((monotonic() - request.state.started_at) * 1000)),
        )
        return await run_in_threadpool(store.set_analysis, number, payload.revision, response.model_dump(mode="json"))

    @router.put("/requirements/{number}/review")
    async def save_review(number: Ticket, request: Request):
        return await run_in_threadpool(store.save_review, number, await _body(request, SaveReview))

    @router.post("/requirements/{number}/preview")
    async def preview(number: Ticket, request: Request):
        return await run_in_threadpool(store.preview, number, await _body(request, Scope))

    @router.post("/requirements/{number}/documents", status_code=201)
    async def export(number: Ticket, request: Request):
        return await run_in_threadpool(store.export, number, await _body(request, Export))

    @router.get("/requirements/{number}/documents/{version}")
    def word(number: Ticket, version: int):
        return _download(store.word(number, version), WORD_TYPE, f"{number}-BA-v{version}.docx")

    @router.post("/requirements/{number}/dispatches", status_code=201)
    async def dispatch(number: Ticket, request: Request):
        payload = await _body(request, Dispatch)
        return await run_in_threadpool(store.export, number, payload, payload.team)

    @router.get("/dispatches")
    def list_dispatches(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
        return store.list_dispatches(offset, limit)

    @router.post("/requirements/{number}/dispatches/{version}/transition")
    async def transition(number: Ticket, version: int, request: Request):
        return await run_in_threadpool(store.transition, number, version, await _body(request, Transition))

    return router
