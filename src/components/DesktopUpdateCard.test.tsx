import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { DesktopUpdateCard, ago } from "./DesktopUpdateCard";
import { isFresh } from "../lib/time";
import type { Reminder } from "../types";

const base: Reminder = { kind: "crew_update", id: "r", crew_id: "owner", crew_name: "Build: Expense Tracker", message: "Needs your answer. Keep fixing or stop here?", scheduled_for: "2026-11-05T16:00:00Z", local_date: "2026-11-05" };
const html = (r: Reminder) => renderToStaticMarkup(<DesktopUpdateCard reminder={r} busy={false} onDismiss={() => undefined} onOpen={() => undefined} onSpeak={() => undefined} />);

it("reads the server's zoneless times as UTC, so a fresh update says now, not 5h", () => {
  const now = Date.parse("2026-11-05T16:00:00Z");
  expect(ago("2026-11-05T15:58:30", now)).toBe("1m");
  expect(ago("2026-11-05T15:59:50.123456", now)).toBe("now");
  expect(ago("2026-11-05T13:00:00+00:00", now)).toBe("3h");
});

it("pops up only what just happened; what came while the laptop was closed waits in Missed", () => {
  const now = Date.parse("2026-11-05T16:00:00Z");
  expect(isFresh("2026-11-05T15:50:00", now)).toBe(true);
  expect(isFresh("2026-11-05T15:40:00", now)).toBe(false);
  expect(isFresh("2026-11-05T09:00:00", now)).toBe(false);
});

it("answers a task's question on the desktop, never through a Crew it does not have", () => {
  // It was sent to Mavo's hidden owner Crew: "Crew not found".
  const task = html({ ...base, task_run_id: "run", task_step_id: "ask3" });
  expect(task).not.toContain(">Ask<");
  expect(task).not.toContain(">Open<");
  const crew = html({ ...base, crew_name: "News Watch", message: "Three new stories." });
  expect(crew).toContain("Ask");
  expect(crew).toContain("Open");
});

it("renders every digest item in a keyboard-accessible scroll region instead of +more", () => {
  const message = "## News Reader\n\n" + Array.from({length: 8}, (_, i) => `- Story ${i + 1} with source`).join("\n");
  const view = html({...base, message});
  for (let i = 1; i <= 8; i++) expect(view).toContain(`Story ${i} with source`);
  expect(view).not.toContain("report-more");
  expect(view).toContain('aria-label="Notification contents"');
  expect(view).toContain('tabindex="0"');
});

it("offers full screen and a clear restore control without removing dismissal", () => {
  const props = {reminder: base, busy: false, onDismiss: () => {}, onOpen: () => {}, onSpeak: () => {}, onToggleFullScreen: () => {}};
  const small = renderToStaticMarkup(<DesktopUpdateCard {...props} />);
  expect(small).toContain('aria-label="Full screen"');
  expect(small).toContain('aria-pressed="false"');
  const large = renderToStaticMarkup(<DesktopUpdateCard {...props} fullScreen />);
  expect(large).toContain('aria-label="Restore notification size"');
  expect(large).toContain('aria-pressed="true"');
  expect(large).toContain("Done");
});

it("a change waiting for the OK is approved or denied on the alert, never just closed", () => {
  // It showed the task answer box ("Already answered, or the task has ended") and only Done.
  const view = html({ ...base, kind: "approval", message: "Waiting for your OK: Create event.", task_run_id: "run", task_step_id: "action:a1" });
  expect(view).toContain("Loading…");  // then its Run or Approve and Deny (WaitingChanges)
  expect(view).not.toContain(">Done<");
  expect(view).not.toContain("Already answered");
});

it("an agent's question offers its choices and a box for your own words", () => {
  const view = html({ ...base, kind: "question", message: "Ravi: Roadmap: Thursday is busy. What should I do?", task_run_id: "run", task_step_id: "desk:t1", choices: ["Offer Friday instead", "Ask Ravi for other times"] });
  expect(view).toContain(">Offer Friday instead<");
  expect(view).toContain(">Ask Ravi for other times<");
  expect(view).toContain('placeholder="Or in your words…"');
  expect(view).not.toContain(">Done<");
  const open = html({ ...base, kind: "question", message: "What should I do?", task_run_id: "run", task_step_id: "desk:t1" });
  expect(open).toContain('placeholder="Your answer"');
});

it("an alert that waits for an answer is marked so nothing replaces it while it is open", () => {
  expect(html({ ...base, kind: "question", task_step_id: "desk:t1", choices: ["Yes"] })).toContain('data-answerable="true"');
  expect(html({ ...base, kind: "approval", task_step_id: "action:a1" })).toContain('data-answerable="true"');
  expect(html({ ...base, task_run_id: "run", task_step_id: "ask3" })).toContain('data-answerable="true"');  // a task's question
  expect(html({ ...base, crew_name: "News Watch", message: "Three new stories." })).not.toContain("data-answerable");
});
