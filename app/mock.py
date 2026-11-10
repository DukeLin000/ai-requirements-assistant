import json
import re
from pathlib import Path

from app.schemas import AnalysisRequest


_EXAMPLE = json.loads(
    (Path(__file__).parent / "static" / "example.json").read_text(encoding="utf-8")
)["requirement_text"]
_SENTENCES = re.compile(r"(?<=[。！？])|\n+|(?<=[.!?])(?=\s|$)")
_REQUIREMENT_WORD = re.compile(
    r"可以|必須|不得|應|允許|禁止|最多|最少|限一|需要|須|只可|只能|支援|"
    r"\b(?:must|shall|may|can|cannot|allows?|supports?|requires?)\b",
    re.IGNORECASE,
)
_REPORT_FLOW = re.compile(
    r"(?P<actor>[\w ]{1,32}?)(?:可以|可)\s*查詢\s*(?:並|及|與)\s*下載\s*"
    r"(?P<report>[^，,。；;]{0,75}報表)[，,]\s*格式為\s*(?P<format>Excel|CSV|PDF)\s*[。.]?",
    re.IGNORECASE,
)
_ORDERED_FLOW = re.compile(
    r"(?P<actor>[\w ]{1,32}?)(?:必須|需要|須|應)先(?P<first>[^，,。；;]{1,80})[，,]\s*"
    r"(?:再|接著|然後)(?P<second>[^，,。；;]{1,90})[。.]?",
)
_DEFAULT_QUESTION = "請確認此片段的觸發條件、可驗收結果與例外規則。"
_NEGATIVE_SUFFIXES = ("不", "未", "無")


def _suggest_activity(fragment: str, question_id: str) -> tuple[dict[str, object] | None, str]:
    report = _REPORT_FLOW.fullmatch(fragment)
    if report and not report["actor"].strip().endswith(_NEGATIVE_SUFFIXES):
        actor, name, file_format = report.group("actor", "report", "format")
        empty = "查無交易" if "交易" in name else "查無資料"
        return {
            "steps": [
                {"actor": actor, "action": f"查詢{name}"},
                {"actor": "系統", "action": f"取得{name}資料"},
            ],
            "branch": {
                "condition": "查到報表資料？",
                "when_true": [
                    {"actor": "系統", "action": "顯示報表"},
                    {"actor": "系統", "action": f"輸出 {file_format} 檔案", "guard": f"{actor}選擇下載？"},
                ],
                "otherwise_question_id": question_id,
            },
        }, f"{empty}時，是否可下載 {file_format}？"

    ordered = _ORDERED_FLOW.fullmatch(fragment)
    if ordered and not ordered["actor"].strip().endswith(_NEGATIVE_SUFFIXES):
        actor, first, second = ordered.group("actor", "first", "second")
        return {"steps": [
            {"actor": actor, "action": first},
            {"actor": "角色待確認", "action": second.rstrip(".")},
        ]}, _DEFAULT_QUESTION

    return None, _DEFAULT_QUESTION


class MockAnalyzer:
    name = "mock"

    async def generate(self, request: AnalysisRequest) -> object:
        if request.requirement_text == _EXAMPLE:
            return self._reservation_example(request.max_test_cases)

        candidates = [
            part.strip()
            for part in _SENTENCES.split(request.requirement_text)
            if _REQUIREMENT_WORD.search(part)
        ]
        fragments = [part for part in candidates if len(part) <= 200]
        features = []
        questions = []
        test_cases = []
        warnings = []
        if len(fragments) < len(candidates):
            warnings.append("有需求片段超過引用長度，請分句後重新分析。")
        if len(fragments) > 10:
            warnings.append("本次顯示前 10 個候選片段，其他內容可分批分析。")

        for index, fragment in enumerate(fragments[:10], start=1):
            feature_id = f"F{index:02d}"
            question_id = f"Q{index:02d}"
            activity, question = _suggest_activity(fragment, question_id)
            features.append({
                "id": feature_id,
                "title": f"需求片段 {index}",
                "description": fragment,
                "basis": "proposed",
                "source_quotes": [fragment],
                "activity": activity,
            })
            questions.append({
                "id": question_id,
                "question": question,
                "reason": "確認後才能制定可執行的驗收條件。",
                "related_feature_ids": [feature_id],
            })
            if index <= request.max_test_cases:
                test_cases.append({
                    "id": f"T{index:02d}",
                    "feature_id": feature_id,
                    "criterion_id": None,
                    "title": f"確認需求片段 {index} 的基本情境",
                    "kind": "happy_path",
                    "preconditions": [],
                    "steps": ["依確認後的條件，操作對應的功能並記錄結果。"],
                    "expected_result": None,
                    "blocked_by": [question_id],
                    "basis": "proposed",
                    "source_quotes": [fragment],
                })

        summary = f"擷取到 {len(features)} 個候選需求片段，請先確認業務規則。"
        if not features:
            summary = "找不到可整理的需求片段。請補上使用者、系統動作或明確的限制。"
        return {
            "summary": summary,
            "features": features,
            "questions": questions,
            "acceptance_criteria": [],
            "test_cases": test_cases,
            "warnings": warnings,
        }

    @staticmethod
    def _reservation_example(max_test_cases: int) -> dict[str, object]:
        return {
            "summary": "會員可預約時段並取消自己的預約。取消期限仍待確認。",
            "features": [
                {
                    "id": "F01",
                    "title": "預約時段",
                    "description": "會員可預約時段，每個時段最多一人。",
                    "basis": "explicit",
                    "source_quotes": ["會員可以預約時段。", "每時段限一人。"],
                },
                {
                    "id": "F02",
                    "title": "取消預約",
                    "description": "會員可以取消自己的預約。",
                    "basis": "explicit",
                    "source_quotes": ["會員可以取消自己的預約。"],
                },
            ],
            "questions": [{
                "id": "Q01",
                "question": "取消預約的時間限制為何？",
                "reason": "需要先確認取消期限，才能判定時間邊界的預期結果。",
                "related_feature_ids": ["F02"],
            }],
            "acceptance_criteria": [{
                "id": "A01",
                "feature_id": "F01",
                "given": "某時段已有一筆有效預約。",
                "when": "另一位會員嘗試預約同一時段。",
                "then": "該時段的有效預約人數維持不超過一人。",
                "basis": "explicit",
                "source_quotes": ["每時段限一人。"],
            }],
            "test_cases": [
                {
                    "id": "T01",
                    "feature_id": "F01",
                    "criterion_id": "A01",
                    "title": "同一時段已有預約",
                    "kind": "error",
                    "preconditions": ["測試時段已有一位會員的有效預約。"],
                    "steps": ["由另一位會員嘗試預約同一時段。", "確認該時段的有效預約人數。"],
                    "expected_result": "該時段的有效預約人數不超過一人。",
                    "blocked_by": [],
                    "basis": "explicit",
                    "source_quotes": ["每時段限一人。"],
                },
                {
                    "id": "T02",
                    "feature_id": "F02",
                    "criterion_id": None,
                    "title": "時段開始前一分鐘取消預約",
                    "kind": "boundary",
                    "preconditions": ["會員有一筆屬於自己的有效預約。"],
                    "steps": ["在時段開始前一分鐘嘗試取消該預約。"],
                    "expected_result": None,
                    "blocked_by": ["Q01"],
                    "basis": "proposed",
                    "source_quotes": ["會員可以取消自己的預約。"],
                },
            ][:max_test_cases],
            "warnings": [],
        }
