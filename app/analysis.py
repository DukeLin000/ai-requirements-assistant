import asyncio
from collections.abc import Iterable
from threading import Lock
from typing import Protocol

from pydantic import ValidationError

from app.schemas import AnalysisData, AnalysisRequest, AnalysisStatus


class Analyzer(Protocol):
    name: str

    async def generate(self, request: AnalysisRequest) -> object: ...


class AnalysisError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = [] if details is None else details


class InvalidAnalysis(ValueError):
    """The analyzer returned inconsistent data."""


def unique_ids(ids: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            raise InvalidAnalysis("duplicate ID")
        seen.add(item_id)
    return seen


def validate_analysis(data: AnalysisData, request: AnalysisRequest) -> AnalysisStatus:
    feature_ids = unique_ids(feature.id for feature in data.features)
    question_ids = unique_ids(question.id for question in data.questions)
    unique_ids(criterion.id for criterion in data.acceptance_criteria)
    unique_ids(case.id for case in data.test_cases)
    criteria = {criterion.id: criterion for criterion in data.acceptance_criteria}

    if len(data.test_cases) > request.max_test_cases:
        raise InvalidAnalysis("test-case limit exceeded")
    if not feature_ids and (criteria or data.test_cases):
        raise InvalidAnalysis("criteria or tests without features")

    for item in (*data.features, *data.acceptance_criteria, *data.test_cases):
        if item.basis == "explicit" and not item.source_quotes:
            raise InvalidAnalysis("explicit item without a citation")
        if any(not quote.strip() or quote not in request.requirement_text for quote in item.source_quotes):
            raise InvalidAnalysis("citation not present in the requirement")

    for question in data.questions:
        if not unique_ids(question.related_feature_ids) <= feature_ids:
            raise InvalidAnalysis("question references an unknown feature")
    for criterion in data.acceptance_criteria:
        if criterion.feature_id not in feature_ids:
            raise InvalidAnalysis("criterion references an unknown feature")
    for case in data.test_cases:
        if case.feature_id not in feature_ids:
            raise InvalidAnalysis("test references an unknown feature")
        if case.criterion_id is not None:
            criterion = criteria.get(case.criterion_id)
            if criterion is None or criterion.feature_id != case.feature_id:
                raise InvalidAnalysis("test references a missing or unrelated criterion")
        if not unique_ids(case.blocked_by) <= question_ids:
            raise InvalidAnalysis("test references an unknown question")
        if case.blocked_by and case.expected_result is not None:
            raise InvalidAnalysis("blocked test has an expected result")
        if not case.blocked_by and (
            case.expected_result is None or not case.expected_result.strip()
        ):
            raise InvalidAnalysis("unblocked test has no expected result")

    if not feature_ids:
        return "insufficient_input"
    if data.questions:
        return "needs_clarification"
    return "generated"


class AnalysisService:
    def __init__(self, analyzer: Analyzer, timeout_seconds: float = 45) -> None:
        self.analyzer = analyzer
        self._timeout_seconds = timeout_seconds
        self._slot = Lock()

    async def analyze(self, request: AnalysisRequest) -> tuple[AnalysisData, AnalysisStatus]:
        if not self._slot.acquire(blocking=False):
            raise AnalysisError(429, "ANALYSIS_BUSY", "已有分析正在進行，請稍後重試。", retryable=True)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw = await self.analyzer.generate(request)
                data = AnalysisData.model_validate(raw)
                return data, validate_analysis(data, request)
        except TimeoutError as exc:
            raise AnalysisError(504, "LLM_TIMEOUT", "分析逾時，請稍後重試。", retryable=True) from exc
        except (ValidationError, InvalidAnalysis) as exc:
            raise AnalysisError(502, "INVALID_LLM_OUTPUT", "分析結果未通過來源或資料檢查。") from exc
        finally:
            self._slot.release()
