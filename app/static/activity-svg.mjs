import { activityLabel } from "./ba-activity.mjs";

const ACTION_WIDTH = 272;
const DECISION_WIDTH = 236;
const LINE_HEIGHT = 18;
const STEP_GAP = 26;
const BRANCH_GAP = 28;
const BRANCH_ENTRY = 42;
const BRANCH_EXIT = 28;
const MERGE_RADIUS = 8;

function wrapText(value, limit) {
  const text = value.replace(/[\p{Cc}\p{Cf}\p{Cs}\u2028\u2029\ufffe\uffff]/gu, " ").trim();
  const lines = [];
  let line = "";
  let width = 0;
  for (const char of text) {
    const size = char.codePointAt(0) > 0xff ? 2 : 1;
    if (width + size > limit) {
      lines.push(line.trim());
      line = "";
      width = 0;
    }
    line += char;
    width += size;
  }
  lines.push(line.trim());
  return lines;
}

function xmlText(value) {
  return value.replace(/[\p{Cc}\p{Cf}\p{Cs}\u2028\u2029\ufffe\uffff]/gu, " ")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function measureStep(step) {
  if (step.kind === "action") {
    const lines = wrapText(activityLabel(step), 30);
    return { ...step, lines, width: ACTION_WIDTH, height: lines.length * LINE_HEIGHT + 24 };
  }

  const lines = wrapText(step.label, 16);
  const yes = measureSequence(step.yes);
  const no = measureSequence(step.no);
  const decisionHeight = Math.max(76, lines.length * 40 + 24);
  return {
    ...step, lines, yes, no, decisionHeight,
    width: yes.width + no.width + BRANCH_GAP,
    height: decisionHeight + BRANCH_ENTRY + Math.max(yes.height, no.height) + BRANCH_EXIT + MERGE_RADIUS,
  };
}

function measureSequence(steps) {
  const blocks = steps.map(measureStep);
  return {
    blocks,
    width: Math.max(120, ...blocks.map(({ width }) => width)),
    height: blocks.reduce((sum, { height }) => sum + height, 0) + STEP_GAP * Math.max(0, blocks.length - 1),
  };
}

export function activitySvg(activity) {
  const layout = measureSequence(activity.steps);
  const width = Math.max(320, layout.width + 40);
  const center = width / 2;
  const heading = [activity.title, ...activity.metadata, activity.status]
    .flatMap((line) => wrapText(line, Math.floor((width - 36) / 10)));
  const startY = heading.length * LINE_HEIGHT + 42;
  const sequenceY = startY + 7 + STEP_GAP;
  const endY = sequenceY + layout.height + STEP_GAP + 10;
  const height = endY + 28;
  const parts = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="UML 活動圖">`,
    `<title>${xmlText(activity.title)}</title>`,
    `<desc>${xmlText(activity.status)}</desc>`,
    '<defs><marker id="uml-arrow" markerWidth="7" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#425568"/></marker></defs>',
    `<rect width="${width}" height="${height}" fill="#fff"/>`,
    '<g font-family="Microsoft JhengHei, Noto Sans CJK TC, sans-serif" font-size="14" fill="#172638">',
  ];

  function label(text, x, y, anchor = "middle") {
    parts.push(`<text x="${x}" y="${y}" text-anchor="${anchor}" dominant-baseline="central">${xmlText(text)}</text>`);
  }

  function path(d, arrow = false) {
    const marker = arrow ? ' marker-end="url(#uml-arrow)"' : "";
    parts.push(`<path d="${d}" fill="none" stroke="#425568" stroke-width="1.5"${marker}/>`);
  }

  function diamond(x, y, halfWidth, halfHeight) {
    parts.push(`<path d="M ${x} ${y - halfHeight} L ${x + halfWidth} ${y} L ${x} ${y + halfHeight} L ${x - halfWidth} ${y} Z" fill="#fff" stroke="#425568"/>`);
  }

  function drawSequence(sequence, x, top) {
    let y = top;
    for (const [index, block] of sequence.blocks.entries()) {
      if (index) path(`M ${x} ${y - STEP_GAP} V ${y}`, true);
      if (block.kind === "action") {
        const fill = block.pending ? "#fff5dc" : "#edf5fb";
        const stroke = block.pending ? "#9a691c" : "#56748a";
        const dash = block.pending ? ' stroke-dasharray="5 3"' : "";
        parts.push(`<rect x="${x - ACTION_WIDTH / 2}" y="${y}" width="${ACTION_WIDTH}" height="${block.height}" rx="10" fill="${fill}" stroke="${stroke}"${dash}/>`);
        block.lines.forEach((line, row) => label(line, x, y + 12 + (row + 0.5) * LINE_HEIGHT));
      } else {
        drawDecision(block, x, y);
      }
      y += block.height + STEP_GAP;
    }
  }

  function drawDecision(block, x, y) {
    const decisionCenter = y + block.decisionHeight / 2;
    diamond(x, decisionCenter, DECISION_WIDTH / 2, block.decisionHeight / 2);
    block.lines.forEach((line, row) => label(line, x, decisionCenter + (row - (block.lines.length - 1) / 2) * LINE_HEIGHT));

    const bottom = y + block.decisionHeight;
    const branchY = bottom + BRANCH_ENTRY;
    const mergeY = y + block.height - MERGE_RADIUS;
    const left = x - (block.no.width + BRANCH_GAP) / 2;
    const right = x + (block.yes.width + BRANCH_GAP) / 2;
    path(`M ${x} ${bottom} V ${bottom + 14}`);
    for (const [branch, branchX, edge, mergeX] of [
      [block.yes, left, "是", x - MERGE_RADIUS], [block.no, right, "否", x + MERGE_RADIUS],
    ]) {
      path(`M ${x} ${bottom + 14} H ${branchX} V ${branchY}`, branch.height > 0);
      label(edge, branchX + 9, bottom + 25, "start");
      drawSequence(branch, branchX, branchY);
      path(`M ${branchX} ${branchY + branch.height} V ${mergeY} H ${mergeX}`, true);
    }
    diamond(x, mergeY, MERGE_RADIUS, MERGE_RADIUS);
  }

  heading.forEach((line, row) => label(line, center, 22 + row * LINE_HEIGHT));
  parts.push(`<circle cx="${center}" cy="${startY}" r="7" fill="#172638"/>`);
  path(`M ${center} ${startY + 7} V ${sequenceY}`, true);
  drawSequence(layout, center, sequenceY);
  path(`M ${center} ${sequenceY + layout.height} V ${endY - 10}`, true);
  parts.push(`<circle cx="${center}" cy="${endY}" r="10" fill="#fff" stroke="#172638"/><circle cx="${center}" cy="${endY}" r="6" fill="#172638"/>`);
  parts.push("</g></svg>");
  return parts.join("\n") + "\n";
}
