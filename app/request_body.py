import json
from typing import NoReturn, TypeVar

from fastapi import Request
from pydantic import BaseModel, ValidationError

from app.analysis import AnalysisError


T = TypeVar("T", bound=BaseModel)


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("not a JSON number")


async def read_model(request: Request, model: type[T], limit: int, size_message: str, invalid_message: str) -> T:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if content_type != "application/json" or encoding not in ("", "identity"):
        raise AnalysisError(415, "UNSUPPORTED_MEDIA_TYPE", "請送出未壓縮的 application/json。")

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > limit - len(body):
            raise AnalysisError(413, "PAYLOAD_TOO_LARGE", size_message)
        body.extend(chunk)
    try:
        raw = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
    except (ValueError, RecursionError) as exc:
        raise AnalysisError(400, "INVALID_JSON", "請求內容不是有效的 UTF-8 JSON。") from exc

    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = []
        for issue in exc.errors(include_input=False, include_context=False, include_url=False)[:10]:
            path = issue["loc"]
            field = path[0] if path and path[0] in model.model_fields else None
            detail = {"reason": issue["type"]}
            if field is not None:
                detail["field"] = field
            details.append(detail)
        raise AnalysisError(422, "VALIDATION_ERROR", invalid_message, details=details) from exc
