import { expect, it } from "vitest";
import type { CrewAsk, Reminder } from "../types";
import { threadOf } from "./AgentInbox";

const NOW = Date.parse("2026-11-06T15:00:00Z");

it("weaves what agents said and what you asked them into one thread, oldest first, for the last week", () => {
  const updates = [
    { id: "1", crew_id: "inbox", crew_name: "Inbox Summary", message: "3 important mails", scheduled_for: "2026-11-06T08:00:00", kind: "crew_update" },
    { id: "0", crew_id: "inbox", crew_name: "Inbox Summary", message: "Old", scheduled_for: "2026-10-01T08:00:00", kind: "crew_update" },
  ] as Reminder[];
  const asks = { inbox: { name: "Inbox Summary", items: [
    { id: "q1", question: "Which one is urgent?", answer: "The bill, due Friday.", pending: false, at: "2026-11-06T09:00:00" },
    { id: "q2", question: "Anything from Prof. Rao?", answer: null, pending: true, at: "2026-11-06T10:00:00" },
  ] as CrewAsk[] } };
  expect(threadOf(updates, asks, NOW).map(t => [t.from, t.text, !!t.pending])).toEqual([
    ["agent", "3 important mails", false],
    ["you", "Which one is urgent?", false],
    ["agent", "The bill, due Friday.", false],
    ["you", "Anything from Prof. Rao?", false],
    ["agent", "", true],
  ]);
});

it("names a one-time task for what it is, and an agent by its name", async () => {
  const { senderOf, dayLabel } = await import("./AgentInbox");
  expect(senderOf("Build: RSS Feed Reader", false)).toEqual({ label: "RSS Feed Reader", task: true });
  expect(senderOf("One-time ea64f6 build: Create the Microsoft Form", false)).toEqual({ label: "Create the Microsoft Form", task: true });
  expect(senderOf("Inbox Summary", true)).toEqual({ label: "Inbox Summary", task: false });
  const now = new Date("2026-11-06T15:00:00Z");
  expect([dayLabel("2026-11-06T09:00:00Z", now), dayLabel("2026-11-05T09:00:00Z", now)]).toEqual(["Today", "Yesterday"]);
});

it("an agent you removed is not mistaken for a one-time task", async () => {
  const { senderOf } = await import("./AgentInbox");
  expect(senderOf("Old Weather Watch", false)).toEqual({ label: "Old Weather Watch", task: false, gone: true });
});

it("answers to held work use the same words as its card", async () => {
  const { heldWords } = await import("./AgentInbox");
  expect(heldWords({ held: { kind: "ask" } } as never)).toEqual({ yes: "Yes", no: "No" });
  expect(heldWords({ held: { kind: "approve" } } as never)).toEqual({ yes: "Approve", no: "Send back" });
  expect(heldWords({ held: { kind: "review_stuck" } } as never)).toEqual({ yes: "Send it on", no: "Send back" });
});
