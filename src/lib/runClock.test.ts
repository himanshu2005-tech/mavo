import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { listens, runClock, timeLeft } from "./runClock";

const NOW = Date.parse("2026-11-06T10:00:00Z");
const iso = (min: number) => new Date(NOW + min * 60_000).toISOString();
const crew = (over: Partial<CrewInstance> = {}) => ({
  id: "c", name: "News Watch", status: "active", is_running: true, runtime_state: "waiting", trigger_type: "interval",
  review_interval_minutes: 30, next_run_at: iso(12), last_run_at: iso(-18), configuration_plan: {}, ...over,
}) as unknown as CrewInstance;

it("puts the dot where the agent is between its last run and its next", () => {
  const clock = runClock(crew(), NOW)!;
  expect(clock.progress).toBeCloseTo(18 / 30);
  expect(clock.label).toBe("in 12 min");
  expect(clock.every).toBe("every 30 min");
  expect(clock.canRunNow).toBe(true);
});

it("uses the schedule when there is no sensible last run, and is due when the time has come", () => {
  expect(runClock(crew({ last_run_at: null }), NOW)!.progress).toBeCloseTo(18 / 30);
  const due = runClock(crew({ next_run_at: iso(-1) }), NOW)!;
  expect([due.progress, due.label]).toEqual([1, "any moment"]);
});

it("a daily reminder turns once a day and runs at its time only", () => {
  const clock = runClock(crew({ trigger_type: "daily", next_run_at: iso(360), last_run_at: null }), NOW)!;
  expect(clock.progress).toBeCloseTo(1 - 360 / 1440);
  expect(clock.every).toBe("every day");
  expect(clock.canRunNow).toBe(false);
});

it("has no clock when there is nothing to count down to", () => {
  for (const over of [{ is_running: false }, { status: "draft" }, { runtime_state: "working" }, { runtime_state: "needs_input" },
    { trigger_type: "manual" }, { trigger_type: "gmail_message" }, { next_run_at: null }] as Partial<CrewInstance>[])
    expect(runClock(crew(over), NOW)).toBeNull();
});

it("says how long is left the way people say it", () => {
  expect([timeLeft(30_000), timeLeft(90 * 60_000), timeLeft(120 * 60_000)]).toEqual(["in under a minute", "in 1 h 30 min", "in 2 h"]);
  expect(runClock(crew({ review_interval_minutes: 120, next_run_at: iso(60), last_run_at: null }), NOW)!.every).toBe("every 2 h");
});

it("knows an agent that waits for a mail is listening", () => {
  expect([listens(crew({ trigger_type: "gmail_message" })), listens(crew()), listens(crew({ trigger_type: "gmail_message", is_running: false }))]).toEqual([true, false, false]);
});

it("rounds the whole time, never showing 60 minutes", () => {
  expect(timeLeft((4 * 60 + 59.6) * 60_000)).toBe("in 5 h");
  expect(timeLeft((4 * 60 + 20.2) * 60_000)).toBe("in 4 h 20 min");
});
