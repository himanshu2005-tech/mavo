import { expect, it } from "vitest";
import type { CrewRun } from "../types";
import { finishedSince, runOutcome } from "./runOutcome";

it("says how a run the person started ended, never nothing", () => {
  expect(runOutcome("Meeting Reply Desk", { status: "completed", result: "Nothing new.", error: null })).toBe("Meeting Reply Desk checked: nothing new this time.");
  expect(runOutcome("Desk", { status: "completed", result: "**Draft created**\nTo: a@b.com", error: null })).toBe("Desk: Draft created");
  expect(runOutcome("Desk", { status: "failed", result: null, error: "Connect Google first" })).toBe("Desk stopped: Connect Google first");
});

it("finds the run that finished after the person pressed Run now", () => {
  const run = (created_at: string, status: CrewRun["status"]) => ({ created_at, status }) as CrewRun;
  const since = Date.parse("2026-11-13T07:09:00Z");
  expect(finishedSince([run("2026-11-13T07:09:41", "running")], since)).toBeNull();
  expect(finishedSince([run("2026-11-13T07:09:41", "completed"), run("2026-11-13T07:03:00", "completed")], since)?.created_at).toBe("2026-11-13T07:09:41");
  expect(finishedSince([run("2026-11-13T07:03:00", "completed")], since)).toBeNull();
});
