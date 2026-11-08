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
