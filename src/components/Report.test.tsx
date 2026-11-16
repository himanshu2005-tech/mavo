import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Report, parseReport, reportLatex, reportLinkUrl, statOf } from "./Report";
import { normalizeReportMarkup } from "../lib/reportFormat";

const SAMPLE = `Checked **placements.haveloc.com**. The portal currently displays **108 Jobs Listed**.

Please note: The **Location** field is not visible in the main table view on this page.

Based on the "Job Posted Date" column, the newest listings are:

1. **Job Title:** CEI INDIA for BTech 2027 batch
   **Company:** CEI INDIA PRIVATE LIMITED
   **Posted Date:** 2 Nov 2026

2. **Job Title:** Hut Labs : B.Tech 2027 POB
   **Company:** HuT Labs
   **Posted Date:** 31 Oct 2026

3. **Job Title:** Third
   **Company:** C3

Other listings go back to 29 Sept 2026.`;

describe("Report", () => {
  it("turns an agent's markdown into a lead, a note, record cards and a closing line", () => {
    const blocks = parseReport(SAMPLE);
    expect(blocks.map(b => b.kind)).toEqual(["paragraph", "callout", "paragraph", "records", "paragraph"]);
    const records = blocks[3];
    if (records.kind !== "records") throw new Error("expected records");
    expect(records.records).toHaveLength(3);
    expect(records.records[0].title).toBe("CEI INDIA for BTech 2027 batch");
    expect(records.records[0].fields).toEqual([{ label: "Company", value: "CEI INDIA PRIVATE LIMITED" }, { label: "Posted Date", value: "2 Nov 2026" }]);
  });

  it("keeps the bullets under a bare \"Please note:\" inside the note", () => {
    const text = "The balance is **$0.74**.\n\nPlease note:\n- No expiry date was shown.\n- First check, nothing to compare.\n\nDone.";
    const blocks = parseReport(text);
    expect(blocks.map(b => b.kind)).toEqual(["paragraph", "callout", "paragraph"]);
    const note = blocks[1];
    if (note.kind !== "callout") throw new Error("expected a note");
    expect(note.items).toEqual(["No expiry date was shown.", "First check, nothing to compare."]);
    const html = renderToStaticMarkup(<Report text={text} />);
    expect(html.indexOf("No expiry date")).toBeGreaterThan(html.indexOf("report-callout"));
    expect(html.indexOf("No expiry date")).toBeLessThan(html.indexOf("</aside>"));
    // A one-line note is unchanged, and a bare label with no list stays a plain note.
    expect(parseReport("Note: one line.")[0]).toEqual({ kind: "callout", label: "Note", text: "one line." });
    expect(parseReport("Note:")[0]).toEqual({ kind: "callout", label: "Note", text: "" });
  });

  it("shows bold text as bold and never the asterisks", () => {
    const html = renderToStaticMarkup(<Report text={SAMPLE} />);
    expect(html).toContain("<strong>108 Jobs Listed</strong>");
    expect(html).not.toContain("**");
    expect(html).toContain('role="note"');
  });

  it("keeps a plain list a list and renders tables", () => {
    const blocks = parseReport("- one\n- two\n\n| Name | Role |\n| --- | --- |\n| Asha | Intern |");
    expect(blocks.map(b => b.kind)).toEqual(["list", "table"]);
    const html = renderToStaticMarkup(<Report text={"| Name | Role |\n| --- | --- |\n| Asha | Intern |"} />);
    expect(html).toContain("<th>Name</th>");
    expect(html).toContain("<td>Asha</td>");
  });

  it("never injects source markup and opens sources only through explicit safe links", () => {
    const html = renderToStaticMarkup(<Report text={'<img src=x onerror=alert(1)> and [site](https://example.com/a)'} />);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain('href="https://example.com/a"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain("report-link");
  });

  it("renders all seven saved news items separated by escaped HTML breaks", () => {
    const text = 'News Reader\n\n' + Array.from({length: 7}, (_, i) => `- Story ${i + 1} — BBC`).join('\\<br>');
    const blocks = parseReport(text);
    expect(blocks.map(b => b.kind)).toEqual(['paragraph','list']);
    expect(blocks[1]).toMatchObject({items: Array.from({length: 7}, (_, i) => `Story ${i + 1} — BBC`)});
    const html = renderToStaticMarkup(<Report text={text} compact truncate={false}/>);
    expect(html.match(/<li>/g)).toHaveLength(7);
    expect(html).not.toContain('&lt;br');
    expect(html).not.toContain('report-latex'); // plain news reads in the app's own type
    expect(renderToStaticMarkup(<Report text={'The area is $\\pi r^2$.'}/>)).toContain('report-latex'); // maths keeps the maths font
    expect(html).not.toContain('Copy report as LaTeX');
    expect(html).not.toContain('report-tools');
    expect(html).not.toContain('>LaTeX<');
  });

  it("handles ordinary and entity-escaped line breaks without accepting arbitrary HTML", () => {
    for (const br of ['<br>','<br/>','<BR />','&lt;br&gt;','\\<br>']) {
      expect(parseReport('- First'+br+'- Second')[0]).toMatchObject({kind:'list',items:['First','Second']});
    }
    const html=renderToStaticMarkup(<Report text={'<br onclick="alert(1)"><script>alert(1)</script>'}/>);
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;br onclick');
  });

  it("renders LaTeX documents, sections, emphasis, enumerated lists and source links", () => {
    const text=String.raw`\documentclass{article}
\begin{document}
\section*{News Reader}
\begin{enumerate}
\item \textbf{A useful headline}. \emph{BBC}. \href{https://example.com/story?x=1\&y=2}{Read source}
\item A second headline.
\end{enumerate}
\end{document}`;
    const html=renderToStaticMarkup(<Report text={text}/>);
    expect(html).toContain('report-h1');
    expect(html).toContain('<strong>A useful headline</strong>');
    expect(html).toContain('<em>BBC</em>');
    expect(html).toContain('<ol');
    expect(html.match(/<li>/g)).toHaveLength(2);
    expect(html).toContain('href="https://example.com/story?x=1&amp;y=2"');
    expect(html).not.toMatch(/\\(?:item|section|begin|end|textbf|href)/);
  });

  it("preserves math, tables, inline code and currency while normalizing prose", () => {
    expect(normalizeReportMarkup('`<br>` <br> end')).toBe('`<br>` \n end');
    const original=String.raw`\begin{align}a &= b \\ c &= d\end{align}`;
    expect(normalizeReportMarkup(original)).toBe(original);
    expect(normalizeReportMarkup(String.raw`\textbf{Budget} is $5 and $10; \(a_1\) remains.`)).toContain(String.raw`\(a_1\)`);
  });

  it("exports a real LaTeX document with escaped prose and no executable source commands", () => {
    const result=reportLatex('## News\n\n- **AI & ML** costs $5. [BBC](https://example.com/a)\n- \\input{private-file}');
    expect(result).toContain('\\documentclass{article}');
    expect(result).toContain('\\subsection*{News}');
    expect(result).toContain('\\begin{itemize}');
    expect(result).toContain('\\textbf{AI \\& ML}');
    expect(result).toContain('costs \\$5');
    expect(result).toContain('\\href{https://example.com/a}{BBC}');
    expect(result).not.toContain('\\input{private-file}');
    expect(reportLatex('$$\\input{private-file}$$')).not.toContain('\\input{private-file}');
    expect(reportLatex('$$\\frac{1}{2}$$')).toContain('\\[\\frac{1}{2}\\]');
  });

  it("rejects unsafe and local source targets", () => {
    for (const value of ['javascript:alert(1)','file:///C:/secret','https://user:pass@example.com','http://127.1','http://192.168.1.1','https://localhost','http://[::1]','https://example.com:9999','https://example.com/\\evil']) expect(reportLinkUrl(value)).toBeNull();
    expect(reportLinkUrl('https://www.bbc.com/news/story?x=1')).toBe('https://www.bbc.com/news/story?x=1');
  });

  it("gives a small window the gist: the opening line and the first item, no notes", () => {
    const html = renderToStaticMarkup(<Report digest text={SAMPLE} />);
    expect(html).toContain("108 Jobs Listed");
    expect(html).not.toContain("report-callout");
    expect(html).not.toContain("Based on the");
    expect(html).toContain("CEI INDIA for BTech 2027 batch");
    expect(html).not.toContain("Hut Labs");
    expect(html).toContain("+2 more");
  });

  it("leaves out what only the worker needs: its plan, empty groups and stray asterisks", () => {
    // A real inbox report, as it was shown.
    const text = "Found 5 new messages.\n\nImportant\n**\n1. **Title:** Zaggle intern\n   **Company:** NxtWave\n\nNeeds a reply: (None)\n\nFYI:\n\nNote: verify links.\nNext: wait_for_next_review";
    const html = renderToStaticMarkup(<Report text={text} />);
    expect(html).not.toMatch(/Next:|wait_for_next_review|\(None\)|Needs a reply|\*\*/);
    expect(html).toContain("Important");
    expect(html).not.toContain("FYI"); // nothing under it
    expect(parseReport(text).map(b => b.kind)).toEqual(["paragraph", "heading", "records", "callout"]);
  });

  it("renders LaTeX: block and inline equations, and a tabular as a table", () => {
    const text = "The quadratic formula:\n\n$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$\n\nwhere $a \\neq 0$.\n\n\\begin{tabular}{lr}\n\\hline\nPlan & Price \\\\\n\\hline\nBasic & \\$5 \\\\\nPro & \\$12 \\\\\n\\end{tabular}";
    const blocks = parseReport(text);
    expect(blocks.map(b => b.kind)).toEqual(["paragraph", "math", "paragraph", "table"]);
    const table = blocks[3];
    if (table.kind !== "table") throw new Error("expected a table");
    expect(table.head).toEqual(["Plan", "Price"]);
    expect(table.rows).toEqual([["Basic", "$5"], ["Pro", "$12"]]);
    const html = renderToStaticMarkup(<Report text={text} />);
    expect(html).toContain('class="katex-display"');
    expect(html).toContain("<mfrac>"); // drawn as a fraction (the TeX is kept only for screen readers)
    expect(html).toContain("report-tex");
    // An environment over several lines is one block.
    const align = parseReport("\\begin{align}\na &= b + c \\\\\nd &= e\n\\end{align}\nAfter.");
    expect(align.map(b => b.kind)).toEqual(["math", "paragraph"]);
  });

  it("leaves money alone: $0.15 and $3.40 are not maths", () => {
    const html = renderToStaticMarkup(<Report text="Balance $0.15, below $3.40." />);
    expect(html).not.toContain("katex");
    expect(html).toContain("Balance $0.15, below $3.40.");
  });

  it("handles plain prose and empty input without fuss", () => {
    expect(parseReport("")).toEqual([]);
    expect(parseReport("Nothing new today.")).toEqual([{ kind: "paragraph", text: "Nothing new today." }]);
  });
  it("can keep compact typography without truncating lists, records or tables", () => {
    const fixtures = [
      {text: Array.from({length: 8}, (_, i) => `- Story ${i + 1}`).join('\n'), last: 'Story 8'},
      {text: SAMPLE, last: 'Third'},
      {text: '| Item |\n| --- |\n' + Array.from({length: 8}, (_, i) => `| Row ${i + 1} |`).join('\n'), last: 'Row 8'},
    ];
    for (const {text, last} of fixtures) {
      const complete = renderToStaticMarkup(<Report text={text} compact truncate={false} />);
      expect(complete).toContain('report-compact');
      expect(complete).toContain(last);
      expect(complete).not.toContain('report-more');
      const preview = renderToStaticMarkup(<Report text={text} compact />);
      expect(preview).toContain('report-more');
      expect(preview).not.toContain(last);
    }
  });
});

it("never shows a thinking model's reasoning or a stray tag of it", async () => {
  const { withoutThinking } = await import("./Report");
  expect(withoutThinking("<think>weighing options</think>\n**Plan**")).toBe("**Plan**");
  expect(withoutThinking("## Time-block\n**Tonight (Nov 12):\n</think>]")).toBe("## Time-block\n**Tonight (Nov 12):\n");
  const html = renderToStaticMarkup(<Report text={"Priority 1: Register\n</think>]"} />);
  expect(html).toContain("Priority 1");
  expect(html).not.toContain("think");
});

describe("a report that is an action's JSON", () => {
  it("is shown as labelled lines, ids left out, with what was done after it", () => {
    const text = '[ { "to": "hr@example.com", "subject": "Re: Interview", "body": "Hi,\n\nConfirmed for 11:30.", "thread_id": "1a1d84391619fd20" } ]\n\nDone: Create event (Interview)';
    const html = renderToStaticMarkup(<Report text={text} />);
    expect(html).not.toContain("thread_id");
    expect(html).not.toContain("1a1d84391619fd20");
    expect(html).not.toContain('"to"');
    expect(html).toContain("hr@example.com");
    expect(html).toContain("Confirmed for 11:30.");
    expect(html).toContain("Done: Create event (Interview)");
  });
});

describe("the answer first, big", () => {
  it("shows a report's opening 'Label: value' as the answer, the rest as details", () => {
    const html = renderToStaticMarkup(<Report text={"Credits left: $0.50\n- Last top-up: Nov 14 ($1.00)\n- $1 = ₹94.25"} />);
    expect(html).toContain('<div class="report-stat"><span>Credits left</span><b>$0.50</b></div>');
    expect(html).toContain("Last top-up: Nov 14 ($1.00)");
  });

  it("only for a short answer at the very start", () => {
    expect(statOf("Status: All systems normal")).toEqual({ label: "Status", value: "All systems normal" });
    expect(statOf("**Current Balance:** $0.50 USD")).toEqual({ label: "Current Balance", value: "$0.50 USD" });
    expect(statOf("Note: the page was slow today")).toBeNull();
    expect(statOf("Source: https://airouter.in/settings/credits")).toBeNull();
    expect(statOf("Summary: the page lists several plans and options for teams")).toBeNull();
    const later = renderToStaticMarkup(<Report text={"Three new stories today.\n\nCredits left: 5"} />);
    expect(later).not.toContain("report-stat");
  });
});

it("a 'who · subject' heading is never shown as the big answer", () => {
  expect(statOf("Aditya · Re: Roadmap")).toBeNull();
});
