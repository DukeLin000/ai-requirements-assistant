from base64 import b64decode
from binascii import Error as Base64Error
from datetime import date
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from app.analysis import AnalysisError, InvalidAnalysis, unique_ids
from app.schemas import AnalysisRequest, StrictModel


ATTACHMENT_LIMIT = 2 * 1024 * 1024
DIAGRAM_LIMIT = 512 * 1024
REQUEST_LIMIT = 3 * 1024 * 1024
Ticket = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
FunctionId = Annotated[str, StringConstraints(pattern=r"^F[0-9]{2,6}$")]
RuleId = Annotated[str, StringConstraints(pattern=r"^BR-[0-9]{2,6}$")]
QuestionId = Annotated[str, StringConstraints(pattern=r"^Q-[0-9]{2,6}$")]
CriterionId = Annotated[str, StringConstraints(pattern=r"^AC-[0-9]{2,6}$")]
Small = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
RequiredSmall = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Detail = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
SourceKind = Literal["requirement", "letter"]
SOURCE_LABELS = {"requirement": "需求文件", "letter": "公文"}


class WorkModel(StrictModel):
    @field_validator("*")
    @classmethod
    def require_document_text(cls, value: object) -> object:
        if isinstance(value, str):
            value.encode("utf-8")
            if any((ord(char) < 32 and char not in "\t\n\r") or char in "\ufffe\uffff" for char in value):
                raise ValueError("invalid document text")
        return value


class SourceAttachment(WorkModel):
    filename: RequiredSmall
    content: str = Field(min_length=1, max_length=4 * ((ATTACHMENT_LIMIT + 2) // 3))

    @field_validator("filename")
    @classmethod
    def require_local_filename(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.name != value or PureWindowsPath(value).name != value or "\r" in value or "\n" in value:
            raise ValueError("invalid filename")
        if path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
            raise ValueError("unsupported extension")
        return value

    def file_bytes(self) -> bytes:
        return decode_file(self.content, ATTACHMENT_LIMIT)


class Intake(WorkModel):
    number: Ticket
    title: RequiredSmall
    source_kind: SourceKind
    source_number: RequiredSmall
    source_name: RequiredSmall
    source_version: RequiredSmall
    requirement_text: str = Field(min_length=20, max_length=10_000)
    system_name: str | None = Field(default=None, max_length=80)
    attachment: SourceAttachment | None = None

    @field_validator("requirement_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return AnalysisRequest.model_validate({"requirement_text": value}).requirement_text


class AnalyzeSaved(WorkModel):
    revision: int = Field(ge=1)
    max_test_cases: int = Field(default=8, ge=1, le=12)


class Rule(WorkModel):
    id: RuleId
    text: Detail
    location: Small
    status: Literal["pending", "confirmed"]
    confirmation: Small


class Question(WorkModel):
    id: QuestionId
    text: Detail
    impact: Detail
    ruleIds: list[RuleId] = Field(max_length=40)
    decision: Detail
    confirmedBy: Small
    confirmedOn: Annotated[str, StringConstraints(pattern=r"^(\d{4}-\d{2}-\d{2})?$")]

    @field_validator("confirmedOn")
    @classmethod
    def check_date(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value


class Criterion(WorkModel):
    id: CriterionId
    ruleId: RuleId | Literal[""]
    given: Detail
    when: Detail
    then: Detail
    blockerIds: list[QuestionId] = Field(max_length=40)


class Function(WorkModel):
    id: FunctionId
    title: RequiredSmall
    rule_ids: list[RuleId] = Field(max_length=40)


class Review(WorkModel):
    goal: Detail = ""
    scope: Detail = ""
    rules: list[Rule] = Field(max_length=40)
    questions: list[Question] = Field(max_length=40)
    criteria: list[Criterion] = Field(max_length=60)


class SaveReview(WorkModel):
    revision: int = Field(ge=1)
    grade: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
    review: Review
    functions: list[Function] = Field(max_length=40)


class Scope(WorkModel):
    revision: int = Field(ge=1)
    function_ids: list[FunctionId] = Field(min_length=1, max_length=40)


class Diagram(WorkModel):
    id: RuleId | CriterionId
    png: str = Field(min_length=1, max_length=4 * ((DIAGRAM_LIMIT + 2) // 3))


class Export(Scope):
    diagram: Diagram | None = None


class Dispatch(Export):
    team: RequiredSmall


class Transition(WorkModel):
    action: Literal["accept", "return", "send_pg", "start_pg"]
    note: Small = ""


def decode_file(value: str, limit: int) -> bytes:
    try:
        data = b64decode(value, validate=True)
    except (ValueError, Base64Error) as exc:
        raise AnalysisError(422, "INVALID_FILE", "檔案內容格式不正確。") from exc
    if not data or len(data) > limit:
        raise AnalysisError(422, "INVALID_FILE", "檔案大小超出限制。")
    return data


def question_resolved(question: dict) -> bool:
    return bool(question["decision"] and question["confirmedBy"] and question["confirmedOn"])


def rule_confirmed(rule: dict) -> bool:
    return rule["status"] == "confirmed" and bool(rule["text"] and rule["confirmation"])


def criterion_status(criterion: dict, rules: dict, questions: dict) -> str:
    if not rule_confirmed(rules[criterion["ruleId"]]) or any(
        not question_resolved(questions[key]) for key in criterion["blockerIds"]
    ):
        return "待業務確認"
    if not all(criterion[field] for field in ("given", "when", "then")):
        return "待補驗收條件"
    return "可供驗收設計"


def validate_review(payload: SaveReview) -> None:
    try:
        rules = unique_ids(item.id for item in payload.review.rules)
        questions = unique_ids(item.id for item in payload.review.questions)
        unique_ids(item.id for item in payload.review.criteria)
        unique_ids(item.id for item in payload.functions)
        for item in payload.review.questions:
            if not unique_ids(item.ruleIds) <= rules:
                raise InvalidAnalysis("unknown rule")
        for item in payload.review.criteria:
            if (item.ruleId and item.ruleId not in rules) or not unique_ids(item.blockerIds) <= questions:
                raise InvalidAnalysis("unknown criterion reference")
        for item in payload.functions:
            if not unique_ids(item.rule_ids) <= rules:
                raise InvalidAnalysis("unknown function rule")
    except InvalidAnalysis as exc:
        raise AnalysisError(422, "INVALID_REVIEW_LINKS", "請核對規則、問題、驗收條件與功能的編號關聯。") from exc
