import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.analysis import AnalysisError
from app.ba_document import TEMPLATE_VERSION, diagram_image, document_content, document_scope, render_word
from app.workbench_models import Export, Intake, SaveReview, Scope, Transition, validate_review


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    number TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    filename TEXT,
    attachment BLOB,
    UNIQUE (kind, number, version)
);
CREATE TABLE IF NOT EXISTS requirements (
    number TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    requirement_text TEXT NOT NULL,
    system_name TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    grade TEXT NOT NULL DEFAULT '',
    analysis TEXT,
    review TEXT,
    functions TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    number TEXT NOT NULL REFERENCES requirements(number),
    version INTEGER NOT NULL,
    based_on_revision INTEGER NOT NULL,
    template_version TEXT NOT NULL,
    functions TEXT NOT NULL,
    word BLOB NOT NULL,
    team TEXT,
    status TEXT,
    feedback TEXT NOT NULL DEFAULT '',
    specification TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (number, version)
);
"""

_DOCUMENT_FIELDS = (
    "number, version, based_on_revision, template_version, functions, team, "
    "status, feedback, specification, created_at"
)
_TRANSITIONS = {
    "accept": ({"waiting_sa"}, "accepted"),
    "return": ({"waiting_sa", "accepted"}, "returned"),
    "send_pg": ({"accepted"}, "waiting_pg"),
    "start_pg": ({"waiting_pg"}, "in_development"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _document(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["functions"] = json.loads(result["functions"])
    return result


class WorkbenchStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialized = False
        self._lock = Lock()

    @contextmanager
    def _connection(self, *, write: bool = False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(self.path, timeout=3)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
                if not self._initialized:
                    with self._lock:
                        if not self._initialized:
                            db.execute("PRAGMA journal_mode = WAL")
                            db.executescript(_SCHEMA)
                            self._initialized = True
                with db:
                    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                    yield db
        except sqlite3.OperationalError as exc:
            if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise AnalysisError(503, "WORKBENCH_BUSY", "工作台正在儲存，請稍後再試。", retryable=True) from exc
            raise

    @staticmethod
    def require_revision(actual: int, expected: int) -> None:
        if actual != expected:
            raise AnalysisError(409, "REVISION_CONFLICT", "需求已有新版本。請核對最新版本後再儲存。")

    def _detail(self, db: sqlite3.Connection, number: str) -> dict:
        row = db.execute(
            "SELECT r.*, s.kind, s.number AS source_number, s.name AS source_name, "
            "s.version AS source_version, s.filename FROM requirements r "
            "JOIN sources s ON s.id = r.source_id WHERE r.number = ?",
            (number,),
        ).fetchone()
        if row is None:
            raise AnalysisError(404, "REQUIREMENT_NOT_FOUND", "找不到這筆需求單。")
        result = {key: row[key] for key in (
            "number", "title", "requirement_text", "system_name", "grade", "revision", "updated_at",
        )}
        result["source"] = {
            "kind": row["kind"], "number": row["source_number"], "name": row["source_name"],
            "version": row["source_version"], "filename": row["filename"],
        }
        for key in ("analysis", "review", "functions"):
            result[key] = json.loads(row[key]) if row[key] is not None else None
        result["documents"] = [_document(item) for item in db.execute(
            f"SELECT {_DOCUMENT_FIELDS} FROM documents WHERE number = ? ORDER BY version DESC", (number,),
        )]
        result["locked_functions"] = sorted({
            item["id"] for document in result["documents"]
            if document["status"] and document["status"] != "returned" for item in document["functions"]
        })
        return result

    def detail(self, number: str) -> dict:
        with self._connection() as db:
            return self._detail(db, number)

    def list_requirements(self, offset: int, limit: int) -> dict:
        with self._connection() as db:
            rows = db.execute(
                "SELECT r.number, r.title, r.grade, r.revision, r.updated_at, r.analysis IS NOT NULL AS analyzed, "
                "r.review IS NOT NULL AS reviewed, s.number AS source_number, s.version AS source_version, "
                "(SELECT count(*) FROM documents d WHERE d.number = r.number AND d.status <> 'returned') AS dispatches "
                "FROM requirements r JOIN sources s ON s.id = r.source_id "
                "ORDER BY r.updated_at DESC, r.number LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}

    def create(self, payload: Intake) -> dict:
        attachment = payload.attachment.file_bytes() if payload.attachment else None
        filename = payload.attachment.filename if payload.attachment else None
        with self._connection(write=True) as db:
            if db.execute("SELECT 1 FROM requirements WHERE number = ?", (payload.number,)).fetchone():
                raise AnalysisError(409, "DUPLICATE_REQUIREMENT", "這個需求單號已存在。")
            key = (payload.source_kind, payload.source_number, payload.source_version)
            source = db.execute(
                "SELECT id, name, filename, attachment FROM sources WHERE kind = ? AND number = ? AND version = ?", key,
            ).fetchone()
            if source is None:
                source_id = db.execute(
                    "INSERT INTO sources(kind, number, version, name, filename, attachment) VALUES (?, ?, ?, ?, ?, ?)",
                    (*key, payload.source_name, filename, attachment),
                ).lastrowid
            else:
                if source["name"] != payload.source_name or (
                    filename is not None and (filename != source["filename"] or attachment != source["attachment"])
                ):
                    raise AnalysisError(409, "SOURCE_VERSION_CONFLICT", "同一來源版次已有登錄內容，請核對文件或使用新版次。")
                source_id = source["id"]
            db.execute(
                "INSERT INTO requirements(number, title, source_id, requirement_text, system_name, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (payload.number, payload.title, source_id, payload.requirement_text,
                 (payload.system_name or "").strip() or None, _now()),
            )
            return self._detail(db, payload.number)

    def set_analysis(self, number: str, revision: int, analysis: dict) -> dict:
        with self._connection(write=True) as db:
            record = self._detail(db, number)
            self.require_revision(record["revision"], revision)
            if record["analysis"] is not None:
                raise AnalysisError(409, "ALREADY_ANALYZED", "這筆需求已有分析結果，請繼續 BA 審閱。")
            functions = [
                {"id": item["id"], "title": item["title"], "rule_ids": ["BR-" + item["id"][1:]]}
                for item in analysis["data"]["features"]
            ]
            db.execute(
                "UPDATE requirements SET analysis = ?, functions = ?, revision = revision + 1, updated_at = ? WHERE number = ?",
                (_json(analysis), _json(functions), _now(), number),
            )
            return self._detail(db, number)

    def save_review(self, number: str, payload: SaveReview) -> dict:
        validate_review(payload)
        functions = [item.model_dump() for item in payload.functions]
        with self._connection(write=True) as db:
            record = self._detail(db, number)
            self.require_revision(record["revision"], payload.revision)
            if record["analysis"] is None:
                raise AnalysisError(409, "ANALYSIS_REQUIRED", "請先執行需求分析。")
            old = {item["id"]: item for item in record["functions"]}
            new = {item["id"]: item for item in functions}
            if any(new.get(key) != old.get(key) for key in record["locked_functions"]):
                raise AnalysisError(409, "FUNCTION_ALREADY_DISPATCHED", "請保留已派發功能的名稱與規則關聯。")
            db.execute(
                "UPDATE requirements SET grade = ?, review = ?, functions = ?, revision = revision + 1, updated_at = ? "
                "WHERE number = ?",
                (payload.grade, _json(payload.review.model_dump()), _json(functions), _now(), number),
            )
            return self._detail(db, number)

    def preview(self, number: str, payload: Scope) -> dict:
        with self._connection() as db:
            record = self._detail(db, number)
            self.require_revision(record["revision"], payload.revision)
            scope = document_scope(record, payload.function_ids)
            return document_content(record, scope, None, None)

    def export(self, number: str, payload: Export, team: str | None = None) -> dict:
        with self._connection(write=True) as db:
            record = self._detail(db, number)
            self.require_revision(record["revision"], payload.revision)
            if team and set(payload.function_ids).intersection(record["locked_functions"]):
                raise AnalysisError(409, "FUNCTION_ALREADY_DISPATCHED", "所選功能已有承接紀錄，請調整派發範圍。")
            scope = document_scope(record, payload.function_ids, dispatch=team is not None)
            diagram = diagram_image(payload.diagram, scope)
            version = db.execute(
                "SELECT coalesce(max(version), 0) + 1 FROM documents WHERE number = ?", (number,),
            ).fetchone()[0]
            word = render_word(document_content(record, scope, version, team), diagram)
            now = _now()
            db.execute(
                "INSERT INTO documents(number, version, based_on_revision, template_version, functions, word, team, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (number, version, record["revision"], TEMPLATE_VERSION, _json(scope["functions"]), word,
                 team, "waiting_sa" if team else None, now),
            )
            db.execute("UPDATE requirements SET revision = revision + 1, updated_at = ? WHERE number = ?", (now, number))
            return self._detail(db, number)

    def list_dispatches(self, offset: int, limit: int) -> dict:
        with self._connection() as db:
            rows = db.execute(
                f"SELECT {_DOCUMENT_FIELDS} FROM documents WHERE status IS NOT NULL "
                "ORDER BY CASE status WHEN 'waiting_sa' THEN 0 WHEN 'waiting_pg' THEN 1 ELSE 2 END, "
                "created_at DESC, number, version DESC LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        return {"items": [_document(row) for row in rows[:limit]], "has_more": len(rows) > limit}

    def transition(self, number: str, version: int, payload: Transition) -> dict:
        previous, next_status = _TRANSITIONS[payload.action]
        if payload.action in {"return", "send_pg"} and not payload.note:
            message = "請填寫退回原因。" if payload.action == "return" else "請填寫 SA 規格的位置或編號。"
            raise AnalysisError(422, "NOTE_REQUIRED", message)
        with self._connection(write=True) as db:
            row = db.execute(
                f"SELECT {_DOCUMENT_FIELDS} FROM documents WHERE number = ? AND version = ? AND status IS NOT NULL",
                (number, version),
            ).fetchone()
            if row is None:
                raise AnalysisError(404, "DISPATCH_NOT_FOUND", "找不到這筆派發紀錄。")
            if row["status"] not in previous:
                raise AnalysisError(409, "INVALID_TRANSITION", "派發狀態已變更，請重新整理後操作。")
            result = _document(row)
            result["status"] = next_status
            if payload.action == "return":
                result["feedback"] = payload.note
            if payload.action == "send_pg":
                result["specification"] = payload.note
            db.execute(
                "UPDATE documents SET status = ?, feedback = ?, specification = ? WHERE number = ? AND version = ?",
                (next_status, result["feedback"], result["specification"], number, version),
            )
            return result

    def word(self, number: str, version: int) -> bytes:
        with self._connection() as db:
            row = db.execute("SELECT word FROM documents WHERE number = ? AND version = ?", (number, version)).fetchone()
            if row is None:
                raise AnalysisError(404, "DOCUMENT_NOT_FOUND", "找不到這個 BA 文件版次。")
            return row["word"]

    def source_file(self, number: str) -> tuple[str, bytes]:
        with self._connection() as db:
            row = db.execute(
                "SELECT s.filename, s.attachment FROM sources s JOIN requirements r ON r.source_id = s.id WHERE r.number = ?",
                (number,),
            ).fetchone()
            if row is None or row["attachment"] is None:
                raise AnalysisError(404, "ATTACHMENT_NOT_FOUND", "找不到來源附件。")
            return row["filename"], row["attachment"]
