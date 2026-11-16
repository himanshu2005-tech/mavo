import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { agentState, cycleLine } from "./AgentGraph";
import { LiveCycle } from "./LiveCycle";

const crew = (cycle: CrewInstance["cycle"]) => ({ id: "c1", name: "Slot Scout", status: "active", is_running: true, runtime_state: "waiting", configuration_plan: {}, cycle } as unknown as CrewInstance);
const steps = [{ title: "Read Gmail", status: "done" }, { title: "Decide", status: "running" }, { title: "Send & reply", status: "waiting" }];

it("shows which step a running cycle is on, on the map and in its panel", () => {
  const working = crew({ run_id: "r1", status: "running", started_at: new Date().toISOString(), steps, current: 1, done: 1, total: 3, question: null });
  expect(cycleLine(working)).toBe("Step 2 of 3 · Decide");
  expect(agentState(working)).toBe("working");
  const html = renderToStaticMarkup(<LiveCycle crew={working} />);
  expect(html).toContain("Working now");
  expect(html).toContain("Read Gmail");
  expect(html).toContain('data-status="running"');
});

it("says when a step waits for the person, and shows nothing between runs", () => {
  const asking = crew({ run_id: "r1", status: "running", started_at: new Date().toISOString(), steps: [{ title: "Decide", status: "asking" }], current: 0, done: 0, total: 1, question: "Which time?", question_step: "decide" });
  expect(cycleLine(asking)).toBe("Waiting for your answer");
  expect(agentState(asking)).toBe("needs");
  expect(renderToStaticMarkup(<LiveCycle crew={asking} />)).toContain("Which time?");
  expect(renderToStaticMarkup(<LiveCycle crew={crew(null)} />)).toBe("");
});

it("shows what the agent has done right in view, while it works", () => {
  const thinking = { ...crew(null), cycle: { run_id: "r", status: "running", started_at: new Date().toISOString(), steps: [{ title: "Agent decides", status: "running" }], current: 0, done: 0, total: 1, question: null, thoughts: [{ label: "Asha: Sync · checking your calendar", text: "Free: Tue 17 Nov 15:30" }] } } as unknown as CrewInstance;
  const html = renderToStaticMarkup(<LiveCycle crew={thinking} />);
  expect(html).not.toContain("What it’s thinking");  // no click needed
  expect(html).toContain("Asha: Sync · checking your calendar");
  expect(html).toContain("Free: Tue 17 Nov 15:30");
});

it("a guarded agent's run: its cards in view, its path folded under How it works", () => {
  const recipe = { final: "desk", steps: [{ id: "read_gmail", kind: "read", source: "gmail" }, { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail"] }] };
  const thoughts = [{ label: "", text: "Reading it.", tool: "read" }, ...Array.from({ length: 7 }, (_, i) => ({ label: "", text: "", tool: "gmail.send", outcome: "done", about: `To Ravi: reply ${i}` }))];
  const working = { ...crew(null), configuration_plan: { recipe }, cycle: { run_id: "r", status: "running", started_at: new Date().toISOString(), steps: [{ title: "Agent decides", status: "running" }], current: 0, done: 0, total: 1, question: null, thoughts } } as unknown as CrewInstance;
  const html = renderToStaticMarkup(<LiveCycle crew={working} />);
  expect(html).toContain("To Ravi: reply 6");
  expect(html).toContain("2 earlier steps");
  expect(html).not.toContain("To Ravi: reply 0");
  expect(html).toContain("<summary>How it works</summary>");
});
