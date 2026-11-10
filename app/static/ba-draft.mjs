export const ACCEPTANCE_LABELS = {
  blocked: "待業務確認",
  incomplete: "待補驗收條件",
  ready: "可供驗收設計",
};

const SOURCE_LABELS = { explicit: "原文明示", proposed: "建議草稿", manual: "BA 新增" };
const ruleId = (id) => "BR-" + id.slice(1);
const questionId = (id) => "Q-" + id.slice(1);

export function escapeMarkdown(text) {
  return text.replace(/[\r\n]+/g, " ")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\\*_{}\[\]()#+\-.!|\x60]/g, "\\$&");
}

export function createBaDraft(result) {
  const data = result.data;
  return {
    requestId: result.request_id,
    systemName: result.system_name || "",
    meta: { requestNumber: "", sourceName: "", sourceVersion: "", goal: "", scope: "" },
    rules: data.features.map((feature) => {
      const plan = feature.activity ? structuredClone(feature.activity) : null;
      if (plan?.branch) plan.branch.otherwise_question_id = questionId(plan.branch.otherwise_question_id);
      return {
        id: ruleId(feature.id),
        text: feature.description,
        basis: feature.basis,
        quotes: [...feature.source_quotes],
        location: "",
        status: "pending",
        confirmation: "",
        activitySeed: plan ? { text: feature.description, plan } : null,
      };
    }),
    questions: data.questions.map((question) => ({
      id: questionId(question.id),
      text: question.question,
      impact: question.reason,
      ruleIds: question.related_feature_ids.map(ruleId),
      decision: "",
      confirmedBy: "",
      confirmedOn: "",
    })),
    criteria: data.acceptance_criteria.map((criterion) => ({
      id: "AC-" + criterion.id.slice(1),
      ruleId: ruleId(criterion.feature_id),
      given: criterion.given,
      when: criterion.when,
      then: criterion.then,
      blockerIds: [...new Set(data.test_cases
        .filter((test) => test.criterion_id === criterion.id)
        .flatMap((test) => test.blocked_by.map(questionId)))],
    })),
  };
}

export function sourceLabel(rule) {
  return SOURCE_LABELS[rule.basis];
}

export function restoreBaDraft(result, record) {
  const draft = createBaDraft(result);
  if (!record) return draft;
  draft.meta.requestNumber = record.number;
  draft.meta.sourceName = record.source.name;
  draft.meta.sourceVersion = record.source.version;
  if (!record.review) return draft;
  const sourceRules = new Map(draft.rules.map((rule) => [rule.id, rule]));
  const saved = structuredClone(record.review);
  draft.meta.goal = saved.goal;
  draft.meta.scope = saved.scope;
  draft.rules = saved.rules.map((rule) => ({
    ...(sourceRules.get(rule.id) ?? { basis: "manual", quotes: [], activitySeed: null }), ...rule,
  }));
  draft.questions = saved.questions;
  draft.criteria = saved.criteria;
  return draft;
}

export function reviewSnapshot(draft) {
  return structuredClone({
    goal: draft.meta.goal,
    scope: draft.meta.scope,
    rules: draft.rules.map(({ id, text, location, status, confirmation }) => ({ id, text, location, status, confirmation })),
    questions: draft.questions,
    criteria: draft.criteria,
  });
}

function validDate(value) {
  const date = new Date(value);
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(date.getTime())
    && date.toISOString().slice(0, 10) === value;
}

export function questionResolved(question) {
  return Boolean(question?.decision.trim() && question.confirmedBy.trim() && validDate(question.confirmedOn));
}

export function acceptanceStatus(draft, criterion) {
  const rule = draft.rules.find(({ id }) => id === criterion.ruleId);
  if (rule?.status !== "confirmed" || !rule.confirmation.trim() || criterion.blockerIds.some((id) =>
    !questionResolved(draft.questions.find((question) => question.id === id))
  )) return "blocked";
  if ([criterion.given, criterion.when, criterion.then].some((text) => !text.trim())) return "incomplete";
  return "ready";
}

export function validateBaDraft(draft) {
  if (!draft.meta.requestNumber.trim() || !draft.meta.sourceName.trim() || !draft.meta.sourceVersion.trim()) {
    return "請填寫需求單號、來源文件名稱與來源版次。";
  }
  if (!draft.rules.length) return "請至少整理一條業務規則。";

  const ruleIds = new Set(draft.rules.map(({ id }) => id));
  const questionIds = new Set(draft.questions.map(({ id }) => id));
  for (const rule of draft.rules) {
    if (!rule.text.trim()) return rule.id + " 請填寫業務規則。";
    if (rule.status === "confirmed" && !rule.confirmation.trim()) return rule.id + " 請填寫業務確認依據。";
  }
  for (const question of draft.questions) {
    if (!question.text.trim()) return question.id + " 請填寫待確認問題。";
    if (question.ruleIds.some((id) => !ruleIds.has(id))) return question.id + " 請重新選擇關聯規則。";
    if (question.confirmedOn && !validDate(question.confirmedOn)) return question.id + " 請填寫有效的確認日期。";
  }
  for (const criterion of draft.criteria) {
    if (!ruleIds.has(criterion.ruleId)) return criterion.id + " 請選擇關聯規則。";
    if (criterion.blockerIds.some((id) => !questionIds.has(id))) {
      return criterion.id + " 請重新選擇影響驗收的問題。";
    }
  }
  return null;
}

export function baHandoffMarkdown(draft) {
  const value = (text) => text.trim() ? escapeMarkdown(text.trim()) : "待補";
  const confirmedCount = draft.rules.filter((rule) => rule.status === "confirmed").length;
  const openCount = draft.questions.filter((question) => !questionResolved(question)).length;
  const lines = [
    "# BA 需求交接稿", "", "> 審閱草稿，依現有工單流程確認與保存。", "",
    "## 基本資料", "",
    "- 需求單號　" + value(draft.meta.requestNumber),
    "- 系統名稱　" + value(draft.systemName),
    "- 來源文件　" + value(draft.meta.sourceName),
    "- 來源版次　" + value(draft.meta.sourceVersion),
    "- 分析請求編號　" + draft.requestId,
    "- 已確認規則　" + confirmedCount + "／" + draft.rules.length,
    "- 待確認問題　" + openCount,
    "", "## 業務目標與範圍", "",
    "- 業務目標　" + value(draft.meta.goal),
    "- 適用範圍　" + value(draft.meta.scope),
    "", "## 業務規則",
  ];
  for (const rule of draft.rules) {
    lines.push(
      "", "### " + rule.id, "",
      "- 規則　" + value(rule.text),
      "- 狀態　" + (rule.status === "confirmed" ? "業務已確認" : "待確認"),
      "- 擷取依據　" + sourceLabel(rule),
      "- 來源位置　" + value(rule.location),
    );
    if (rule.confirmation.trim()) lines.push("- 業務確認依據　" + value(rule.confirmation));
    for (const quote of rule.quotes) lines.push("- 擷取時原文引用　" + escapeMarkdown(quote));
  }
  lines.push("", "## 待確認事項與業務決議");
  if (!draft.questions.length) lines.push("", "無");
  for (const question of draft.questions) {
    const resolved = questionResolved(question);
    lines.push(
      "", "### " + question.id, "",
      "- 問題　" + value(question.text),
      "- 影響　" + value(question.impact),
      "- 關聯規則　" + (question.ruleIds.join("、") || "未指定"),
      "- 狀態　" + (resolved ? "業務已確認" : "待確認"),
    );
    if (question.decision.trim()) lines.push("- " + (resolved ? "業務決議" : "決議草稿") + "　" + value(question.decision));
    if (resolved) {
      lines.push("- 確認人　" + value(question.confirmedBy), "- 確認日期　" + value(question.confirmedOn));
    }
  }
  lines.push("", "## 驗收條件");
  if (!draft.criteria.length) lines.push("", "待補");
  for (const criterion of draft.criteria) {
    lines.push(
      "", "### " + criterion.id + " · " + criterion.ruleId, "",
      "- 狀態　" + ACCEPTANCE_LABELS[acceptanceStatus(draft, criterion)],
      "- 關聯問題　" + (criterion.blockerIds.join("、") || "無"),
      "- Given　" + value(criterion.given),
      "- When　" + value(criterion.when),
      "- Then　" + value(criterion.then),
    );
  }
  return lines.join("\n") + "\n";
}
