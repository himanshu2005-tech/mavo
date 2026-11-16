import { expect, it } from "vitest";
import { parseReport } from "./Report";

it("puts back LaTeX a model damaged in JSON, and keeps each email's lines as one record", () => {
  const text = "\\begin{itemize}\n\\item \\textbf{From:} Internshala\n\\textbf{Subject:} Confirm your spot\n\\textit{Summary:} Urgent.\n\\textbf{Action:} \textbf{Reply needed.}\n\\end{itemize}";
  const blocks = parseReport(text);
  expect(JSON.stringify(blocks)).not.toContain("extbf");
  const records = blocks.find(b => b.kind === "records");
  expect(records && records.kind === "records" && records.records[0].title).toBe("Confirm your spot");
  expect(records && records.kind === "records" && records.records[0].fields.map(f => f.label)).toEqual(["From", "Summary", "Action"]);
});
