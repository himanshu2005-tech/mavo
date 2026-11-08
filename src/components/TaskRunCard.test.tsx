import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TaskRunCard, chatTimeline, moment, parseBuild } from "./TaskRunCard";
import type { TaskRun } from "../types";

const base: TaskRun = {
  id: "run", title: "Microsoft Form", status: "running", result: null, error: null, used_calls: 3, budget_calls: 15,
  conversation_id: "chat", created_at: "2026-11-04T05:00:00Z", finished_at: null,
  steps: [
    { id: "make", title: "Create the form", kind: "agent", status: "running" },
    { id: "check", title: "Check the fields", kind: "helper", status: "waiting" },
  ],
};
const html = (run: TaskRun) => renderToStaticMarkup(<TaskRunCard run={run} />);
const has = (markup: string, ...parts: string[]) => parts.forEach(part => expect(markup).toContain(part));

it("shows who does what while a task is running, with a way to stop it", () => {
  const markup = html(base);
  has(markup, "Microsoft Form", "3 of 15 model calls", "Working", "Create the form", "Agent", "Helper", "Stop");
  expect(markup).not.toContain("Copy");
});

it("shows the result and nothing but the result once it is done", () => {
  const markup = html({ ...base, status: "done", result: "https://forms.office.com/r/abc123", steps: base.steps.map(s => ({ ...s, status: "done" as const })) });
  has(markup, "Done", "https://forms.office.com/r/abc123", "Copy");
  expect(markup).not.toContain("Stop");
});

it("says plainly what it needs from the person when it cannot go on", () => {
  const markup = html({ ...base, status: "needs_you", steps: [{ ...base.steps[0], status: "blocked", question: "Turn on in Actions (Defaults): Open page. Tip: Set all lets one-time tasks run without asking." }] });
  has(markup, "Needs you", "Turn on in Actions (Defaults): Open page", "Set all");
});

it("gives the sign-in address and a way to sign in and try again when a site needs the person", () => {
  const markup = html({ ...base, status: "needs_you", sign_in_url: "https://forms.office.com", steps: [{ ...base.steps[0], status: "blocked", question: "Please sign in. Sign in at https://forms.office.com with the Sign in button on the task card, then press Try again." }] });
  has(markup, "Sign in</button>", "https://forms.office.com", "Try again");
});

it("offers Try again, but no Sign in, when the person only has to turn something on", () => {
  const markup = html({ ...base, status: "needs_you", steps: [{ ...base.steps[0], status: "blocked", question: "Turn on in Actions (Defaults): Open page." }] });
  has(markup, "Try again");
  expect(markup).not.toContain("Sign in</button>");
  has(html({ ...base, status: "failed", error: "The agent ran out of steps." }), "Try again");
  expect(html(base)).not.toContain("Try again");
  expect(html({ ...base, status: "done", result: "x" })).not.toContain("Try again");
});

it("shows what the agent asks and a box to answer it, only while it is waiting", () => {
  const asking = { ...base, steps: [{ ...base.steps[0], ask: "Which name should the form carry?", asked_at: "2026-11-04T05:01:00Z" }, base.steps[1]] };
  const markup = html(asking);
  has(markup, "Which name should the form carry?", "Your answer", "Send", "Asks you", "passwords and codes in Mavo&#x27;s browser", "Stop");
  const quiet = html(base);
  expect(quiet).not.toContain("Your answer");
  expect(quiet).not.toContain("Asks you");
  // Once the task has ended the question is no longer open, whatever the step still carries.
  expect(html({ ...asking, status: "needs_you" })).not.toContain("Your answer");
});

const BUILT = "Built in C:\\Users\\me\\Documents\\Mavo Projects\\expense-todo. Open C:\\Users\\me\\Documents\\Mavo Projects\\expense-todo\\index.html in your browser to use it.\nTEST: problems found.\n- The page opened and shows content.\n- Typing an item and pressing Enter or Add did NOT put it on the page.\n- You asked for React, but the app does NOT use it.\n- Pressed each of its 1 buttons once.";

it("shows what a build team made as a card: name, folder, open buttons, and each check", () => {
  const build = parseBuild(BUILT)!;
  expect(build.project).toBe("expense-todo");
  expect(build.passed).toBe(false);
  expect(build.checks.map(c => c.ok)).toEqual([true, false, false, true]);
  const markup = html({ ...base, status: "done", result: BUILT, steps: base.steps.map(s => ({ ...s, status: "done" as const })) });
  has(markup, "Built, with problems", "expense-todo", "Open app", "Show folder", "Pressed each of its 1 buttons once.");
  expect(markup).not.toContain("TEST: problems found.");
  expect(parseBuild(BUILT.replace("TEST: problems found.", "TEST: passed."))!.passed).toBe(true);
  expect(parseBuild("The answer is 42.")).toBeNull();
});

it("asks the person to decide with buttons when the team is out of rounds", () => {
  const ask = "2 problems left after 3 rounds of fixes: Script error: x. Keep fixing (about 4 more model calls) or stop here?";
  const markup = html({ ...base, steps: [{ id: "ask3", title: "Your call: keep fixing or stop", kind: "skill", status: "running", ask, asked_at: "2026-11-05T05:01:00Z", choices: ["Keep fixing", "Stop here"] }] });
  has(markup, "Keep fixing</button>", "Stop here</button>", "2 problems left after 3 rounds");
  // And a box for their own words ("yes continue", "fix only the timer bug").
  has(markup, "Or say it in your words", "fix only the timer bug");
  const left = parseBuild(BUILT.replace("- Pressed", "- Still open: Security Reviewer: the amount is not checked.\n- Pressed"))!;
  expect(left.checks.find(c => c.text.startsWith("Still open"))!.ok).toBe(false);
});

it("shows how each step thought, in order, folded away until asked for", () => {
  const thinking = [{ label: "Goal", text: "Merge the feeds into one list." }, { label: "Check", text: "A failed feed never breaks the rest." }];
  const markup = html({ ...base, steps: [{ ...base.steps[0], status: "done", thinking }, base.steps[1]] });
  has(markup, "<details class=\"task-thought\">", "How it thought", "2 steps", "<b>Goal</b>Merge the feeds into one list.", "<b>Check</b>");
  expect(html(base)).not.toContain("How it thought");
});

it("shows any other result as a formatted report, maths included", () => {
  const markup = html({ ...base, status: "done", result: "**Total:** $$a^2+b^2$$" });
  has(markup, "katex", "<strong>Total:</strong>");
});

it("reads server times without a zone as UTC, so cards fall in order with messages", () => {
  expect(moment("2026-11-05T10:00:00")).toBe(Date.parse("2026-11-05T10:00:00Z"));
  expect(moment("2026-11-05T10:00:00+05:30")).toBe(Date.parse("2026-11-05T04:30:00Z"));
});

it("never shows a reply above the message it answers, whatever order they arrive in", () => {
  const at = "2026-11-05T16:00:00.123";
  const reply = { id: "a", role: "assistant", content: "I'll take a look.", created_at: at };
  const asked = { id: "u", role: "user", content: "Plan my day.", created_at: at };
  const order = (items: ReturnType<typeof chatTimeline>) => items.map(i => i.kind === "message" ? i.message.role : i.kind);
  expect(order(chatTimeline([reply, asked], [{ ...base, created_at: at }]))).toEqual(["user", "assistant", "task"]);
  expect(order(chatTimeline([asked, reply], []))).toEqual(["user", "assistant"]);
  // An added Crew stays where it was proposed, not under every later message and task.
  const crew = { status: "active", created_at: "2026-11-05T16:00:01", updated_at: "2026-11-05T18:30:00" };
  const later = { ...asked, id: "u2", content: "Find research problems.", created_at: "2026-11-05T17:00:00" };
  const placed = (items: ReturnType<typeof chatTimeline>) => items.map(i => i.kind === "message" ? (i.message as unknown as { id: string }).id : i.kind);
  expect(placed(chatTimeline([asked, reply, later], [{ ...base, created_at: "2026-11-05T17:00:02" }], [crew]))).toEqual(["u", "a", "setup", "u2", "task"]);
  // A draft waiting to be added stays at the bottom, by the message box, even when the latest
  // reply ("maybe hourly") did not change it.
  expect(placed(chatTimeline([asked, reply, later], [{ ...base, created_at: "2026-11-05T17:00:02" }], [{ ...crew, status: "draft", updated_at: "2026-11-05T16:00:01" }]))).toEqual(["u", "a", "u2", "task", "setup"]);
  // A later moment still wins over the tie rule.
  expect(order(chatTimeline([{ ...asked, created_at: "2026-11-05T16:00:05" }, reply], []))).toEqual(["assistant", "user"]);
});

it("reports a failure or a stop without pretending it finished", () => {
  const failed = html({ ...base, status: "failed", error: "The task's model-call budget ran out." });
  has(failed, "Could not finish", "budget ran out");
  expect(failed).not.toContain("Copy");
  has(html({ ...base, status: "stopped", error: "Stopped." }), "Stopped");
});
