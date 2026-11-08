import { expect, it } from "vitest";
import type { ActionRequest, CrewRun, Reminder } from "../types";
import { happenings, troubles } from "./EmptyPages";

const NOW = Date.parse("2026-11-06T15:00:00Z");
const action = (over: Partial<ActionRequest>): ActionRequest => ({
  id: Math.random().toString(36).slice(2), crew_id: "jobs", crew_name: "Placements Job Watch", source: "crew", operation: "browser.read",
  arguments: {}, status: "completed", result: null, error: null, created_at: "2026-11-06T10:00:00", finished_at: null, ...over,
});

it("says what each agent did in plain words, with what it was about", () => {
  const list = happenings([action({ operation: "browser.open", arguments: { url: "https://www.example.com/jobs" } })], [], []);
  expect(list[0]).toMatchObject({ agent: "Placements Job Watch", what: "Opened a page", target: "example.com", outcome: "done" });
});

it("puts what went wrong lately first: one line per agent, how often, and the latest reason", () => {
  const list = happenings([
    action({ status: "failed", error: "Open a page in Mavo's browser first", created_at: "2026-11-06T03:56:00" }),
    action({ status: "failed", error: "Open a page in Mavo's browser first", operation: "browser.click", created_at: "2026-11-06T03:55:00" }),
    action({ crew_id: "credits", crew_name: "Credit Watch", status: "failed", error: "Old problem", created_at: "2026-10-20T10:00:00" }),
  ], [], []);
  expect(troubles(list, NOW)).toEqual([{ agentId: "jobs", agent: "Placements Job Watch", count: 2, reason: "Open a page in Mavo's browser first", at: "2026-11-06T03:56:00", fixed: false }]);
});

it("an agent that worked again since is fine now, and is not listed", () => {
  const list = happenings([
    action({ status: "completed", created_at: "2026-11-06T12:00:00" }),
    action({ status: "failed", error: "Timed out", created_at: "2026-11-06T09:00:00" }),
  ], [], []);
  expect(troubles(list, NOW)).toEqual([]);
});

it("sessions and updates join the same timeline, newest first", () => {
  const run = { id: "r", crew_id: "inbox", crew_name: "Inbox Summary", status: "failed", error: "The model took too long.\nmore", result: null, created_at: "2026-11-06T11:00:00" } as unknown as CrewRun;
  const update = { id: "m", crew_id: "inbox", crew_name: "Inbox Summary", message: "3 important mails\nmore", scheduled_for: "2026-11-06T12:00:00" } as unknown as Reminder;
  const list = happenings([], [run], [update]);
  expect(list.map(h => [h.what, h.outcome, h.reason])).toEqual([["3 important mails", "said", undefined], ["Could not finish its work", "failed", "The model took too long."]]);
});
