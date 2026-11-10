const REQUESTS = "/api/v1/requirements";
const PAGE_SIZE = 30;
const DISPATCH_LABEL = {
  waiting_sa: "待 SA 接單", accepted: "SA 規格整理中", returned: "退回 BA",
  waiting_pg: "待 PG 接手", in_development: "PG 開發中",
};

const pathFor = (number) => REQUESTS + "/" + encodeURIComponent(number);
const wordPath = (item) => pathFor(item.number) + "/documents/" + item.version;

async function api(path, method = "GET", payload) {
  let response;
  let body;
  try {
    response = await fetch(path, {
      method,
      cache: "no-store",
      ...(payload === undefined ? {} : {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    });
    body = await response.json();
  } catch {
    throw new Error("無法讀取工作台回應。請重新整理清單確認最新狀態。");
  }
  if (!response.ok) throw new Error(body.error?.message || "工作台操作失敗，請檢查輸入後重試。");
  return body;
}

function attachmentContent(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("無法讀取來源附件。"));
    reader.readAsDataURL(file);
  });
}

async function diagramPng(markup) {
  const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const scale = Math.min(1.4, 1600 / image.naturalWidth, 2000 / image.naturalHeight);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.floor(image.naturalHeight * scale));
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    const data = canvas.toDataURL("image/png").split(",")[1];
    if (!data || data.length > 4 * Math.ceil(512 * 1024 / 3)) throw new Error("活動圖超出 512 KiB，請選擇較精簡的圖面。");
    return data;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function mountWorkbench({ node, review, canOpen, onOpen, onBusy }) {
  const $ = (id) => document.querySelector("#" + id);
  const workbench = $("workbench");
  const intake = $("intake-form");
  const intakeFields = $("intake-fields");
  const detail = $("work-detail");
  const detailFields = $("work-detail-fields");
  const grade = $("work-grade");
  const team = $("work-team");
  const diagram = $("work-diagram");
  const preview = $("work-doc-preview");
  const mainNotice = $("work-notice");
  const detailNotice = $("work-detail-notice");
  let record = null;
  let functions = [];
  let selected = new Set();
  let changed = false;
  let busy = false;
  let externalBusy = false;
  let offset = 0;
  let dispatchOffset = 0;
  let more = false;
  let moreDispatches = false;
  let ruleKey = "";

  function notify(target, message, kind = "info") {
    target.textContent = message;
    target.dataset.kind = kind;
    target.hidden = !message;
  }

  function moveTo(heading, panel) {
    heading.focus({ preventScroll: true });
    panel.scrollIntoView({ block: "start" });
  }

  function button(label, action) {
    const control = node("button", label, "secondary");
    control.type = "button";
    control.addEventListener("click", action);
    return control;
  }

  function link(label, href) {
    const anchor = node("a", label, "work-link");
    anchor.href = href;
    return anchor;
  }

  function warnBeforeLeaving(event) {
    event.preventDefault();
    event.returnValue = true;
  }

  function markChanged(value) {
    if (changed === value) return;
    changed = value;
    if (changed) window.addEventListener("beforeunload", warnBeforeLeaving);
    else window.removeEventListener("beforeunload", warnBeforeLeaving);
  }

  function lockControls() {
    const locked = busy || externalBusy;
    intakeFields.disabled = locked;
    detailFields.disabled = locked || !record?.analysis;
    $("work-prev").disabled = locked || offset === 0;
    $("work-next").disabled = locked || !more;
    $("dispatch-prev").disabled = locked || dispatchOffset === 0;
    $("dispatch-next").disabled = locked || !moreDispatches;
    $("work-refresh").disabled = locked;
  }

  async function run(action, notice = mainNotice) {
    if (busy || externalBusy) return;
    busy = true;
    onBusy(true);
    lockControls();
    notify(notice, "");
    try {
      await action();
    } catch (error) {
      notify(notice, error.message || "工作台操作失敗。", "error");
    } finally {
      busy = false;
      onBusy(false);
      lockControls();
    }
  }

  function selectedFunctions() {
    return functions.filter(({ id }) => selected.has(id));
  }

  function renderDiagramOptions() {
    const previous = diagram.value;
    const scopeRules = new Set(selectedFunctions().flatMap(({ rule_ids }) => rule_ids));
    const scopeCriteria = new Set((review.snapshot()?.criteria ?? [])
      .filter(({ ruleId }) => scopeRules.has(ruleId)).map(({ id }) => id));
    const options = review.activityOptions().filter(([id]) => scopeRules.has(id) || scopeCriteria.has(id));
    diagram.replaceChildren(...[["", "僅匯出文件"], ...options].map(([id, label]) => {
      const option = node("option", label);
      option.value = id;
      return option;
    }));
    diagram.value = options.some(([id]) => id === previous) || previous === "" ? previous : "";
  }

  function renderFunctions() {
    const rules = review.snapshot()?.rules ?? [];
    ruleKey = rules.map(({ id }) => id).join(" ");
    const cards = functions.map((item) => {
      const locked = record.locked_functions.includes(item.id);
      const card = node("article", undefined, "item work-function");
      const choose = node("label", undefined, "work-check");
      const checked = node("input");
      checked.type = "checkbox";
      checked.checked = selected.has(item.id);
      checked.addEventListener("change", () => {
        if (checked.checked) selected.add(item.id);
        else selected.delete(item.id);
        preview.hidden = true;
        renderDiagramOptions();
      });
      choose.append(checked, node("span", item.id + (locked ? " · 已派發" : " · 納入本次範圍")));
      const name = node("label", "功能名稱", "ba-field");
      const title = node("input");
      title.maxLength = 160;
      title.value = item.title;
      title.readOnly = locked;
      title.addEventListener("input", () => {
        item.title = title.value;
        markChanged(true);
        preview.hidden = true;
      });
      name.append(title);
      const links = node("details");
      links.open = !item.rule_ids.length;
      links.append(node("summary", "關聯業務規則　" + (item.rule_ids.join("、") || "待選")));
      for (const rule of rules) {
        const label = node("label", undefined, "work-check");
        const check = node("input");
        check.type = "checkbox";
        check.checked = item.rule_ids.includes(rule.id);
        check.disabled = locked;
        check.addEventListener("change", () => {
          item.rule_ids = check.checked
            ? [...item.rule_ids, rule.id] : item.rule_ids.filter((key) => key !== rule.id);
          links.querySelector("summary").textContent = "關聯業務規則　" + (item.rule_ids.join("、") || "待選");
          markChanged(true);
          preview.hidden = true;
          renderDiagramOptions();
        });
        label.append(check, node("span", rule.id + "　" + (rule.text.slice(0, 80) || "待填規則")));
        links.append(label);
      }
      card.append(choose, name, links);
      if (!locked) card.append(button("移除功能", () => {
        functions = functions.filter(({ id }) => id !== item.id);
        selected.delete(item.id);
        markChanged(true);
        preview.hidden = true;
        renderFunctions();
        $("work-add-function").focus();
      }));
      return card;
    });
    $("work-functions").replaceChildren(...(cards.length ? cards : [node("p", "請新增本筆需求的功能。", "hint")]));
    renderDiagramOptions();
  }

  function documentRow(item) {
    const card = node("article", undefined, "item");
    const state = item.status ? DISPATCH_LABEL[item.status] : "BA 審閱草稿";
    card.append(
      node("h4", item.number + " · BA v" + item.version + " · " + state),
      node("p", item.functions.map(({ id, title }) => id + " " + title).join("、")),
    );
    if (item.team) card.append(node("p", "承接團隊　" + item.team, "hint"));
    if (item.feedback) card.append(node("p", "退回原因　" + item.feedback, "hint"));
    if (item.specification) card.append(node("p", "SA 規格　" + item.specification, "hint"));
    card.append(link("下載 BA Word v" + item.version, wordPath(item)));
    return card;
  }

  function renderHistory() {
    const cards = record.documents.map(documentRow);
    $("work-history").replaceChildren(...(cards.length ? cards : [node("p", "尚無文件版次。", "hint")]));
  }

  function showRecord(next, { reset = false, open = false } = {}) {
    record = next;
    detail.hidden = !next;
    if (!next) {
      functions = [];
      selected.clear();
      markChanged(false);
      return;
    }
    functions = structuredClone(next.functions);
    if (reset) {
      selected = new Set(functions.map(({ id }) => id));
      team.value = "";
      diagram.value = "";
      preview.hidden = true;
      notify(detailNotice, "");
    }
    grade.value = next.grade;
    markChanged(false);
    if (open) onOpen(next);
    $("work-record-label").textContent = next.number + "　" + next.title
      + (next.analysis ? "" : "　請先執行需求分析");
    const source = $("work-source");
    source.textContent = "來源　" + next.source.number + "　" + next.source.name + "　" + next.source.version;
    if (next.source.filename) source.append(link("下載來源附件", pathFor(next.number) + "/source"));
    renderFunctions();
    renderHistory();
    lockControls();
  }

  async function openItem(number) {
    if (busy || externalBusy || !canOpen()) return;
    await run(async () => {
      const next = await api(pathFor(number));
      showRecord(next, { reset: true, open: true });
    });
  }

  function renderRequests(items) {
    $("work-rows").replaceChildren(...items.map((item) => {
      const row = node("tr");
      const state = !item.analyzed ? "待分析" : !item.reviewed ? "待審閱" : !item.grade ? "待分級"
        : item.dispatches ? "已派發 " + item.dispatches + " 批" : "待派發";
      for (const value of [item.number, item.title, item.source_number + " " + item.source_version, item.grade || "待分級", state]) {
        row.append(node("td", value));
      }
      const action = node("td");
      action.append(button("開啟需求", () => openItem(item.number)));
      row.append(action);
      return row;
    }));
    $("work-empty").textContent = items.length ? "" : "目前沒有需求，請新增需求單。";
  }

  async function transition(item, action, note) {
    await run(async () => {
      await api(pathFor(item.number) + "/dispatches/" + item.version + "/transition", "POST", { action, note });
      if (record?.number === item.number) {
        const latest = await api(pathFor(item.number));
        record = { ...record, documents: latest.documents, locked_functions: latest.locked_functions };
        renderFunctions();
        renderHistory();
      }
      await refresh();
      notify(mainNotice, "已更新開發團隊接單狀態。");
    });
  }

  function renderDispatches(items) {
    const cards = items.map((item) => {
      const card = documentRow(item);
      const actions = node("div", undefined, "actions");
      const note = node("input");
      note.maxLength = 160;
      if (["waiting_sa", "accepted"].includes(item.status)) {
        const label = node("label", item.status === "accepted" ? "SA 規格位置或退回原因" : "退回原因", "ba-field");
        label.append(note);
        card.append(label);
        actions.append(button("退回 BA", () => transition(item, "return", note.value)));
      }
      if (item.status === "waiting_sa") actions.append(button("SA 接單", () => transition(item, "accept", "")));
      if (item.status === "accepted") actions.append(button("交給 PG", () => transition(item, "send_pg", note.value)));
      if (item.status === "waiting_pg") actions.append(button("PG 開始開發", () => transition(item, "start_pg", "")));
      if (item.status === "returned") actions.append(button("開啟需求釐清", () => openItem(item.number)));
      card.append(actions);
      return card;
    });
    $("work-dispatches").replaceChildren(...(cards.length ? cards : [node("p", "目前沒有開發派發紀錄。", "hint")]));
  }

  async function refresh(requestStart = offset, dispatchStart = dispatchOffset) {
    const [requests, dispatches] = await Promise.all([
      api(REQUESTS + "?offset=" + requestStart + "&limit=" + PAGE_SIZE),
      api("/api/v1/dispatches?offset=" + dispatchStart + "&limit=" + PAGE_SIZE),
    ]);
    offset = requestStart;
    dispatchOffset = dispatchStart;
    more = requests.has_more;
    moreDispatches = dispatches.has_more;
    renderRequests(requests.items);
    renderDispatches(dispatches.items);
  }

  async function save() {
    const snapshot = review.snapshot();
    if (!record || !snapshot) throw new Error("請先執行需求分析。");
    if (!changed && !review.isDirty() && record.review) return;
    const next = await api(pathFor(record.number) + "/review", "PUT", {
      revision: record.revision, grade: grade.value, functions, review: snapshot,
    });
    showRecord(next);
    review.markSaved();
  }

  function renderPreview(content, diagramId) {
    const container = $("work-preview-content");
    const rows = node("dl", undefined, "work-preview-fields");
    for (const [label, value] of content.fields) rows.append(node("dt", label), node("dd", value));
    container.replaceChildren(node("h4", content.title), rows);
    for (const [heading, lines] of content.sections) {
      container.append(node("h4", heading), ...lines.map((line) => node("p", line)));
    }
    if (diagramId) {
      const markup = review.activityMarkup(diagramId);
      if (markup) {
        const figure = node("div", undefined, "activity-preview");
        figure.append(new DOMParser().parseFromString(markup, "image/svg+xml").documentElement);
        container.append(node("h4", "UML 活動圖"), figure);
      }
    }
    preview.hidden = false;
    moveTo($("work-preview-title"), preview);
  }

  async function documentAction(action) {
    await run(async () => {
      const functionIds = selectedFunctions().map(({ id }) => id);
      if (!functionIds.length) throw new Error("請勾選本次處理的功能。");
      if (action === "dispatches" && !team.value.trim()) throw new Error("請填寫承接團隊。");
      const diagramId = diagram.value;
      await save();
      const payload = { revision: record.revision, function_ids: functionIds };
      if (action === "preview") {
        renderPreview(await api(pathFor(record.number) + "/preview", "POST", payload), diagramId);
        return;
      }
      if (diagramId) {
        const markup = review.activityMarkup(diagramId);
        if (!markup) throw new Error("活動圖已變更，請重新選擇。");
        payload.diagram = { id: diagramId, png: await diagramPng(markup) };
      }
      if (action === "dispatches") payload.team = team.value;
      const next = await api(pathFor(record.number) + "/" + action, "POST", payload);
      showRecord(next);
      preview.hidden = true;
      const message = action === "dispatches" ? "已派發給 SA，保留 BA Word v" : "已產出 BA Word v";
      notify(detailNotice, message + next.documents[0].version + "。可從下方紀錄下載。");
      try {
        await refresh();
      } catch {
        notify(mainNotice, "清單尚待更新，可使用重新整理取得最新狀態。", "error");
      }
    }, detailNotice);
  }

  intake.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy || externalBusy || !canOpen()) return;
    const values = new FormData(intake);
    void run(async () => {
      const file = values.get("attachment");
      values.delete("attachment");
      const payload = Object.fromEntries(values);
      if (file?.size) {
        if (file.size > 2 * 1024 * 1024) throw new Error("來源附件上限為 2 MiB。");
        payload.attachment = { filename: file.name, content: await attachmentContent(file) };
      }
      const next = await api(REQUESTS, "POST", payload);
      intake.reset();
      $("work-intake").open = false;
      offset = 0;
      showRecord(next, { reset: true, open: true });
      await refresh();
    });
  });
  $("work-refresh").addEventListener("click", () => run(refresh));
  $("work-prev").addEventListener("click", () => run(() => refresh(offset - PAGE_SIZE)));
  $("work-next").addEventListener("click", () => run(() => refresh(offset + PAGE_SIZE)));
  $("dispatch-prev").addEventListener("click", () => run(() => refresh(offset, dispatchOffset - PAGE_SIZE)));
  $("dispatch-next").addEventListener("click", () => run(() => refresh(offset, dispatchOffset + PAGE_SIZE)));
  $("work-back").addEventListener("click", () => moveTo($("workbench-title"), workbench));
  grade.addEventListener("input", () => { markChanged(true); preview.hidden = true; });
  diagram.addEventListener("change", () => { preview.hidden = true; });
  $("work-add-function").addEventListener("click", () => {
    const oldIds = record.documents.flatMap((item) => item.functions.map(({ id }) => id));
    const latest = Math.max(0, ...[...oldIds, ...functions.map(({ id }) => id)].map((id) => Number(id.slice(1))));
    const id = "F" + String(latest + 1).padStart(2, "0");
    functions.push({ id, title: "", rule_ids: [] });
    selected.add(id);
    markChanged(true);
    preview.hidden = true;
    renderFunctions();
  });
  const saveButton = () => run(async () => {
    await save();
    notify(detailNotice, "已儲存分級、功能範圍與 BA 審閱。");
    await refresh();
  }, detailNotice);
  $("work-save").addEventListener("click", saveButton);
  $("save-tracked-review").addEventListener("click", () => {
    moveTo($("work-detail-title"), detail);
    void saveButton();
  });
  $("open-dispatch-controls").addEventListener("click", () => {
    renderFunctions();
    moveTo($("work-detail-title"), detail);
  });
  $("work-preview").addEventListener("click", () => documentAction("preview"));
  $("work-word").addEventListener("click", () => documentAction("documents"));
  $("work-dispatch").addEventListener("click", () => documentAction("dispatches"));

  return {
    current: () => record,
    isDirty: () => changed,
    isRuleLocked: (id) => Boolean(record && functions.some((item) =>
      record.locked_functions.includes(item.id) && item.rule_ids.includes(id))),
    refresh: () => run(refresh),
    acceptAnalysis: (next) => showRecord(next, { reset: true }),
    detach: () => showRecord(null),
    setBusy(value) { externalBusy = value; lockControls(); },
    reviewChanged() {
      if (!record) return;
      const ids = (review.snapshot()?.rules ?? []).map(({ id }) => id);
      if (ids.join(" ") !== ruleKey) {
        const rules = new Set(ids);
        for (const item of functions) {
          const available = item.rule_ids.filter((id) => rules.has(id));
          if (available.length === item.rule_ids.length) continue;
          item.rule_ids = available;
          markChanged(true);
        }
        renderFunctions();
      } else renderDiagramOptions();
      preview.hidden = true;
    },
  };
}
