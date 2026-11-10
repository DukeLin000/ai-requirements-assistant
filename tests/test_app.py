import asyncio
import copy
import json
import unittest
from pathlib import Path
from uuid import UUID

import httpx

from app.analysis import AnalysisService
from app.main import ANALYSIS_PATH, MAX_BODY_BYTES, create_app
from app.mock import MockAnalyzer
from app.schemas import AnalysisRequest


ORIGIN = "http://127.0.0.1:8000"
EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "static" / "example.json").read_text(encoding="utf-8")
)


class FixtureAnalyzer:
    name = "fixture"

    def __init__(self, result: object) -> None:
        self.result = result

    async def generate(self, request: AnalysisRequest) -> object:
        return self.result


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = self.make_client()

    def make_client(self, service: AnalysisService | None = None) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(service)),
            base_url=ORIGIN,
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_local_ui_and_health(self) -> None:
        page = await self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="analysis-form"', page.text)
        self.assertIn("default-src 'self'", page.headers["Content-Security-Policy"])
        self.assertEqual((await self.client.get("/healthz")).json(), {"status": "ok"})
        self.assertEqual((await self.client.get("/static/example.json")).json(), EXAMPLE)

    async def test_example_preserves_citations_and_pending_outcome(self) -> None:
        response = await self.client.post(ANALYSIS_PATH, json=EXAMPLE)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(str(UUID(result["request_id"])), response.headers["X-Request-ID"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["meta"]["model"], "mock")
        self.assertIsNone(result["meta"]["usage"]["input_tokens"])
        data = result["data"]
        self.assertEqual(len(data["features"]), 2)
        self.assertEqual(data["acceptance_criteria"][0]["feature_id"], "F01")
        first, second = data["test_cases"]
        self.assertEqual(first["criterion_id"], "A01")
        self.assertEqual(second["blocked_by"], ["Q01"])
        self.assertIsNone(second["expected_result"])
        for item in data["features"] + data["acceptance_criteria"] + data["test_cases"]:
            for quote in item["source_quotes"]:
                self.assertIn(quote, EXAMPLE["requirement_text"])

    async def test_request_is_normalized_and_case_limit_is_honored(self) -> None:
        raw = " \r\n" + EXAMPLE["requirement_text"] + "\r\n "
        response = await self.client.post(ANALYSIS_PATH, json={
            "system_name": "   ",
            "requirement_text": raw,
            "max_test_cases": 1,
        })
        self.assertEqual(response.status_code, 200)
        cases = response.json()["data"]["test_cases"]
        self.assertEqual([case["id"] for case in cases], ["T01"])

    async def test_arbitrary_text_is_treated_as_candidate_input(self) -> None:
        text = "管理員可以匯出交易摘要。\r\n使用者不得讀取其他人的報表。"
        response = await self.client.post(ANALYSIS_PATH, json={"requirement_text": text})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["features"][1]["source_quotes"], ["使用者不得讀取其他人的報表。"])
        self.assertTrue(all(feature["basis"] == "proposed" for feature in data["features"]))
        self.assertTrue(all(case["expected_result"] is None for case in data["test_cases"]))

    async def test_insufficient_input_has_no_features_or_tests(self) -> None:
        response = await self.client.post(ANALYSIS_PATH, json={"requirement_text": "今天的例會會議紀錄整理完畢，以上是工作進度報告。"})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "insufficient_input")
        self.assertEqual(result["data"]["features"], [])
        self.assertEqual(result["data"]["test_cases"], [])

    async def test_unicode_length_boundaries(self) -> None:
        for size, status in ((19, 422), (20, 200), (10_000, 200), (10_001, 422)):
            with self.subTest(size=size):
                body = json.dumps({"requirement_text": "😀" * size}, ensure_ascii=False).encode()
                response = await self.client.post(
                    ANALYSIS_PATH, content=body, headers={"Content-Type": "application/json"}
                )
                self.assertEqual(response.status_code, status, response.text)

    async def test_rejects_strict_type_errors_without_echoing_input(self) -> None:
        for change in (
            {"max_test_cases": True},
            {"max_test_cases": 1.0},
            {"max_test_cases": "8"},
            {"max_test_cases": 0},
            {"max_test_cases": 13},
            {"system_name": "a" * 81},
            {"requirement_text": "    "},
            {"system_name": "\ud800"},
            {"private-marker-field": "private-marker-value"},
        ):
            with self.subTest(change=list(change)):
                body = json.dumps({**EXAMPLE, **change}).encode()
                response = await self.client.post(
                    ANALYSIS_PATH, content=body, headers={"Content-Type": "application/json"}
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
                self.assertNotIn("private-marker", response.text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_checks_media_type_and_json_syntax(self) -> None:
        for body, headers, status, code in (
            (b"{", {"Content-Type": "application/json"}, 400, "INVALID_JSON"),
            (b'{"max_test_cases":NaN}', {"Content-Type": "application/json"}, 400, "INVALID_JSON"),
            (b"\xff", {"Content-Type": "application/json"}, 400, "INVALID_JSON"),
            (b"{}", {"Content-Type": "text/plain"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
            (b"{}", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ):
            with self.subTest(code=code):
                response = await self.client.post(ANALYSIS_PATH, content=body, headers=headers)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)

    async def test_actual_size_limit_includes_streamed_body(self) -> None:
        async def chunks():
            yield b" " * MAX_BODY_BYTES
            yield b" "

        response = await self.client.post(
            ANALYSIS_PATH, content=chunks(), headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "PAYLOAD_TOO_LARGE")

    async def test_loopback_host_and_same_origin(self) -> None:
        response = await self.client.get("/", headers={"Host": "example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_HOST")
        response = await self.client.post(ANALYSIS_PATH, json=EXAMPLE, headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "ORIGIN_NOT_ALLOWED")
        response = await self.client.post(ANALYSIS_PATH, json=EXAMPLE, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)

    async def test_invalid_output_is_rejected_and_next_request_can_run(self) -> None:
        good = await MockAnalyzer().generate(AnalysisRequest.model_validate(EXAMPLE))
        invalid_outputs = []
        for change in ("quote", "empty_quote", "duplicate_id", "criterion", "blocker", "result"):
            data = copy.deepcopy(good)
            if change == "quote":
                data["features"][0]["source_quotes"] = ["private-model-marker"]
            elif change == "empty_quote":
                data["features"][0]["source_quotes"] = []
            elif change == "duplicate_id":
                data["features"][1]["id"] = "F01"
            elif change == "criterion":
                data["test_cases"][1]["criterion_id"] = "A01"
            elif change == "blocker":
                data["test_cases"][1]["blocked_by"] = ["Q99"]
            else:
                data["test_cases"][1]["expected_result"] = "private-model-marker"
            invalid_outputs.append(data)

        analyzer = FixtureAnalyzer(good)
        client = self.make_client(AnalysisService(analyzer))
        for invalid in invalid_outputs:
            analyzer.result = invalid
            response = await client.post(ANALYSIS_PATH, json=EXAMPLE)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(response.json()["error"]["code"], "INVALID_LLM_OUTPUT")
            self.assertNotIn("private-model-marker", response.text)
        analyzer.result = good
        self.assertEqual((await client.post(ANALYSIS_PATH, json=EXAMPLE)).status_code, 200)

    async def test_busy_and_cancellation_release_the_analysis_slot(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class PausedAnalyzer(MockAnalyzer):
            async def generate(self, request: AnalysisRequest) -> object:
                started.set()
                await release.wait()
                return await super().generate(request)

        client = self.make_client(AnalysisService(PausedAnalyzer()))
        task = asyncio.create_task(client.post(ANALYSIS_PATH, json=EXAMPLE))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            busy = await client.post(ANALYSIS_PATH, json=EXAMPLE)
            self.assertEqual(busy.status_code, 429)
            self.assertEqual(busy.headers["Retry-After"], "3")
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
        self.assertEqual((await client.post(ANALYSIS_PATH, json=EXAMPLE)).status_code, 200)

    async def test_timeout_releases_the_analysis_slot(self) -> None:
        class SlowFirstAnalyzer(MockAnalyzer):
            started = False

            async def generate(self, request: AnalysisRequest) -> object:
                if not self.started:
                    self.started = True
                    await asyncio.Future()
                return await super().generate(request)

        client = self.make_client(AnalysisService(SlowFirstAnalyzer(), timeout_seconds=0.05))
        response = await client.post(ANALYSIS_PATH, json=EXAMPLE)
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "LLM_TIMEOUT")
        self.assertEqual((await client.post(ANALYSIS_PATH, json=EXAMPLE)).status_code, 200)


if __name__ == "__main__":
    unittest.main()
