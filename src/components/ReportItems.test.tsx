import { expect, it } from "vitest";
import { parseReport } from "./Report";

const B = String.fromCharCode(92);
const SAMPLE = [
  `${B}section{Inbox Summary}`, `${B}begin{itemize}`,
  `    ${B}item ${B}textbf{From:} Team Unstop`, `    ${B}textbf{Subject:} You're Missing Out on an Exciting AWS Opportunity!`,
  `    ${B}textbf{Key Point:} An AWS-related opportunity.`, `    ${B}textbf{Action:} Review the email.`, ``,
  `    ${B}item ${B}textbf{From:} Internshala`, `    ${B}textbf{Subject:} Confirm your spot`,
  `    ${B}textbf{Key Point:} Needs confirmation.`, `    ${B}textbf{Deadline:} Immediate action required.`, ``,
  `    ${B}item ${B}textbf{From:} Maverick AI`, `    ${B}textbf{Subject:} You can now claim your business in ChatGPT`,
  `    ${B}textbf{Action:} Check if relevant.`,
  `${B}end{itemize}`, `Note: The Internshala email likely has a tight deadline.`,
].join("\n");

it("keeps every item of one LaTeX list in one numbered list, however it is indented or spaced", () => {
  const blocks = parseReport(SAMPLE);
  const records = blocks.filter(b => b.kind === "records");
  expect(records).toHaveLength(1);
  const list = records[0].kind === "records" ? records[0].records : [];
  expect(list.map(r => r.title)).toEqual(["You're Missing Out on an Exciting AWS Opportunity!", "Confirm your spot", "You can now claim your business in ChatGPT"]);
  expect(JSON.stringify(blocks)).not.toMatch(/textbf|begin\{|\item/);
});
