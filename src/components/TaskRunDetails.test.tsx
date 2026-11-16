import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { TaskRun } from "../types";
import { TaskRunCard } from "./TaskRunCard";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => false }));

const run = { id: "t1", title: "Summarise my emails", status: "done", result: "Inbox Summary: 3 emails need you", error: null,
  used_calls: 1, budget_calls: 160, conversation_id: null, created_at: "2026-11-15T17:00:00Z", finished_at: "2026-11-15T17:01:00Z",
  steps: [{ id: "s", title: "Read inbox", kind: "agent", status: "done" }] } as unknown as TaskRun;

it("under a result already shown, says only how it was done: never the title, status or result again", () => {
  const html = renderToStaticMarkup(<TaskRunCard run={run} details />);
  expect(html).not.toContain("3 emails need you");
  expect(html).not.toContain(">Summarise my emails<");
  expect(html).not.toContain(">Done<");
  expect(html).toContain("1 of 160 model calls");
  expect(html).toContain("Read inbox");
  // On its own it still shows everything.
  expect(renderToStaticMarkup(<TaskRunCard run={run} />)).toContain("3 emails need you");
});
