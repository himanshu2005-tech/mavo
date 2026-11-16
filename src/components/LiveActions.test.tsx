import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewCycle, Thought } from "../types";
import { LiveActions, liveRows } from "./LiveActions";

const thoughts: Thought[] = [
  { label: "", text: "Opening the credits page", tool: "action", aid: "a1", op: "browser.open", outcome: "running" },
  { label: "", text: "Opening the credits page", tool: "action", aid: "a1", op: "browser.open", outcome: "done" },
  { label: "", text: "", tool: "browser.open", outcome: "done", about: "airouter.in/credits", result: "Credits", aid: "a1" },
  { label: "", text: "Reading your credits", tool: "action", aid: "a2", op: "browser.read", outcome: "running" },
];

it("each action is one row in its own words, its state the latest, its page its detail", () => {
  const rows = liveRows(thoughts);
  expect(rows.map(r => [r.text, r.state])).toEqual([["Opening the credits page", "done"], ["Reading your credits", "running"]]);
  expect(rows[0].detail?.about).toBe("airouter.in/credits");
  expect(liveRows([{ label: "", text: "Read 14 emails" }, { label: "", text: "Writing it up" }]).map(r => r.state)).toEqual(["done", "running"]);
});

it("draws a run live: its steps, a bar, and the actions ticked off", () => {
  const cycle = { run_id: "r", status: "running", started_at: new Date().toISOString(), steps: [{ title: "Opening airouter.in", status: "done" }, { title: "Reading your credits", status: "running" }], current: 1, done: 1, total: 2, question: null, thoughts } as CrewCycle;
  const html = renderToStaticMarkup(<LiveActions cycle={cycle} />);
  expect(html).toContain("Opening the credits page");
  expect(html).toContain('aria-label="Done"');
  expect(html).toContain('aria-label="Working"');
  expect(html).toContain("la-bar");
});
