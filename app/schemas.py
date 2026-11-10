from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100)]
DetailText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
StepText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
QuoteText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
FeatureId = Annotated[str, StringConstraints(pattern=r"^F(0[1-9]|[1-9][0-9])$")]
QuestionId = Annotated[str, StringConstraints(pattern=r"^Q(0[1-9]|[1-9][0-9])$")]
AcceptanceId = Annotated[str, StringConstraints(pattern=r"^A(0[1-9]|[1-9][0-9])$")]
TestId = Annotated[str, StringConstraints(pattern=r"^T(0[1-9]|[1-9][0-9])$")]
AnalysisStatus = Literal["generated", "needs_clarification", "insufficient_input"]
ActivityText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisRequest(StrictModel):
    system_name: str | None = Field(default=None, min_length=1, max_length=80)
    requirement_text: str = Field(min_length=20, max_length=10_000)
    max_test_cases: int = Field(default=8, ge=1, le=12)

    @field_validator("system_name", mode="before")
    @classmethod
    def normalize_system_name(cls, value: object) -> object:
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator("requirement_text", mode="before")
    @classmethod
    def normalize_requirement(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return value

    @field_validator("system_name", "requirement_text", mode="before")
    @classmethod
    def require_utf8(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("文字必須是有效的 Unicode。") from exc
        return value


class CitedItem(StrictModel):
    basis: Literal["explicit", "proposed"]
    source_quotes: list[QuoteText] = Field(max_length=3)


class ActivityStep(StrictModel):
    actor: ActivityText
    action: ActivityText
    guard: ActivityText | None = None


class ActivityBranch(StrictModel):
    condition: ActivityText
    when_true: list[ActivityStep] = Field(min_length=1, max_length=4)
    otherwise_question_id: QuestionId


class ActivityPlan(StrictModel):
    steps: list[ActivityStep] = Field(min_length=1, max_length=4)
    branch: ActivityBranch | None = None


class Feature(CitedItem):
    id: FeatureId
    title: ShortText
    description: DetailText
    activity: ActivityPlan | None = None


class Question(StrictModel):
    id: QuestionId
    question: DetailText
    reason: DetailText
    related_feature_ids: list[FeatureId] = Field(max_length=10)


class Acceptance(CitedItem):
    id: AcceptanceId
    feature_id: FeatureId
    given: DetailText
    when: DetailText
    then: DetailText


class TestCase(CitedItem):
    id: TestId
    feature_id: FeatureId
    criterion_id: AcceptanceId | None
    title: ShortText
    kind: Literal["happy_path", "boundary", "error"]
    preconditions: list[StepText] = Field(max_length=5)
    steps: list[StepText] = Field(min_length=1, max_length=8)
    expected_result: DetailText | None
    blocked_by: list[QuestionId] = Field(max_length=10)


class AnalysisData(StrictModel):
    summary: str = Field(min_length=1, max_length=600)
    features: list[Feature] = Field(max_length=10)
    questions: list[Question] = Field(max_length=10)
    acceptance_criteria: list[Acceptance] = Field(max_length=15)
    test_cases: list[TestCase] = Field(max_length=12)
    warnings: list[QuoteText] = Field(max_length=5)


class Usage(StrictModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class AnalysisMeta(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    prompt_version: Literal["1.0"] = "1.0"
    model: str
    latency_ms: int = Field(ge=0)
    usage: Usage = Field(default_factory=Usage)


class AnalysisResponse(StrictModel):
    request_id: UUID
    status: AnalysisStatus
    data: AnalysisData
    meta: AnalysisMeta
