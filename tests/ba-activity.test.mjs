import assert from "node:assert/strict";
import test from "node:test";
import { activityOptions, activityPlantUml, buildActivity } from "../app/static/ba-activity.mjs";
import { activitySvg } from "../app/static/activity-svg.mjs";
import { createBaDraft } from "../app/static/ba-draft.mjs";

function reportResult() {
  const description = "分行作業人員可以查詢並下載所屬分行的每日交易彙總報表，格式為 Excel。";
  return {
    request_id: "7f4a60cc-bff0-448c-89fc-7e84d89c2b9d",
    data: {
      features: [{
        id: "F01", basis: "proposed", description, source_quotes: [description],
        activity: {
          steps: [{ actor: "分行作業人員", action: "查詢每日交易報表" }],
          branch: {
            condition: "查到報表資料？", otherwise_question_id: "Q01",
            when_true: [{ actor: "系統", action: "輸出 Excel", guard: "分行作業人員選擇下載？" }],
          },
        },
      }],
      questions: [{ id: "Q01", question: "查無交易時，是否可下載 Excel？", reason: "影響驗收", related_feature_ids: ["F01"] }],
      acceptance_criteria: [],
      test_cases: [],
    },
  };
}

function reviewedReport() {
  const draft = createBaDraft(reportResult());
  Object.assign(draft.meta, { requestNumber: "REQ-2026-001", sourceName: "分行交易報表需求書", sourceVersion: "v1.2" });
  return draft;
}

test("report keeps the optional download and an unresolved branch tied to the BA question", () => {
  const result = reportResult();
  const draft = createBaDraft(result);
  assert.deepEqual(activityOptions(draft), [["BR-01", "BR-01 規則流程"]]);
  assert.equal(result.data.features[0].activity.branch.otherwise_question_id, "Q01");
  const flow = buildActivity(draft, "BR-01");
  const decision = flow.steps[1];
  assert.equal(decision.label, "查到報表資料？");
  assert.equal(decision.yes[0].label, "分行作業人員選擇下載？");
  assert.deepEqual(decision.yes[0].no, []);
  assert.equal(decision.no[0].reference, "Q-01");
  assert.equal(decision.no[0].pending, true);
  assert.deepEqual(flow.questionLabels, ["Q-01 待確認"]);
});

test("a branch updates only after its linked decision is fully recorded", () => {
  const draft = reviewedReport();
  const question = draft.questions[0];
  const decision = "顯示查無交易並停用 Excel 下載。";
  Object.assign(question, { decision, confirmedBy: "測試業務代表" });
  assert.ok(!activityPlantUml(buildActivity(draft, "BR-01")).includes(decision));
  question.confirmedOn = "2026-11-10";
  const flow = buildActivity(draft, "BR-01");
  assert.deepEqual(flow.questionLabels, ["Q-01 已確認"]);
  assert.equal(flow.steps[1].no[0].pending, false);
  assert.ok(activityPlantUml(flow).includes("Q-01 · 業務決議｜" + decision));

  question.ruleIds = [];
  const detached = buildActivity(draft, "BR-01");
  assert.deepEqual(detached.questionLabels, []);
  assert.equal(detached.steps[1].no[0].pending, true);
  assert.ok(!activityPlantUml(detached).includes(decision));
});

test("editing a rule invalidates its suggested ordering and leaves independent BA scenarios", () => {
  const draft = reviewedReport();
  draft.rules[0].text = "分行作業人員可查詢報表。";
  assert.equal(buildActivity(draft, "BR-01").status, "待補流程步驟");
  assert.doesNotMatch(activityPlantUml(buildActivity(draft, "BR-01")), /輸出 Excel|^if /m);
  draft.criteria.push({ id: "AC-01", ruleId: "BR-01", given: "人員登入", when: "查詢報表", then: "顯示查詢結果", blockerIds: [] });
  assert.match(activityOptions(draft)[1][1], /AC-01.*BR-01/);
  assert.equal(buildActivity(draft, "AC-01").status, "待業務確認");
  assert.doesNotMatch(activityPlantUml(buildActivity(draft, "AC-01")), /顯示查詢結果/);
  Object.assign(draft.rules[0], { status: "confirmed", confirmation: "業務確認紀錄" });
  assert.equal(buildActivity(draft, "AC-01").status, "可供驗收設計");
  assert.match(activityPlantUml(buildActivity(draft, "AC-01")), /BR-01 · AC-01 · 預期結果｜顯示查詢結果/);
});

test("exports carry the requirement version and keep hostile text inside diagram labels", () => {
  const draft = reviewedReport();
  Object.assign(draft.questions[0], {
    decision: "<script>\n!include a\n@enduml\n); %load_json(a)",
    confirmedBy: "測試業務代表", confirmedOn: "2026-11-10",
  });
  const flow = buildActivity(draft, "BR-01");
  const source = activityPlantUml(flow);
  assert.match(source, /^@startuml\ntitle .+\nstart\n/);
  assert.match(source, /if \(查到報表資料？\) then \(是\)/);
  assert.match(source, /else \(否\)/);
  assert.match(source, /需求單號　REQ-2026-001/);
  assert.match(source, /來源版次　v1\.2/);
  assert.equal(source.match(/^@enduml$/gm).length, 1);
  assert.doesNotMatch(source, /<script|!include|%load_json/);

  const svg = activitySvg(flow);
  assert.match(svg, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.match(svg, /viewBox="0 0 \d+ \d+"/);
  assert.match(svg, /REQ-2026-001/);
  assert.match(svg, /v1\.2/);
  assert.match(svg, /&lt;script/);
  assert.doesNotMatch(svg, /<script|(?:src|href)="/);
});
