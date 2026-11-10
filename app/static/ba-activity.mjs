import { ACCEPTANCE_LABELS, acceptanceStatus, questionResolved } from "./ba-draft.mjs?v=uml-activity";

const action = (label, reference, pending = false) => ({ kind: "action", label, reference, pending });
const choice = (label, yes, no) => ({ kind: "choice", label, yes, no });

export function activityOptions(draft) {
  const ruleIds = new Set(draft.rules.map(({ id }) => id));
  return [
    ...draft.rules.map(({ id }) => [id, id + " 規則流程"]),
    ...draft.criteria.filter(({ ruleId }) => ruleIds.has(ruleId))
      .map(({ id, ruleId }) => [id, id + " 驗收情境 · " + ruleId]),
  ];
}

function plannedSteps(steps, ruleId) {
  return steps.map(({ actor, action: text, guard }) => {
    const step = action(actor + "｜" + text, ruleId);
    return guard ? choice(guard, [step], []) : step;
  });
}

function ruleFlow(draft, rule) {
  const questions = draft.questions.filter(({ ruleIds }) => ruleIds.includes(rule.id));
  const plan = rule.activitySeed?.text === rule.text ? rule.activitySeed.plan : null;
  if (!plan) {
    return {
      status: "待補流程步驟",
      steps: [action("候選規則｜" + (rule.text.trim() || "待填寫"), rule.id, true)],
      questions,
    };
  }

  const steps = plannedSteps(plan.steps, rule.id);
  if (!plan.branch) return { status: "依原文順序擷取的流程草稿", steps, questions };

  const branch = plan.branch;
  const question = questions.find(({ id }) => id === branch.otherwise_question_id);
  const resolved = questionResolved(question);
  const fallback = resolved ? "業務決議｜" + question.decision.trim()
    : "待業務確認｜" + (question?.text.trim() || "請補充此分支的業務決議");
  steps.push(choice(branch.condition, plannedSteps(branch.when_true, rule.id), [
    action(fallback, question?.id ?? rule.id, !resolved),
  ]));
  return { status: resolved ? "已填寫分支決議，流程待審閱" : "有待業務確認的分支", steps, questions };
}

function acceptanceFlow(draft, criterion, rule) {
  const status = acceptanceStatus(draft, criterion);
  const reference = rule.id + " · " + criterion.id;
  const given = criterion.given.trim();
  const when = criterion.when.trim();
  const then = criterion.then.trim();
  const steps = [
    action("前置條件｜" + (given || "待補"), reference, !given),
    action("操作｜" + (when || "待補"), reference, !when),
    status === "blocked" ? action("待業務確認｜請核對關聯規則與問題", reference, true)
      : action("預期結果｜" + (then || "待補"), reference, !then),
  ];
  const questions = draft.questions.filter(({ id }) => criterion.blockerIds.includes(id));
  return { status: ACCEPTANCE_LABELS[status], steps, questions };
}

export function buildActivity(draft, id) {
  const criterion = draft.criteria.find((item) => item.id === id);
  const rule = draft.rules.find((item) => item.id === (criterion?.ruleId ?? id));
  if (!rule) return null;
  const { status, steps, questions } = criterion ? acceptanceFlow(draft, criterion, rule) : ruleFlow(draft, rule);
  return {
    id,
    title: "UML 活動圖草稿 · " + id,
    status,
    metadata: [
      "需求單號　" + (draft.meta.requestNumber.trim() || "待填"),
      "來源文件　" + (draft.meta.sourceName.trim() || "待填"),
      "來源版次　" + (draft.meta.sourceVersion.trim() || "待填"),
    ],
    questionLabels: questions.map((question) => question.id + (questionResolved(question) ? " 已確認" : " 待確認")),
    steps,
  };
}

export function activityLabel(step) {
  return step.reference ? step.reference + " · " + step.label : step.label;
}

function plantUmlText(value) {
  // Keep document text in labels, including when it contains PlantUML delimiters or directives.
  return value.replace(/[\p{Cc}\p{Cf}\u2028\u2029]/gu, " ")
    .replace(/[\x21-\x2c\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\x7e]/g,
      (char) => String.fromCharCode(char.charCodeAt(0) + 0xfee0));
}

export function activityPlantUml(activity) {
  const lines = ["@startuml", "title " + plantUmlText(activity.title), "start"];
  function addSteps(steps, indent) {
    for (const step of steps) {
      if (step.kind === "action") {
        lines.push(indent + ":" + plantUmlText(activityLabel(step)) + ";");
        continue;
      }
      lines.push(indent + "if (" + plantUmlText(step.label) + ") then (是)");
      addSteps(step.yes, indent + "  ");
      if (step.no.length) {
        lines.push(indent + "else (否)");
        addSteps(step.no, indent + "  ");
      }
      lines.push(indent + "endif");
    }
  }
  addSteps(activity.steps, "");
  lines.push("stop", "legend");
  for (const text of [...activity.metadata, activity.status, ...activity.questionLabels]) {
    lines.push("  " + plantUmlText(text));
  }
  lines.push("end legend", "@enduml");
  return lines.join("\n") + "\n";
}
