import asyncio
import unittest
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from docx import Document
from PIL import Image

from app.main import create_app


PATH = "/api/v1/requirements"
NUMBER = "REQ-2026-001"
ITEM = f"{PATH}/{NUMBER}"
REPORT = "分行作業人員可以查詢並下載所屬分行的每日交易彙總報表，格式為 Excel。"
SOURCE = {
    "number": NUMBER, "title": "分行交易報表", "source_kind": "letter", "source_number": "測字第 001 號",
    "source_name": "報表需求公文", "source_version": "v1.2", "requirement_text": REPORT,
}


def review_payload(revision: int) -> dict:
    return {
        "revision": revision,
        "grade": "高",
        "functions": [
            {"id": "F01", "title": "分行查詢", "rule_ids": ["BR-01"]},
            {"id": "F02", "title": "Excel 下載", "rule_ids": ["BR-02"]},
        ],
        "review": {
            "goal": "供分行查詢每日交易", "scope": "分行交易報表",
            "rules": [
                {"id": "BR-01", "text": "可查詢所屬分行。", "location": "第一段", "status": "confirmed", "confirmation": "業務會議"},
                {"id": "BR-02", "text": "報表可下載為 Excel。", "location": "", "status": "pending", "confirmation": ""},
            ],
            "questions": [
                {"id": "Q-01", "text": "查無交易可否下載？", "impact": "", "ruleIds": ["BR-02"],
                 "decision": "", "confirmedBy": "", "confirmedOn": ""},
            ],
            "criteria": [
                {"id": "AC-01", "ruleId": "BR-01", "given": "分行人員登入", "when": "查詢", "then": "顯示所屬分行", "blockerIds": []},
                {"id": "AC-02", "ruleId": "BR-02", "given": "查無交易", "when": "下載", "then": "", "blockerIds": ["Q-01"]},
            ],
        },
    }


def example_png() -> str:
    stream = BytesIO()
    Image.new("RGB", (48, 24), "white").save(stream, "PNG")
    return b64encode(stream.getvalue()).decode("ascii")


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db_path = Path(folder.name) / "workbench.sqlite3"
        self.client = self.make_client()

    def make_client(self):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(db_path=self.db_path)),
            base_url="http://127.0.0.1:8000",
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def prepare_review(self) -> int:
        intake = await self.client.post(PATH, json=SOURCE)
        self.assertEqual(intake.status_code, 201, intake.text)
        analyze = await self.client.post(ITEM + "/analysis", json={"revision": 1})
        self.assertEqual(analyze.status_code, 200, analyze.text)
        self.assertEqual(analyze.json()["functions"][0]["rule_ids"], ["BR-01"])
        review = await self.client.put(ITEM + "/review", json=review_payload(analyze.json()["revision"]))
        self.assertEqual(review.status_code, 200, review.text)
        return review.json()["revision"]

    async def test_same_source_can_be_reused_and_reopened(self):
        content = "測試公文內容".encode("utf-8")
        attachment = {"filename": "測試公文.txt", "content": b64encode(content).decode("ascii")}
        response = await self.client.post(PATH, json={**SOURCE, "attachment": attachment})
        self.assertEqual(response.status_code, 201, response.text)
        duplicate = await self.client.post(PATH, json=SOURCE)
        self.assertEqual(duplicate.json()["error"]["code"], "DUPLICATE_REQUIREMENT")
        other = await self.client.post(PATH, json={**SOURCE, "number": "REQ-2026-002", "title": "分行下載需求"})
        self.assertEqual(other.status_code, 201, other.text)
        conflict = await self.client.post(PATH, json={**SOURCE, "number": "REQ-2026-003", "source_name": "不同版本內容"})
        self.assertEqual(conflict.json()["error"]["code"], "SOURCE_VERSION_CONFLICT")
        for number in (NUMBER, "REQ-2026-002"):
            response = await self.client.get(f"{PATH}/{number}/source")
            self.assertEqual(response.content, content)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))
        restarted = self.make_client()
        listing = await restarted.get(PATH + "?limit=1")
        self.assertTrue(listing.json()["has_more"])
        self.assertEqual((await restarted.get(ITEM)).json()["source"]["filename"], attachment["filename"])

    async def test_word_partial_dispatch_and_sa_pg_transitions(self):
        revision = await self.prepare_review()
        first = {"revision": revision, "function_ids": ["F01"]}
        preview = (await self.client.post(ITEM + "/preview", json=first)).json()
        self.assertIn("分行查詢", str(preview["sections"]))
        self.assertNotIn("Excel 下載", str(preview["sections"]))
        bad = await self.client.post(ITEM + "/dispatches", json={**first, "function_ids": ["F02"], "team": "開發組"})
        self.assertEqual(bad.json()["error"]["code"], "RULE_PENDING")
        dispatch = await self.client.post(ITEM + "/dispatches", json={
            **first, "team": "開發組", "diagram": {"id": "BR-01", "png": example_png()},
        })
        self.assertEqual(dispatch.status_code, 201, dispatch.text)
        record = dispatch.json()
        self.assertEqual(record["locked_functions"], ["F01"])
        word = await self.client.get(ITEM + "/documents/1")
        document = Document(BytesIO(word.content))
        self.assertEqual(len(document.inline_shapes), 1)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("分行查詢", text)
        self.assertIn("顯示所屬分行", text)
        self.assertNotIn("F02", text)
        self.assertTrue(word.headers["content-type"].endswith("wordprocessingml.document"))
        self.assertIn("BA-v1.docx", word.headers["content-disposition"])
        retry = await self.client.post(ITEM + "/dispatches", json={**first, "team": "開發組"})
        self.assertEqual(retry.json()["error"]["code"], "REVISION_CONFLICT")
        overlap = await self.client.post(ITEM + "/dispatches", json={**first, "revision": record["revision"], "team": "開發組"})
        self.assertEqual(overlap.json()["error"]["code"], "FUNCTION_ALREADY_DISPATCHED")

        transition = ITEM + "/dispatches/1/transition"
        invalid = await self.client.post(transition, json={"action": "start_pg"})
        self.assertEqual(invalid.json()["error"]["code"], "INVALID_TRANSITION")
        for action, note, expected in [
            ("accept", "", "accepted"),
            ("send_pg", "SA-SPEC-001 v1", "waiting_pg"),
            ("start_pg", "", "in_development"),
        ]:
            response = await self.client.post(transition, json={"action": action, "note": note})
            self.assertEqual(response.json()["status"], expected)
        self.assertEqual((await self.client.get(ITEM + "/documents/1")).content, word.content)

    async def test_return_allows_new_dispatch_and_preserves_old_document(self):
        revision = await self.prepare_review()
        first = {"revision": revision, "function_ids": ["F01"], "team": "開發組"}
        dispatch = await self.client.post(ITEM + "/dispatches", json=first)
        version_one = (await self.client.get(ITEM + "/documents/1")).content
        invalid = await self.client.post(ITEM + "/dispatches/1/transition", json={"action": "return"})
        self.assertEqual(invalid.json()["error"]["code"], "NOTE_REQUIRED")
        returned = await self.client.post(ITEM + "/dispatches/1/transition", json={"action": "return", "note": "請補查詢日期"})
        self.assertEqual(returned.json()["feedback"], "請補查詢日期")
        amended = review_payload(dispatch.json()["revision"])
        amended["review"]["criteria"][0]["given"] = "分行人員選定營業日"
        saved = await self.client.put(ITEM + "/review", json=amended)
        self.assertEqual(saved.status_code, 200, saved.text)
        second = await self.client.post(ITEM + "/dispatches", json={**first, "revision": saved.json()["revision"]})
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual([doc["status"] for doc in second.json()["documents"]], ["waiting_sa", "returned"])
        self.assertEqual((await self.client.get(ITEM + "/documents/1")).content, version_one)
        latest = Document(BytesIO((await self.client.get(ITEM + "/documents/2")).content))
        self.assertIn("分行人員選定營業日", "\n".join(p.text for p in latest.paragraphs))

    async def test_export_is_atomic_on_invalid_diagram_and_retried_revision(self):
        revision = await self.prepare_review()
        body = {"revision": revision, "function_ids": ["F01"]}
        failure = await self.client.post(ITEM + "/documents", json={
            **body, "diagram": {"id": "BR-01", "png": b64encode(b"invalid").decode("ascii")},
        })
        self.assertEqual(failure.json()["error"]["code"], "INVALID_DIAGRAM")
        self.assertEqual((await self.client.get(ITEM)).json()["documents"], [])
        results = await asyncio.gather(*[self.client.post(ITEM + "/documents", json=body) for _ in range(2)])
        self.assertEqual(sorted(result.status_code for result in results), [201, 409])
        record = (await self.client.get(ITEM)).json()
        self.assertEqual([doc["version"] for doc in record["documents"]], [1])
        rejected = await self.client.put(ITEM + "/review", json=review_payload(record["revision"]),
                                         headers={"Origin": "https://example.com"})
        self.assertEqual(rejected.status_code, 403)
