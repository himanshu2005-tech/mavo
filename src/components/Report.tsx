import katex from "katex";
import "katex/dist/katex.min.css";
import React, { type ReactNode } from "react";

/* An agent writes its result as plain text with light markdown. This turns it into a small,
   tidy report: headings, notes, lists, tables, and records ("**Job Title:** ..." lines) shown as
   cards. Nothing is ever injected as HTML, and links are shown as text, not opened. */

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "callout"; label: string; text: string; items?: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "records"; records: ReportRecord[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "math"; tex: string }
  | { kind: "rule" };

/* LaTeX: KaTeX turns the TeX into markup itself (trust off, so no links, URLs or HTML from the
   TeX survive), which is the one place a report is given generated markup. */
function tex(source: string, display: boolean): string {
  return katex.renderToString(source, { displayMode: display, throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml" });
}

/** A LaTeX tabular as a table: rows split on \\, cells on &, rules (\hline...) dropped. */
function tabular(body: string): Block {
  const inner = body.replace(/^[\s\S]*?\\begin\{tabular\*?\}(\{[^}]*\})?(\{[^}]*\})?/, "").replace(/\\end\{tabular\*?\}[\s\S]*$/, "");
  const rows = inner.split(/\\\\/).map(r => r.replace(/\\(hline|toprule|midrule|bottomrule|cline\{[^}]*\})/g, "").trim()).filter(Boolean)
    .map(r => r.split(/(?<!\\)&/).map(c => c.trim().replace(/\\textbf\{([^}]*)\}/g, "**$1**").replace(/\\([$%&#_])/g, "$1")));
  return { kind: "table", head: rows[0] ?? [], rows: rows.slice(1) };
}

const MATH_OPEN = /^\s*(\$\$|\\\[|\\begin\{(equation|align|gather|multline|tabular|array|matrix|pmatrix|bmatrix|cases)\*?\})/;

/** A math or table block that starts at line i: the block and the index of its last line. */
function mathBlock(lines: string[], i: number): [Block, number] | null {
  const open = MATH_OPEN.exec(lines[i]);
  if (!open) return null;
  const close = open[1] === "$$" ? "$$" : open[1] === "\\[" ? "\\]" : `\\end{${open[2]}`;
  // The first line counts from after its opening; later lines whole. No close: up to the end.
  const after = lines[i].slice(lines[i].indexOf(open[1]) + open[1].length);
  let j = i;
  if (!after.includes(close)) {
    j = i + 1;
    while (j < lines.length && !lines[j].includes(close)) j += 1;
    j = Math.min(j, lines.length - 1);
  }
  const text = lines.slice(i, j + 1).join("\n");
  if (open[2] === "tabular") return [tabular(text), j];
  const body = open[1] === "$$" ? text.trim().replace(/^\$\$/, "").replace(/\$\$\s*$/, "") : open[1] === "\\[" ? text.trim().replace(/^\\\[/, "").replace(/\\\]\s*$/, "") : text.trim();
  return [{ kind: "math", tex: body.trim() }, j];
}

// Inline math: \( ... \), or $ ... $ only when it really is maths ("$0.15 and $3.40" is money).
const INLINE_MATH = /\\\((.+?)\\\)|\$(?!\s)([^$\n]*?[\\^_{][^$\n]*?)(?<!\s)\$(?!\d)/g;

export interface ReportRecord { title: string; fields: { label: string; value: string }[] }

const CALLOUT = /^(?:\*\*)?(note|please note|warning|heads up|tip)(?:\*\*)?\s*[:\-–]\s*(.*)$/i;
const FIELD = /^\s*(?:[-*•]\s+|\d+[.)]\s+)?(?:\*\*([^*:]{1,48}?):?\*\*:?|([A-Za-z][A-Za-z0-9 /&'’()-]{0,40}?):)\s+(.+?)\s*$/;
const ITEM = /^(\s*)([-*•]|\d+[.)])\s+(.*)$/;
const TITLE_KEYS = /^(title|name|job title|role|subject|company|item|task|event|headline)$/i;

function fieldOf(line: string): { label: string; value: string } | null {
  const m = FIELD.exec(line);
  if (!m) return null;
  const label = (m[1] ?? m[2]).trim();
  return label ? { label, value: m[3].trim() } : null;
}

const isTableLine = (line: string) => /^\s*\|.*\|\s*$/.test(line);
const isDivider = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());

// A group name on its own line ("**Important**", "FYI:"), or followed by a colon and its content.
const GROUP = /^\s*(?:#{1,4}\s*)?\**\s*(important|needs a reply|needs your reply|fyi|for your information)\s*\**\s*(?::\s*\**\s*(.*?)\s*\**)?\s*$/i;
const NOTHING = /^\(?\s*(none|nothing|n\/a|-)\s*\)?\.?$/i;

/** What a report says, without what only the worker needs: its "Next:" plan, groups with
 *  nothing in them ("Needs a reply: (None)"), and stray markdown ("**" on its own). A group name
 *  ("**Important:**") is a heading, not a warning. */
export function tidyReport(text: string): string {
  const out: string[] = [];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*next\s*:/i.test(line)) continue;
    if (/^\s*\*{1,3}\s*$/.test(line)) continue;
    const group = GROUP.exec(line);
    if (group) {
      const rest = group[2] ?? "";
      if (NOTHING.test(rest)) continue;
      const name = group[1].toUpperCase() === "FYI" ? "FYI" : group[1][0].toUpperCase() + group[1].slice(1).toLowerCase();
      out.push(`#### ${name}`);
      if (rest) out.push(rest);
      continue;
    }
    out.push(line);
  }
  // A group with nothing under it (the next line is another group, a note, or the end) goes too.
  const empty = (i: number) => {
    const next = out.slice(i + 1).find(l => l.trim()) ?? "#### ";
    return next.startsWith("#### ") || CALLOUT.test(next.trim());
  };
  return out.filter((line, i) => !(line.startsWith("#### ") && empty(i))).join("\n").trim();
}

export function parseReport(text: string): Block[] {
  const lines = tidyReport(text).split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => { if (paragraph.length) { blocks.push({ kind: "paragraph", text: paragraph.join(" ") }); paragraph = []; } };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) { flush(); continue; }
    const math = mathBlock(lines, i);
    if (math) { flush(); blocks.push(math[0]); i = math[1]; continue; }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) { flush(); blocks.push({ kind: "heading", level: heading[1].length, text: heading[2].replace(/\*\*/g, "") }); continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); blocks.push({ kind: "rule" }); continue; }

    if (isTableLine(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      flush();
      const head = cells(line); const rows: string[][] = [];
      i += 2;
      while (i < lines.length && isTableLine(lines[i])) { rows.push(cells(lines[i])); i += 1; }
      i -= 1;
      blocks.push({ kind: "table", head, rows });
      continue;
    }

    if (ITEM.test(line)) {
      flush();
      // Gather the whole list, each item with the lines indented under it.
      const items: { ordered: boolean; lines: string[] }[] = [];
      while (i < lines.length) {
        const current = lines[i];
        const m = ITEM.exec(current);
        if (m && (items.length === 0 || m[1].length < 2)) { items.push({ ordered: /\d/.test(m[2]), lines: [m[3]] }); }
        else if (!current.trim()) {
          // A blank line ends the list unless the next line carries on with another item.
          if (i + 1 < lines.length && ITEM.test(lines[i + 1]) && (ITEM.exec(lines[i + 1])?.[1].length ?? 0) < 2) { i += 1; continue; }
          break;
        } else if (items.length && /^\s+\S/.test(current)) { items[items.length - 1].lines.push(current.trim().replace(/^[-*•]\s+/, "")); }
        else break;
        i += 1;
      }
      i -= 1;
      const records = items.map(item => recordOf(item.lines));
      if (records.length && records.every(Boolean)) blocks.push({ kind: "records", records: records as ReportRecord[] });
      else blocks.push({ kind: "list", ordered: items[0].ordered, items: items.map(item => item.lines.join(" ")) });
      continue;
    }

    const callout = CALLOUT.exec(line.trim());
    if (callout) {
      flush();
      const block: Block = { kind: "callout", label: callout[1].replace(/^./, c => c.toUpperCase()), text: callout[2] };
      // "Please note:" with nothing after the colon introduces the list under it: keep them together.
      if (!block.text.trim()) {
        const items: string[] = [];
        while (i + 1 < lines.length && ITEM.test(lines[i + 1])) { i += 1; items.push(ITEM.exec(lines[i])![3].trim()); }
        if (items.length) block.items = items;
      }
      blocks.push(block);
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

/** An item is a record when every one of its lines is a "label: value" pair. */
function recordOf(lines: string[]): ReportRecord | null {
  const fields = lines.map(fieldOf);
  if (fields.length < 2 || fields.some(f => !f)) return null;
  const list = fields as { label: string; value: string }[];
  const titleAt = Math.max(0, list.findIndex(f => TITLE_KEYS.test(f.label)));
  const rest = list.filter((_, index) => index !== titleAt);
  return { title: list[titleAt].value, fields: rest };
}

/** **bold**, *italic*, `code`, inline maths; links and addresses are shown as text. */
export function inline(text: string): ReactNode[] {
  if (!/\\\(|\$/.test(text)) return markup(text);
  const out: ReactNode[] = [];
  let last = 0; let m: RegExpExecArray | null;
  INLINE_MATH.lastIndex = 0;
  while ((m = INLINE_MATH.exec(text))) {
    if (m.index > last) out.push(...markup(text.slice(last, m.index)));
    out.push(<span key={"m" + m.index} className="report-tex" dangerouslySetInnerHTML={{ __html: tex(m[1] ?? m[2], false) }} />);
    last = INLINE_MATH.lastIndex;
  }
  if (last < text.length) out.push(...markup(text.slice(last)));
  return out;
}

function markup(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /\*\*([^*]+)\*\*|\*([^*\s][^*]*?)\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let last = 0; let n = 0; let m: RegExpExecArray | null;
  while ((m = pattern.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={n++}>{m[1]}</strong>);
    else if (m[2] !== undefined) out.push(<em key={n++}>{m[2]}</em>);
    else if (m[3] !== undefined) out.push(<code key={n++}>{m[3]}</code>);
    else out.push(<span key={n++} className="report-link" title={m[5]}>{m[4]}</span>);
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const CAP = { records: 2, items: 3, rows: 4 };
const GIST = { records: 1, items: 2, rows: 3 };
const more = (n: number) => n > 0 ? <p className="report-more">+{n} more</p> : null;

function Blocks({ blocks, compact, gist }: { blocks: Block[]; compact: boolean; gist: boolean }) {
  const cap = gist ? GIST : CAP;
  return <>{blocks.map((block, index) => {
    switch (block.kind) {
      case "heading": return <h4 key={index} className={"report-h report-h" + Math.min(block.level, 3)}>{inline(block.text)}</h4>;
      case "paragraph": return <p key={index} className={index === 0 ? "report-lead" : undefined}>{inline(block.text)}</p>;
      case "callout": return <aside key={index} className="report-callout" role="note"><b>{block.label}</b><div>{block.text && <span>{inline(block.text)}</span>}{block.items && <ul className="report-list">{block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>}</div></aside>;
      case "rule": return <hr key={index} />;
      case "math": return <div key={index} className="report-math" dangerouslySetInnerHTML={{ __html: tex(block.tex, true) }} />;
      case "list": {
        const items = compact ? block.items.slice(0, cap.items) : block.items;
        const rest = more(block.items.length - items.length);
        return <React.Fragment key={index}>{block.ordered
          ? <ol className="report-list">{items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ol>
          : <ul className="report-list">{items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>}{rest}</React.Fragment>;
      }
      case "records": {
        const records = compact ? block.records.slice(0, cap.records) : block.records;
        return <React.Fragment key={index}><div className="report-records">{records.map((record, i) =>
          <article key={i} className="report-record"><span className="report-index">{i + 1}</span>
            <h5>{inline(record.title)}</h5>
            <dl>{record.fields.map(field => <div key={field.label}><dt>{field.label}</dt><dd>{inline(field.value)}</dd></div>)}</dl></article>)}</div>{more(block.records.length - records.length)}</React.Fragment>;
      }
      case "table": {
        const rows = compact ? block.rows.slice(0, cap.rows) : block.rows;
        return <React.Fragment key={index}><div className="report-table"><table><thead><tr>{block.head.map((h, i) => <th key={i}>{inline(h)}</th>)}</tr></thead>
          <tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>{more(block.rows.length - rows.length)}</React.Fragment>;
      }
    }
  })}</>;
}

/** A Crew's result, laid out to be read. `compact` is for the small desktop card. */
export function Report({ text, compact, digest }: { text: string; compact?: boolean; digest?: boolean }) {
  const all = parseReport(text);
  // A digest is the gist for a small window: the opening line and the first few items, no notes.
  const blocks = digest ? all.filter(b => b.kind !== "callout" && b.kind !== "heading" && b.kind !== "rule").filter((b, i, kept) => b.kind !== "paragraph" || kept.findIndex(k => k.kind === "paragraph") === i) : all;
  const small = !!compact || !!digest;
  return <div className={"report" + (small ? " report-compact" : "")}><Blocks blocks={blocks} compact={small} gist={!!digest} /></div>;
}
