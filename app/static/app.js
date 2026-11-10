"use strict";

const form = document.querySelector("#analysis-form");
const fields = document.querySelector("#analysis-fields");
const systemName = document.querySelector("#system-name");
const requirementText = document.querySelector("#requirement-text");
const maxTestCases = document.querySelector("#max-test-cases");
const loadExample = document.querySelector("#load-example");
const analyzeButton = document.querySelector("#analyze");
const notice = document.querySelector("#notice");
const resultPanel = document.querySelector("#result-panel");
const resultView = document.querySelector("#analysis-result");
const resultStatus = document.querySelector("#result-status");
const resultContent = document.querySelector("#result-content");
const emptyState = document.querySelector("#empty-state");
const charCount = document.querySelector("#char-count");

const DRAFT_NOTICE = "Mock 分析草稿，待人工審閱";
const PENDING_RESULT = "待確認規則後填寫";
const STATUS_LABELS = {
  generated: "已產出草稿",
  needs_clarification: "有待確認事項",
  insufficient_input: "需求資訊不足",
};
const BASIS_LABELS = { explicit: "原文明示", proposed: "建議草稿" };
const KIND_LABELS = { happy_path: "正常情境", boundary: "邊界情境", error: "錯誤情境" };

let currentResult = null;
let activeRequest = null;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function setNotice(message, kind = "info") {
  notice.textContent = message;
  notice.dataset.kind = kind;
  notice.hidden = !message;
}

function startRequest() {
  activeRequest = new AbortController();
  fields.disabled = true;
  loadExample.disabled = true;
  return activeRequest.signal;
}

function finishRequest() {
  activeRequest = null;
  fields.disabled = false;
  loadExample.disabled = false;
  analyzeButton.textContent = "執行分析";
  resultPanel.removeAttribute("aria-busy");
}

function clearResult() {
  currentResult = null;
  resultView.hidden = true;
  resultStatus.hidden = true;
  resultContent.replaceChildren();
  emptyState.textContent = "輸入需求或載入範例後執行分析。";
  emptyState.hidden = false;
}

function canonicalRequirement() {
  return requirementText.value.replace(/\r\n?/g, "\n").trim();
}

function updateCount() {
  const length = Array.from(canonicalRequirement()).length;
  charCount.textContent = length.toLocaleString("zh-TW") + " / 10,000";
}

function sections(data) {
  return [
    ["功能", data.features.map((feature) => ({
      title: feature.id + " " + feature.title,
      rows: [["描述", feature.description], ["依據", BASIS_LABELS[feature.basis]]],
      quotes: feature.source_quotes,
    }))],
    ["待確認事項", data.questions.map((question) => ({
      title: question.id + " " + question.question,
      rows: [["影響", question.reason], ["關聯功能", question.related_feature_ids.join("、") || "無"]],
    }))],
    ["驗收條件", data.acceptance_criteria.map((criterion) => ({
      title: criterion.id + " · " + criterion.feature_id,
      rows: [
        ["Given", criterion.given], ["When", criterion.when], ["Then", criterion.then],
        ["依據", BASIS_LABELS[criterion.basis]],
      ],
      quotes: criterion.source_quotes,
    }))],
    ["測試案例", data.test_cases.map((test) => ({
      title: test.id + " " + test.title,
      rows: [
        ["類型", KIND_LABELS[test.kind]], ["關聯功能", test.feature_id],
        ["驗收條件", test.criterion_id || "未指定"], ["前置條件", test.preconditions],
        ["步驟", test.steps], ["預期結果", test.expected_result],
        ["阻塞事項", test.blocked_by.join("、") || "無"], ["依據", BASIS_LABELS[test.basis]],
      ],
      quotes: test.source_quotes,
    }))],
  ];
}

function renderItem(item) {
  const card = node("article", undefined, "item");
  card.append(node("h4", item.title));
  const rows = node("dl");
  for (const [label, value] of item.rows) {
    rows.append(node("dt", label));
    const cell = node("dd");
    if (Array.isArray(value) && value.length) {
      const list = node("ol");
      value.forEach((entry) => list.append(node("li", entry)));
      cell.append(list);
    } else {
      cell.textContent = Array.isArray(value) ? "無" : (value ?? PENDING_RESULT);
    }
    rows.append(cell);
  }
  card.append(rows);
  if (item.quotes?.length) {
    const details = node("details");
    details.append(node("summary", "查看原文引用"));
    for (const quote of item.quotes) details.append(node("blockquote", quote));
    card.append(details);
  }
  return card;
}

function renderResult(result) {
  currentResult = result;
  resultStatus.textContent = STATUS_LABELS[result.status];
  resultStatus.dataset.status = result.status;
  resultStatus.hidden = false;
  emptyState.hidden = true;
  resultContent.replaceChildren(node("p", result.data.summary, "result-summary"));
  if (result.system_name) resultContent.prepend(node("p", result.system_name, "hint"));
  if (result.data.warnings.length) {
    const warnings = node("ul", undefined, "warnings");
    result.data.warnings.forEach((warning) => warnings.append(node("li", warning)));
    resultContent.append(warnings);
  }
  for (const [label, items] of sections(result.data)) {
    const group = node("section", undefined, "result-group");
    group.append(node("h3", label + "（" + items.length + "）"));
    if (!items.length) group.append(node("p", "無", "hint"));
    items.forEach((item) => group.append(renderItem(item)));
    resultContent.append(group);
  }
  resultContent.append(node("p", "請求編號 " + result.request_id, "hint request-id"));
  resultView.hidden = false;
}

function escapeMarkdown(text) {
  return text.replace(/[\r\n]+/g, " ")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\\*_{}\[\]()#+\-.!|\x60]/g, "\\$&");
}

function markdown(result) {
  const lines = [
    "# 需求分析草稿", "", "> " + DRAFT_NOTICE, "",
    "- 狀態 " + STATUS_LABELS[result.status],
    "- 請求編號 " + result.request_id,
  ];
  if (result.system_name) lines.push("- 系統名稱 " + escapeMarkdown(result.system_name));
  lines.push("", "## 摘要", "", escapeMarkdown(result.data.summary));
  if (result.data.warnings.length) {
    lines.push("", "## 提示", "");
    result.data.warnings.forEach((warning) => lines.push("- " + escapeMarkdown(warning)));
  }
  for (const [label, items] of sections(result.data)) {
    lines.push("", "## " + label);
    if (!items.length) lines.push("", "無");
    for (const item of items) {
      lines.push("", "### " + escapeMarkdown(item.title), "");
      for (const [field, value] of item.rows) {
        if (Array.isArray(value) && value.length) {
          lines.push("- " + field);
          value.forEach((entry, index) => {
            lines.push("  " + (index + 1) + ". " + escapeMarkdown(entry));
          });
        } else {
          const text = Array.isArray(value) ? "無" : (value ?? PENDING_RESULT + "（null）");
          lines.push("- " + field + "　" + escapeMarkdown(text));
        }
      }
      for (const quote of item.quotes || []) lines.push("- 原文引用　" + escapeMarkdown(quote));
    }
  }
  return lines.join("\n") + "\n";
}

function download(content, mediaType, extension) {
  const url = URL.createObjectURL(new Blob([content], { type: mediaType + ";charset=utf-8" }));
  const link = node("a");
  link.href = url;
  link.download = "requirements-" + currentResult.request_id + "." + extension;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeRequest) return;
  clearResult();
  const name = systemName.value.trim();
  const requirement = canonicalRequirement();
  const length = Array.from(requirement).length;
  const caseLimit = maxTestCases.valueAsNumber;
  if (length < 20 || length > 10_000) {
    setNotice("需求原文須為 20 至 10,000 個字元。", "error");
    requirementText.focus();
    return;
  }
  if (Array.from(name).length > 80 || !Number.isInteger(caseLimit) || caseLimit < 1 || caseLimit > 12) {
    setNotice("系統名稱上限為 80 個字元，測試案例上限須為 1 至 12 的整數。", "error");
    return;
  }
  const signal = startRequest();
  analyzeButton.textContent = "分析中…";
  resultPanel.setAttribute("aria-busy", "true");
  emptyState.textContent = "正在分析需求…";
  setNotice("分析中，請稍候。");
  try {
    const response = await fetch("/api/v1/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_name: name || null, requirement_text: requirement, max_test_cases: caseLimit }),
      cache: "no-store",
      signal,
    });
    const body = await response.json();
    if (!response.ok) {
      const id = body.request_id ? " 請求編號 " + body.request_id : "";
      setNotice((body.error?.message || "分析失敗。") + id, "error");
      clearResult();
      return;
    }
    renderResult({ ...body, system_name: name || null });
    setNotice("已完成分析，可檢視草稿與原文引用。");
  } catch (error) {
    clearResult();
    setNotice(error.name === "AbortError" ? "" : "無法取得分析結果，請稍後重試。", "error");
  } finally {
    finishRequest();
  }
});

loadExample.addEventListener("click", async () => {
  if (activeRequest) return;
  const signal = startRequest();
  try {
    const response = await fetch("/static/example.json", { signal });
    if (!response.ok) throw new Error("example unavailable");
    const example = await response.json();
    systemName.value = example.system_name;
    requirementText.value = example.requirement_text;
    maxTestCases.value = example.max_test_cases;
    updateCount();
    clearResult();
    setNotice("已載入預約範例。");
  } catch (error) {
    if (error.name !== "AbortError") setNotice("無法載入範例，仍可手動輸入需求。", "error");
  } finally {
    finishRequest();
  }
});

document.querySelector("#clear").addEventListener("click", () => {
  if (activeRequest) return;
  form.reset();
  updateCount();
  clearResult();
  setNotice("");
  requirementText.focus();
});
document.querySelector("#export-json").addEventListener("click", () => {
  if (currentResult) {
    const report = { ...currentResult, notice: DRAFT_NOTICE };
    download(JSON.stringify(report, null, 2) + "\n", "application/json", "json");
  }
});
document.querySelector("#export-md").addEventListener("click", () => {
  if (currentResult) download(markdown(currentResult), "text/markdown", "md");
});
requirementText.addEventListener("input", updateCount);
window.addEventListener("pagehide", () => activeRequest?.abort());

form.reset();
updateCount();
