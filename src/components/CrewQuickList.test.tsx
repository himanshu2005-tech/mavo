import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CrewQuickList, nextRun } from "./CrewQuickList";
import { CrewGrants, grantsFor } from "./CrewGrants";
import type { CrewInstance } from "../types";

const NOW = Date.parse("2026-11-06T08:52:00Z");
const crew = (over: Partial<CrewInstance>): CrewInstance => ({
  id: "c", template_id: "research", name: "News Watch", responsibilities: "", autonomy_mode: "reactive", status: "active",
  provider: "airouter", model: "m", route_id: "r", max_model_calls: 4, schedule_time: null, reminder_text: null, resources: [],
  permissions: [], triggers: [], created_at: "2026-11-05T10:00:00", updated_at: "2026-11-05T10:00:00", is_running: true,
  runtime_state: "waiting", review_interval_minutes: 30, daily_call_limit: 96, daily_calls_used: 0, trigger_type: "interval",
  next_run_at: null, last_runtime_error: null, ...over,
} as CrewInstance);

it("says when each Crew runs next, in plain words", () => {
  expect(nextRun(crew({ next_run_at: "2026-11-06T09:04:00" }), NOW).text).toBe("in 12 min");
  expect(nextRun(crew({ next_run_at: "2026-11-06T08:51:00" }), NOW).text).toBe("Any moment");
  expect(nextRun(crew({ trigger_type: "gmail_message", next_run_at: null }), NOW).text).toBe("When mail arrives");
  expect(nextRun(crew({ runtime_state: "needs_input", next_run_at: "2026-11-06T09:04:00" }), NOW)).toEqual({ text: "Needs you", tone: "wait" });
  expect(nextRun(crew({ is_running: false }), NOW)).toEqual({ text: "Stopped", tone: "off" });
  expect(nextRun(crew({ next_run_at: "2026-11-06T13:00:00" }), NOW).text).toMatch(/^at /);
});

it("lists every Crew with a button to run it now", () => {
  const markup = renderToStaticMarkup(<CrewQuickList initial={[crew({ name: "Battery Watch", runtime_state: "needs_input" }), crew({ id: "n", name: "News Watch" })]} onBack={() => undefined} onOpen={() => undefined} />);
  expect(markup).toContain("Battery Watch");
  expect(markup).toContain("Has a question"); // why it waits, not just that it does
  expect(markup).toContain('aria-label="Run News Watch now"');
});

it("offers the permission a stopped Crew is waiting for, right under its message", () => {
  // "Allow device metrics in Edit to monitor this computer." used to send the person hunting.
  const stuck = crew({ template_id: "computer", name: "Battery Watch", last_runtime_error: "Allow device metrics in Edit to monitor this computer.", permissions: ["model:invoke"] });
  expect(grantsFor(stuck).map(g => g.change)).toEqual([{ allow_device_metrics: true }]);
  expect(renderToStaticMarkup(<CrewGrants crew={stuck} />)).toContain("Read device metrics");
  // Already allowed, or nothing asked: nothing shown.
  expect(grantsFor({ ...stuck, permissions: ["device:read"] })).toEqual([]);
  expect(renderToStaticMarkup(<CrewGrants crew={crew({ last_runtime_error: "The model took too long." })} />)).toBe("");
});

it("offers to free space when the Cleaner is waiting for it", () => {
  const cleaner = crew({ template_id: "computer", name: "Temp & Drive Cleaner", last_runtime_error: "Allow cleaning temp files to free space on this computer.", permissions: ["device:read"] });
  expect(grantsFor(cleaner).map(g => g.change)).toEqual([{ allow_temp_cleanup: true }]);
  expect(renderToStaticMarkup(<CrewGrants crew={cleaner} />)).toContain("Free space on this computer");
  expect(grantsFor({ ...cleaner, permissions: ["device:read", "device:clean_temp"] })).toEqual([]);
});

it("says why an agent is waiting, in one short line", async () => {
  const { needsReason } = await import("./CrewQuickList");
  expect(needsReason(crew({ runtime_state: "needs_input", question: "Which inbox label counts as urgent?\nMore detail." }))).toBe("Which inbox label counts as urgent?");
  expect(needsReason(crew({ runtime_state: "error", last_runtime_error: "Allow device metrics in Edit to monitor this computer." }))).toBe("Allow device metrics in Edit to monitor this computer.");
  expect(needsReason(crew({ runtime_state: "needs_setup" }))).toBe("Finish setting it up");
  expect(needsReason(crew({ runtime_state: "error", last_runtime_error: null }))).toBe("Stopped by a problem");
});

it("the desktop menu names the one agent that needs you, and why", async () => {
  const { OrbMenu } = await import("../pages/StatusOverlay");
  const noop = () => undefined;
  const props = { headline: "7 running", tone: "good" as const, running: 7, missed: 0, muted: false, confirmQuit: false, onOpen: noop, onAgents: noop, onMissed: noop, onAttention: noop, onNeed: noop, onMute: noop, onUnmute: noop, onAskQuit: noop, onQuit: noop };
  const one = renderToStaticMarkup(<OrbMenu {...props} pending={1} needs={[{ id: "b", name: "Battery Watch", reason: "Allow device metrics in Edit." }]} />);
  expect(one).toContain("Battery Watch needs you");
  expect(one).toContain("Allow device metrics in Edit.");
  expect(renderToStaticMarkup(<OrbMenu {...props} pending={3} needs={[]} />)).toContain("3 agents need you");
});

it("out of today's AI calls is not something the person must deal with: it is back tomorrow", () => {
  expect(nextRun(crew({ runtime_state: "budget_paused" }), NOW)).toEqual({ text: "Back tomorrow (today's AI calls used)", tone: "ok" });
});
