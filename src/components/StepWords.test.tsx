import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { TaskRun } from "../types";
import { TaskRunCard, shownThoughts } from "./TaskRunCard";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => false }));

const run = { id: "t", title: "Momentum Spark", status: "done", result: "Keep going.", error: null, used_calls: 2, budget_calls: 12,
  conversation_id: null, created_at: "2026-11-15T17:55:00Z", finished_at: "2026-11-15T17:55:30Z",
  steps: [{ id: "s3", title: "Select Message", run_text: "Picking today's quote", kind: "helper", status: "done",
    thinking: [{ label: "Goal", text: "Extract the message" }, { label: "Approach", text: 'Retrieve {"message": "..."}' }, { label: "", text: "Read 14 emails" }] }] } as unknown as TaskRun;

it("a step reads as what it does, with its working notes and JSON left out", () => {
  const html = renderToStaticMarkup(<TaskRunCard run={run} />);
  expect(html).toContain("Picking today&#x27;s quote");
  expect(html).not.toContain(">Select Message<");
  expect(html).not.toContain("Extract the message");
  expect(html).not.toContain("{&quot;message&quot;");
  expect(html).toContain("Read 14 emails");
  expect(shownThoughts(run.steps[0].thinking).map(t => t.text)).toEqual(["Read 14 emails"]);
});
