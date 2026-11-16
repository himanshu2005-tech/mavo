import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { deskFlow, flowRows, gateText, liveNodes, WorkflowFlow, type FlowStep } from "./WorkflowFlow";

const STEPS: FlowStep[] = [
  { id: "mail", kind: "read", source: "gmail", new_only: true },
  { id: "cal", kind: "read", source: "calendar" },
  { id: "understand", kind: "helper", title: "Understand each email", uses: ["mail"], instructions: "What each email asks.", returns: { fields: { thread_id: "id", intent: ["request", "cancel"] } } },
  { id: "slots", kind: "automation", automation: "calendar.slots", uses: ["understand", "cal"], where: { step: "understand", field: "intent", in: ["request", "counter"] }, params: { start: "09:00", end: "18:00", duration_minutes: 30, alternatives: 3 } },
  { id: "book", kind: "act", operation: "calendar.create", args_from: "slots", where: { step: "slots", field: "chosen", is: "not_empty" }, args: { start: "{{item.chosen.start}}" } },
  { id: "offer", kind: "helper", title: "Offer other times", uses: ["slots"], where: { step: "slots", field: "chosen", is: "empty" } },
];

it("lays the path out in the order it runs: reads, then each step after what it reads", () => {
  expect(flowRows(STEPS).map(row => row.map(s => s.id))).toEqual([["mail", "cal"], ["understand"], ["slots"], ["book", "offer"]]);
});

it("says each branch's condition plainly", () => {
  expect(gateText({ step: "s", field: "intent", in: ["request", "counter"] })).toBe("when intent is request or counter");
  expect(gateText({ step: "s", field: "chosen", is: "not_empty" })).toBe("when chosen is set");
  expect(gateText({ step: "s", field: "chosen", is: "empty" })).toBe("when there is no chosen");
  expect(gateText({ step: "s", field: "confidence", min: 0.8 })).toBe("when confidence is at least 0.8");
  expect(gateText({ step: "s", field: "intent", in: ["cancel"], label: "the sender cancels" })).toBe("If the sender cancels");
  expect(gateText({ step: "s", is: "not_empty" })).toBeNull();  // an old "anything new" condition: no branch
});

it("shows what reads, what thinks, what code checks and what acts", () => {
  const html = renderToStaticMarkup(<WorkflowFlow steps={STEPS} final="offer" />);
  for (const text of ["Your mail, new only", "Your calendar", "Agent", "Understand each email", "Code", "Find a free time", "Action", "Book it on your calendar", "when intent is request or counter", "when there is no chosen", "wf-gate"]) expect(html).toContain(text);
  expect(html).not.toContain("What each email asks."); // details open on a click
});

it("draws the guarded agent in the order it works: the email, reading it, the calendar on those days, the decision, each action", () => {
  const steps: FlowStep[] = [
    { id: "read_gmail", kind: "read", source: "gmail", new_only: true },
    { id: "read_calendar", kind: "read", source: "calendar" },
    { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail", "read_calendar"], params: { start: "09:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"], duration_minutes: 30, alternatives: 3, max_rounds: 3 } },
  ];
  const flow = deskFlow(steps, ["gmail.send", "calendar.create", "browser.click"]);
  expect(flowRows(flow).map(row => row.map(s => s.id))).toEqual([
    ["read_gmail"], ["desk_read"], ["read_calendar"], ["desk"], ["do_gmail.send", "do_calendar.create", "ask_you"],
  ]);
  const html = renderToStaticMarkup(<WorkflowFlow steps={steps} final="desk" actions={["gmail.send", "calendar.create"]} />);
  for (const text of ["Your mail, new only", "Reads it", "Checks your calendar on those days", "Decides what to do", "Send the email", "Book it on your calendar", "Ask you", "If their time is free, or they agreed", "If it is unsure"]) expect(html).toContain(text);
  expect(html).toContain('data-kind="agent"');
});

it("leaves any other path as it is", () => {
  expect(deskFlow(STEPS)).toBe(STEPS);
});

it("lights where the agent is: reading, then the calendar, then a refused reply, then the reply again", () => {
  const steps: FlowStep[] = [
    { id: "read_gmail", kind: "read", source: "gmail", new_only: true },
    { id: "read_calendar", kind: "read", source: "calendar" },
    { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail", "read_calendar"] },
  ];
  expect(liveNodes(steps, [])).toEqual({ active: null, done: [], refused: null });
  expect(liveNodes(steps, [{ tool: "read" }])).toEqual({ active: "desk_read", done: ["read_gmail"], refused: null });
  const checking = liveNodes(steps, [{ tool: "read" }, { tool: "find_free_times", outcome: "done" }]);
  expect(checking.active).toBe("read_calendar");
  expect(checking.done).toEqual(expect.arrayContaining(["read_gmail", "desk_read", "desk"]));
  const refused = liveNodes(steps, [{ tool: "read" }, { tool: "find_free_times" }, { tool: "gmail.send", outcome: "refused" }]);
  expect(refused).toMatchObject({ active: "do_gmail.send", refused: "do_gmail.send" });
  // The next email starts afresh: only its own steps are lit.
  const next = liveNodes(steps, [{ tool: "read" }, { tool: "gmail.send", outcome: "done" }, { tool: "read" }]);
  expect(next).toEqual({ active: "desk_read", done: ["read_gmail"], refused: null });
  const html = renderToStaticMarkup(<WorkflowFlow steps={steps} actions={["gmail.send"]} active={refused.active} done={refused.done} refused={refused.refused} />);
  expect(html).toContain('data-refused="true"');
  expect(html).toContain('aria-label="Done"');
});
