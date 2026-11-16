import { expect, it } from "vitest";
import type { CrewRun, TaskRun } from "../types";
import { pastRuns, runLine, whenWords } from "./RunHistory";

const task = (id: string, at: string, extra: Partial<TaskRun>): TaskRun => ({ id, title: "Check", status: "done", steps: [], result: null, error: null, used_calls: 3, budget_calls: 12, conversation_id: null, created_at: at, finished_at: null, ...extra });
const old = { id: "c1", crew_id: "x", crew_name: "x", route_name: "Mavo Provider", provider: "cloud", model: "qwen", prompt: "", status: "completed", result: "Disk C: 41 GB free", error: null, model_calls: 2, created_at: "2026-11-13T08:00:00Z", finished_at: "2026-11-13T08:00:42Z", kind: "run" } as CrewRun;

it("puts runs from both records in order, oldest first, with what each did", () => {
  const runs = pastRuns([old], [
    task("t2", "2026-11-15T09:00:00Z", { status: "failed", error: "It could not find the answer on the pages it read (airouter.in)." }),
    task("t1", "2026-11-14T09:00:00Z", { result: String.raw`Credits left: \textbf{\$0.50}`, steps: [{ id: "s", title: "Read the site", kind: "agent", status: "done", thinking: [{ label: "", text: "", tool: "browser.open", outcome: "done", about: "airouter.in" }, { label: "", text: "Reading it." }] }] }),
  ]);
  expect(runs.map(r => r.id)).toEqual(["c1", "t1", "t2"]);
  expect(runs[0]).toMatchObject({ state: "done", calls: 2, model: "Mavo Provider · qwen", took: 42 });
  expect(runs[1].cards.map(c => c.tool)).toEqual(["browser.open"]);  // the steps it took, as cards
  expect(runs[1].steps).toEqual([{ title: "Read the site", status: "done" }]);
});

it("sums each run up in one plain line", () => {
  const [, ok, failed] = pastRuns([old], [
    task("t1", "2026-11-14T09:00:00Z", { result: String.raw`Credits left: \textbf{\$0.50}` }),
    task("t2", "2026-11-15T09:00:00Z", { status: "failed", error: "It could not find the answer on the pages it read (airouter.in)." }),
  ]);
  expect(runLine(ok)).toBe("Credits left: $0.50");
  expect(runLine(failed)).toBe("Couldn't finish: It could not find the answer on the pages it read (airouter.in).");
  expect(runLine({ ...ok, state: "needs", error: "Please sign in to airouter.in" })).toBe("Needed you: Please sign in to airouter.in");
  expect(runLine({ ...ok, report: "" })).toBe("Done, nothing new.");
});

it("says when in words", () => {
  const now = new Date("2026-11-15T12:00:00");
  expect(whenWords(new Date("2026-11-15T09:05:00").toISOString(), now)).toMatch(/^Today /);
  expect(whenWords(new Date("2026-11-14T09:05:00").toISOString(), now)).toMatch(/^Yesterday /);
  expect(whenWords(new Date("2026-11-10T09:05:00").toISOString(), now)).not.toMatch(/^(Today|Yesterday)/);
});

it("lists a run once when both records carry it, keeping the one with its steps", () => {
  const both = { id: "t1", crew_id: "x", crew_name: "x", route_name: "", provider: "", model: "", prompt: "", status: "completed", result: "Credits left: $0.50", error: null, model_calls: 4, created_at: "2026-11-14T09:00:00Z", finished_at: null, kind: "task" } as CrewRun;
  const runs = pastRuns([both], [task("t1", "2026-11-14T09:00:00Z", { result: "Credits left: $0.50", steps: [{ id: "s", title: "Read", kind: "agent", status: "done" }] })]);
  expect(runs).toHaveLength(1);
  expect(runs[0].steps).toEqual([{ title: "Read", status: "done" }]);
});
