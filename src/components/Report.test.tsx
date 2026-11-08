import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Report, parseReport } from "./Report";

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

  it("never injects markup or makes links clickable", () => {
    const html = renderToStaticMarkup(<Report text={'<img src=x onerror=alert(1)> and [site](https://example.com/a)'} />);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<a ");
    expect(html).toContain("report-link");
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
});
