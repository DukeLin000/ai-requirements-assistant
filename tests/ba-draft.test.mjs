import assert from "node:assert/strict";
import test from "node:test";
import {
  acceptanceStatus, baHandoffMarkdown, createBaDraft, questionResolved, restoreBaDraft, reviewSnapshot, validateBaDraft,
} from "../app/static/ba-draft.mjs";

const result = {
  request_id: "7f4a60cc-bff0-448c-89fc-7e84d89c2b9d",
  system_name: "分行交易報表",
  data: {
    features: [
      { id: "F01", description: "分行人員可查詢所屬分行的報表。", basis: "explicit", source_quotes: ["只能查詢所屬分行。"] },
      { id: "F02", description: "報表可下載為 Excel。", basis: "proposed", source_quotes: ["格式為 Excel。"] },
    ],
    questions: [{ id: "Q01", question: "空報表可否下載？", reason: "影響下載驗收。", related_feature_ids: ["F02"] }],
    acceptance_criteria: [
      { id: "A01", feature_id: "F01", given: "分行人員登入", when: "查詢報表", then: "只顯示所屬分行" },
      { id: "A02", feature_id: "F02", given: "查詢結果為空", when: "下載報表", then: "顯示對應提示" },
    ],
    test_cases: [
      { criterion_id: "A01", blocked_by: [] },
      { criterion_id: "A02", blocked_by: ["Q01"] },
      { criterion_id: "A02", blocked_by: ["Q01"] },
    ],
  },
};

function review() {
  const draft = createBaDraft(result);
  Object.assign(draft.meta, { requestNumber: "REQ-001", sourceName: "分行交易報表需求書", sourceVersion: "v2" });
  for (const rule of draft.rules) Object.assign(rule, { status: "confirmed", confirmation: "業務確認紀錄" });
  return draft;
}

test("copy rules, original citations and selective blockers into a separate BA draft", () => {
  const draft = createBaDraft(result);
  assert.equal(draft.rules[0].status, "pending");
  assert.equal(draft.rules[0].id, "BR-01");
  assert.deepEqual(draft.questions[0].ruleIds, ["BR-02"]);
  assert.deepEqual(draft.criteria[1].blockerIds, ["Q-01"]);
  draft.rules[0].text = "已補充查詢權限。";
  draft.rules[0].quotes.push("測試資料");
  assert.equal(result.data.features[0].description, "分行人員可查詢所屬分行的報表。");
  assert.deepEqual(result.data.features[0].source_quotes, ["只能查詢所屬分行。"]);
});

test("saved BA edits restore against the original citations and keep read-only metadata", () => {
  const draft = review();
  draft.rules[0].text = "核對後的分行查詢規則。";
  const saved = reviewSnapshot(draft);
  assert.equal("quotes" in saved.rules[0], false);
  const restored = restoreBaDraft(result, {
    number: "REQ-2026-010",
    source: { name: "測試公文", version: "v1.2" },
    review: saved,
  });
  assert.equal(restored.meta.requestNumber, "REQ-2026-010");
  assert.equal(restored.meta.sourceVersion, "v1.2");
  assert.equal(restored.rules[0].text, "核對後的分行查詢規則。");
  assert.deepEqual(restored.rules[0].quotes, ["只能查詢所屬分行。"]);
  restored.questions[0].ruleIds.push("BR-01");
  assert.deepEqual(saved.questions[0].ruleIds, ["BR-02"]);
});

test("a partial decision blocks only its related acceptance criterion", () => {
  const draft = review();
  const [query, download] = draft.criteria;
  assert.equal(acceptanceStatus(draft, query), "ready");
  assert.equal(acceptanceStatus(draft, download), "blocked");
  const [question] = draft.questions;
  question.decision = "空報表仍可下載。";
  question.confirmedBy = "業務代表";
  assert.equal(questionResolved(question), false);
  assert.equal(acceptanceStatus(draft, download), "blocked");
  question.confirmedOn = "2026-11-10";
  assert.equal(acceptanceStatus(draft, download), "ready");
  download.then = "   ";
  assert.equal(acceptanceStatus(draft, download), "incomplete");
  draft.rules[0].confirmation = " ";
  assert.equal(acceptanceStatus(draft, query), "blocked");
});

test("export rejects missing required metadata, invalid dates, and dangling references", () => {
  const draft = createBaDraft(result);
  assert.match(validateBaDraft(draft), /需求單號/);
  const reviewed = review();
  reviewed.questions[0].confirmedOn = "2026-02-29";
  assert.match(validateBaDraft(reviewed), /有效的確認日期/);
  reviewed.questions[0].confirmedOn = "";
  reviewed.criteria[1].blockerIds.push("Q-99");
  assert.match(validateBaDraft(reviewed), /重新選擇/);
  reviewed.criteria[1].blockerIds.pop();
  reviewed.criteria[1].ruleId = "BR-99";
  assert.match(validateBaDraft(reviewed), /選擇關聯規則/);
});

test("export distinguishes proposed decisions and escapes user-supplied markup", () => {
  const draft = review();
  draft.meta.goal = "<img src=x>\n# 測試 | [連結](https://example.com)";
  draft.rules[0].text = "可以查詢自己的分行。";
  draft.questions[0].decision = "空報表仍可下載。";
  assert.equal(validateBaDraft(draft), null);
  const report = baHandoffMarkdown(draft);
  assert.match(report, /^# BA 需求交接稿/m);
  assert.ok(report.includes("需求單號　REQ\\-001"));
  assert.ok(report.includes("&lt;img src=x&gt; \\# 測試 \\| \\[連結\\]"));
  assert.ok(report.includes("擷取時原文引用　只能查詢所屬分行。"));
  assert.match(report, /決議草稿　空報表仍可下載。/);
  assert.match(report, /AC-01 · BR-01\n\n- 狀態　可供驗收設計/);
  assert.match(report, /AC-02 · BR-02\n\n- 狀態　待業務確認/);
  assert.doesNotMatch(report, /<img|^# 測試/m);
});

test("a BA can supplement an analysis that did not extract any rules", () => {
  const draft = createBaDraft({
    ...result,
    data: { features: [], questions: [], acceptance_criteria: [], test_cases: [] },
  });
  Object.assign(draft.meta, review().meta);
  assert.match(validateBaDraft(draft), /至少整理一條業務規則/);
  draft.rules.push({ id: "BR-01", text: "補充規則", basis: "manual", quotes: [], location: "", status: "pending", confirmation: "" });
  assert.equal(validateBaDraft(draft), null);
  const report = baHandoffMarkdown(draft);
  assert.match(report, /擷取依據　BA 新增/);
  assert.match(report, /## 驗收條件\n\n待補/);
});
