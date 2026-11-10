import {
  ACCEPTANCE_LABELS, acceptanceStatus, baHandoffMarkdown,
  questionResolved, restoreBaDraft, reviewSnapshot, sourceLabel, validateBaDraft,
} from "./ba-draft.mjs?v=workbench";
import { activityOptions, activityPlantUml, buildActivity } from "./ba-activity.mjs";
import { activitySvg } from "./activity-svg.mjs";

const NEW_ITEMS = {
  rules: () => ({ text: "", basis: "manual", quotes: [], location: "", status: "pending", confirmation: "" }),
  questions: () => ({ text: "", impact: "", ruleIds: [], decision: "", confirmedBy: "", confirmedOn: "" }),
  criteria: () => ({ ruleId: "", given: "", when: "", then: "", blockerIds: [] }),
};
const ID_PREFIXES = { rules: "BR-", questions: "Q-", criteria: "AC-" };

export function mountBaReview(panel, { node, download, onChange, isRuleLocked }) {
  const form = panel.querySelector("form");
  const fields = panel.querySelector("#ba-review-fields");
  const notice = panel.querySelector("#ba-notice");
  const metadata = [...panel.querySelectorAll("[data-ba-meta]")];
  const lists = {
    rules: panel.querySelector("#ba-rules"),
    questions: panel.querySelector("#ba-questions"),
    criteria: panel.querySelector("#ba-criteria"),
  };
  const addButtons = [...panel.querySelectorAll("[data-ba-add]")];
  const unlinked = panel.querySelector("#ba-unlinked");
  const storageNote = panel.querySelector("#ba-storage-note");
  const activitySelect = panel.querySelector("#ba-activity-choice");
  const activityStatus = panel.querySelector("#ba-activity-status");
  const activityQuestions = panel.querySelector("#ba-activity-questions");
  const activityPreview = panel.querySelector("#ba-activity-preview");
  const activityNotice = panel.querySelector("#ba-activity-notice");
  const activityButtons = [...panel.querySelectorAll("[data-activity-export]")];
  let draft = null;
  let tracked = false;
  let dirty = false;
  let nextIds = {};
  let activity = null;
  let activityMarkup = "";
  let activityOptionsKey = "";
  const statusUpdates = [];

  function setNotice(message, kind = "info", target = notice) {
    target.textContent = message;
    target.dataset.kind = kind;
    target.hidden = !message;
  }

  function renderActivity() {
    const options = draft ? activityOptions(draft) : [];
    const key = options.map(([, label]) => label).join("\n");
    if (activityOptionsKey !== key) {
      const selected = activitySelect.value;
      activitySelect.replaceChildren(...options.map(([id, label]) => {
        const option = node("option", label);
        option.value = id;
        return option;
      }));
      activitySelect.value = selected;
      activityOptionsKey = key;
    }
    if (!options.some(([id]) => id === activitySelect.value)) activitySelect.value = options[0]?.[0] ?? "";
    activity = draft ? buildActivity(draft, activitySelect.value) : null;
    activitySelect.disabled = !options.length;
    activityButtons.forEach((button) => { button.disabled = !activity; });
    activityStatus.textContent = activity?.status ?? "請先補上業務規則，再檢視活動圖。";
    const questions = activity?.questionLabels.join("、") ?? "";
    activityQuestions.textContent = questions ? "關聯問題　" + questions : "";
    activityQuestions.hidden = !questions;
    activityPreview.hidden = !activity;
    activityMarkup = activity ? activitySvg(activity) : "";
    activityPreview.replaceChildren();
    if (activityMarkup) {
      const parsed = new DOMParser().parseFromString(activityMarkup, "image/svg+xml");
      activityPreview.append(parsed.documentElement);
    }
    setNotice("", "info", activityNotice);
  }

  function activityBasename() {
    return "ba-activity-" + draft.requestId + "-" + activity.id;
  }

  function warnBeforeLeaving(event) {
    event.preventDefault();
    event.returnValue = true;
  }

  function setDirty(value) {
    if (dirty === value) return;
    dirty = value;
    if (dirty) window.addEventListener("beforeunload", warnBeforeLeaving);
    else window.removeEventListener("beforeunload", warnBeforeLeaving);
  }

  function edited() {
    setDirty(true);
    setNotice("");
    statusUpdates.forEach((update) => update());
    renderActivity();
    onChange();
  }

  function field(label, item, key, { choices, type = "text", placeholder, note, onEdit, regroup = false } = {}) {
    const wrapper = node("label", label, "ba-field");
    const control = node(choices ? "select" : type === "textarea" ? "textarea" : "input");
    control.dataset.field = key;
    if (choices) {
      control.multiple = Array.isArray(item[key]);
      if (control.multiple) control.size = Math.max(2, Math.min(4, choices.length));
      for (const [value, label] of choices) {
        const option = node("option", label);
        option.value = value;
        option.selected = control.multiple ? item[key].includes(value) : value === item[key];
        control.append(option);
      }
    } else {
      if (type === "textarea") control.rows = 2;
      else control.type = type;
      control.maxLength = type === "textarea" ? 1_000 : 160;
      if (placeholder) control.placeholder = placeholder;
      control.value = item[key];
    }
    control.addEventListener("input", () => {
      onEdit?.();
      item[key] = control.multiple
        ? Array.from(control.selectedOptions, (option) => option.value)
        : control.value;
      edited();
      if (regroup) {
        render();
        const moved = panel.querySelector('[data-ba-id="' + item.id + '"]')?.querySelector('[data-field="' + key + '"]');
        const details = moved?.closest("details");
        if (details) details.open = true;
        moved?.focus();
      }
    });
    wrapper.append(control);
    if (note) wrapper.append(node("span", note, "hint"));
    return wrapper;
  }

  function cardFor(group, item) {
    const card = node("article", undefined, "item ba-item");
    card.dataset.baId = item.id;
    const header = node("div", undefined, "ba-item-heading");
    const remove = node("button", "移除", "secondary");
    remove.type = "button";
    remove.setAttribute("aria-label", "移除 " + item.id);
    remove.addEventListener("click", () => {
      if (group === "rules" && isRuleLocked(item.id)) {
        setNotice(item.id + " 仍關聯已派發功能。請保留規則以供追溯。", "error");
        return;
      }
      const linkedRule = group === "rules" && (
        draft.questions.some((question) => question.ruleIds.includes(item.id))
        || draft.criteria.some((criterion) => criterion.ruleId === item.id)
      );
      const linkedQuestion = group === "questions"
        && draft.criteria.some((criterion) => criterion.blockerIds.includes(item.id));
      if (linkedRule || linkedQuestion) {
        setNotice(item.id + " 仍有關聯項目，請先調整關聯再移除。", "error");
        return;
      }
      draft[group].splice(draft[group].indexOf(item), 1);
      render();
      edited();
      addButtons[0].focus();
    });
    header.append(node("h4", item.id), remove);
    card.append(header);
    return { card, header };
  }

  function statusBadge(header, label) {
    const badge = node("span", undefined, "hint");
    header.querySelector("h4").append(badge);
    statusUpdates.push(() => { badge.textContent = "　" + label(); });
  }

  function rulePending(item) {
    const criteria = draft.criteria.filter(({ ruleId }) => ruleId === item.id);
    return item.status !== "confirmed" || !item.confirmation.trim()
      || draft.questions.some((question) => question.ruleIds.includes(item.id) && !questionResolved(question))
      || !criteria.length || criteria.some((criterion) => acceptanceStatus(draft, criterion) !== "ready");
  }

  function renderRule(item) {
    const { card, header } = cardFor("rules", item);
    statusBadge(header, () => rulePending(item) ? "待處理" : "已審閱");
    const status = field("確認狀態", item, "status", {
      choices: [["pending", "待確認"], ["confirmed", "業務已確認"]],
    });
    card.append(
      field("業務規則", item, "text", { type: "textarea", onEdit() {
        item.status = "pending";
        status.querySelector("select").value = item.status;
      } }),
      status,
      field("業務確認依據", item, "confirmation", {
        placeholder: "例如需求書第 3 節的業務確認，或 Q-01 的決議",
        note: "標為業務已確認時須填寫。",
      }),
    );
    const details = node("details");
    details.append(
      node("summary", "原文與來源"),
      field("來源位置", item, "location", { placeholder: "例如第 3 節或表 2 第 1 列" }),
      node("p", "擷取依據 " + sourceLabel(item), "hint"),
    );
    item.quotes.forEach((quote) => details.append(node("blockquote", quote)));
    card.append(details);

    const related = node("div", undefined, "rule-related");
    for (const question of draft.questions.filter(({ ruleIds }) => ruleIds.includes(item.id))) {
      if (question.ruleIds[0] === item.id) related.append(renderQuestion(question));
      else related.append(node("p", question.id + " 同時影響這條規則，可在 " + question.ruleIds[0] + " 核對。", "hint"));
    }
    draft.criteria.filter(({ ruleId }) => ruleId === item.id).forEach((criterion) => related.append(renderCriterion(criterion)));
    const actions = node("div", undefined, "actions");
    for (const [group, label] of [["questions", "新增待確認問題"], ["criteria", "新增驗收條件"]]) {
      const add = node("button", label, "secondary");
      add.type = "button";
      add.addEventListener("click", () => addItem(group, item.id));
      actions.append(add);
    }
    card.append(related, actions);
    return card;
  }

  function renderQuestion(item) {
    const { card, header } = cardFor("questions", item);
    statusBadge(header, () => questionResolved(item) ? "業務已確認" : "待確認");
    const confirmedBy = field("確認人", item, "confirmedBy");
    const confirmedOn = field("確認日期", item, "confirmedOn", { type: "date" });
    function reopenDecision() {
      if (!questionResolved(item)) return;
      item.confirmedBy = "";
      item.confirmedOn = "";
      confirmedBy.querySelector("input").value = "";
      confirmedOn.querySelector("input").value = "";
    }
    card.append(
      field("待確認問題", item, "text", { type: "textarea", onEdit: reopenDecision }),
      field("業務決議", item, "decision", {
        type: "textarea",
        note: "修改已確認的問題、關聯規則或決議時，請重新記錄確認人與日期。",
        onEdit: reopenDecision,
      }),
    );
    const confirmation = node("div", undefined, "ba-field-grid");
    confirmation.append(confirmedBy, confirmedOn);
    const links = node("details");
    links.open = !item.ruleIds.length;
    links.append(
      node("summary", "影響與關聯規則"),
      field("影響", item, "impact", { type: "textarea" }),
      field("關聯規則", item, "ruleIds", {
        choices: draft.rules.map(({ id }) => [id, id]),
        note: "可多選。使用 Ctrl 或 ⌘ 選取或取消。",
        onEdit: reopenDecision,
        regroup: true,
      }),
    );
    card.append(confirmation, links);
    return card;
  }

  function renderCriterion(item) {
    const { card, header } = cardFor("criteria", item);
    statusBadge(header, () => ACCEPTANCE_LABELS[acceptanceStatus(draft, item)]);
    const links = node("details");
    links.open = !item.ruleId;
    links.append(
      node("summary", "規則與阻塞問題"),
      field("關聯規則", item, "ruleId", {
        choices: [["", "請選擇規則"], ...draft.rules.map(({ id }) => [id, id])],
        regroup: true,
      }),
      field("影響這項驗收的問題", item, "blockerIds", {
        choices: draft.questions.map(({ id }) => [id, id]),
        note: "只選擇會影響這項驗收的問題。可使用 Ctrl 或 ⌘ 多選。",
      }),
    );
    card.append(
      field("在什麼情況", item, "given", { type: "textarea" }),
      field("做什麼操作", item, "when", { type: "textarea" }),
      field("應看到什麼結果", item, "then", { type: "textarea" }),
      links,
    );
    return card;
  }

  function render() {
    statusUpdates.length = 0;
    const unlinkedQuestions = draft.questions.filter(({ ruleIds }) => !ruleIds.length);
    const unlinkedCriteria = draft.criteria.filter(({ ruleId }) => !ruleId);
    const groups = {
      rules: [...draft.rules].sort((a, b) => Number(rulePending(b)) - Number(rulePending(a))).map(renderRule),
      questions: unlinkedQuestions.map(renderQuestion),
      criteria: unlinkedCriteria.map(renderCriterion),
    };
    for (const [group, cards] of Object.entries(groups)) {
      lists[group].replaceChildren(...(cards.length ? cards : [node("p", "尚無項目。", "hint")]));
    }
    if (unlinkedQuestions.length || unlinkedCriteria.length) unlinked.open = true;
    statusUpdates.forEach((update) => update());
  }

  function addItem(group, ruleId) {
    const id = ID_PREFIXES[group] + String(nextIds[group]++).padStart(2, "0");
    const item = { id, ...NEW_ITEMS[group]() };
    if (group === "questions" && ruleId) item.ruleIds = [ruleId];
    if (group === "criteria" && ruleId) item.ruleId = ruleId;
    draft[group].push(item);
    render();
    edited();
    panel.querySelector('[data-ba-id="' + id + '"]').querySelector("textarea, input, select")?.focus();
  }

  for (const input of metadata) {
    input.addEventListener("input", () => {
      draft.meta[input.dataset.baMeta] = input.value;
      edited();
    });
  }
  for (const button of addButtons) {
    button.addEventListener("click", () => addItem(button.dataset.baAdd));
  }
  activitySelect.addEventListener("change", renderActivity);
  for (const button of activityButtons) {
    button.addEventListener("click", () => {
      if (!activity || fields.disabled) return;
      const issue = validateBaDraft(draft);
      if (issue) {
        setNotice(issue, "error", activityNotice);
        return;
      }
      const format = button.dataset.activityExport;
      if (format === "svg") download(activityMarkup, "image/svg+xml", format, activityBasename());
      else download(activityPlantUml(activity), "text/plain", format, activityBasename());
      setNotice("已產出活動圖檔案，可附回需求單。", "info", activityNotice);
    });
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!draft || fields.disabled) return;
    const issue = validateBaDraft(draft);
    if (issue) {
      setNotice(issue, "error");
      return;
    }
    const appendix = activity ? [
      "", "## UML 活動圖", "", "- 圖面對象　" + activity.id,
      "- 搭配圖檔　" + activityBasename() + ".svg", "",
      "```plantuml", activityPlantUml(activity).trimEnd(), "```", "",
    ].join("\n") : "";
    download(baHandoffMarkdown(draft) + appendix, "text/markdown", "md", "ba-handoff-" + draft.requestId);
    if (!tracked) setDirty(false);
    setNotice("已產出 BA 交接稿，可附回需求單。");
  });

  return {
    load(result, record) {
      draft = restoreBaDraft(result, record);
      tracked = Boolean(record);
      for (const [group, prefix] of Object.entries(ID_PREFIXES)) {
        nextIds[group] = Math.max(0, ...draft[group].map(({ id }) => Number(id.slice(prefix.length)))) + 1;
      }
      form.reset();
      unlinked.open = false;
      panel.querySelector("#ba-activity").open = false;
      for (const input of metadata) {
        const key = input.dataset.baMeta;
        input.value = draft.meta[key];
        input.readOnly = tracked && ["requestNumber", "sourceName", "sourceVersion"].includes(key);
      }
      storageNote.textContent = tracked ? "請核對規則卡，完成後儲存 BA 審閱。"
        : "自由分析的草稿保存在目前頁面。下載交接稿可保留內容。";
      panel.querySelector("#save-tracked-review").hidden = !tracked;
      panel.querySelector("#open-dispatch-controls").hidden = !tracked;
      setDirty(false);
      setNotice("");
      render();
      renderActivity();
      panel.hidden = false;
    },
    clear() {
      draft = null;
      form.reset();
      Object.values(lists).forEach((list) => list.replaceChildren());
      statusUpdates.length = 0;
      setDirty(false);
      setNotice("");
      renderActivity();
      panel.hidden = true;
    },
    isDirty: () => dirty,
    snapshot: () => draft && reviewSnapshot(draft),
    activityOptions: () => draft ? activityOptions(draft) : [],
    activityMarkup: (id) => {
      const chart = draft && buildActivity(draft, id);
      return chart ? activitySvg(chart) : "";
    },
    markSaved: () => setDirty(false),
    setBusy(busy) { fields.disabled = busy; },
  };
}
