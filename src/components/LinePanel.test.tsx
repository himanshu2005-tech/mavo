import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it } from "vitest";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";
import { HandoverCard, LineSettings, lineOf } from "./LinePanel";

const crew = (id: string, name: string, plan: Record<string, unknown> = {}, extra: Partial<CrewInstance> = {}) =>
  ({ id, name, template_id: "operator", status: "active", configuration_plan: plan, permissions: [], ...extra }) as unknown as CrewInstance;
const writer = crew("w", "Video Script Writer");
const reviewer = crew("r", "Script Reviewer", { upstream: ["w"], review: true, max_rounds: 2 });
const uploader = crew("u", "YouTube Uploader", { upstream: ["r"], approve_first: true });

beforeEach(() => { useAppStore.setState({ crew: [writer, reviewer, uploader] }); });

it("knows where an agent's work comes from and where it goes", () => {
  const { ups, downs } = lineOf(reviewer, [writer, reviewer, uploader]);
  expect([ups.map(c => c.name), downs.map(c => c.name)]).toEqual([["Video Script Writer"], ["YouTube Uploader"]]);
});

it("shows a reviewer's place in the line, its send-back limit and its switches", () => {
  const html = renderToStaticMarkup(<LineSettings crew={reviewer} crews={[writer, reviewer, uploader]} />);
  for (const text of ["Gets work from", "Video Script Writer", "Hands work to", "YouTube Uploader", "Reviews the work it gets", "Sends it back up to", "2×", "Asks you first"]) expect(html).toContain(text);
  expect(html).toMatch(/role="switch" aria-checked="true" aria-label="Reviews the work it gets"/);
});

it("an agent that starts on its own cannot review or ask first until it takes work from someone", () => {
  const html = renderToStaticMarkup(<LineSettings crew={writer} crews={[writer, reviewer, uploader]} />);
  expect(html).toContain("Nobody: it starts on its own schedule");
  expect(html).toMatch(/aria-label="Asks you first" class="resource-switch" disabled=""/);
});

it("work waiting for you says who made it, shows it and its files, and offers approve or send back", () => {
  const held = crew("u", "YouTube Uploader", { upstream: ["r"], approve_first: true }, {
    held: { kind: "approve", from: "Script Reviewer", at: "", result: "Script for day 12", files: ["~/Videos/day-12/script.md"] },
  } as Partial<CrewInstance>);
  const html = renderToStaticMarkup(<HandoverCard crew={held} />);
  for (const text of ["Script Reviewer handed over work", "Script for day 12", "~/Videos/day-12/script.md", "Send back", "Approve"]) expect(html).toContain(text);
  const stuck = renderToStaticMarkup(<HandoverCard crew={{ ...held, held: { kind: "review_stuck", from: "Video Script Writer", at: "", result: "Draft 3", review: "Hook still weak" } } as CrewInstance} />);
  for (const text of ["could not agree", "Hook still weak", "Send it on"]) expect(stuck).toContain(text);
});

it("a team shows who leads and who works for it, and the lead's question gets yes or no", () => {
  const lead = crew("l", "Channel Lead", { leads_team: true, members: ["w", "r"] });
  const member = crew("w", "Video Script Writer", { lead: "l" });
  const all = [lead, member, reviewer];
  const leadHtml = renderToStaticMarkup(<LineSettings crew={lead} crews={all} />);
  for (const text of ["Team", "Leads", "Video Script Writer", "Script Reviewer", "decides, step by step"]) expect(leadHtml).toContain(text);
  expect(leadHtml).not.toContain("Reviews the work it gets"); // no fixed roles inside a team
  expect(renderToStaticMarkup(<LineSettings crew={member} crews={all} />)).toContain("Works only when Channel Lead gives it a task");
  const asking = { ...lead, question: "Publish today's video as public?", held: { kind: "ask", from: "Channel Lead", at: "", result: "Video is ready." } } as unknown as CrewInstance;
  const card = renderToStaticMarkup(<HandoverCard crew={asking} />);
  for (const text of ["Channel Lead asks you", "Publish today&#x27;s video as public?", "Yes, go on", "No"]) expect(card).toContain(text);
});
