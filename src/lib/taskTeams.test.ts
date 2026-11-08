import { expect, it } from "vitest";
import type { TaskRun } from "../types";
import { radialLayout } from "./radialGraph";
import { LINGER_MS, taskOnMap, visibleTasks } from "./taskTeams";

const step = (title: string, status: string, team?: string[]) => ({ id: title, title, kind: "helper", status, team }) as TaskRun["steps"][number];
const build = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: "t1", title: "Build: Combined RSS Reader", status: "running", result: null, error: null, used_calls: 5, budget_calls: 17,
  conversation_id: "c1", created_at: "2026-11-07T10:00:00", finished_at: null,
  steps: [
    step("Lead picked the team", "done", ["React developer", "Tester", "Integration finisher"]),
    step("React developer writes it", "done"),
    step("Tester tries it in a browser", "running"),
    step("Integration finisher fixes what was found", "waiting"),
  ],
  ...over,
});

it("a one-time task is a temporary team: who is on it, how each is doing, how far it has got", () => {
  const t = taskOnMap(build());
  expect(t.members).toEqual([{ name: "React developer", state: "done" }, { name: "Tester", state: "working" }, { name: "Integration finisher", state: "waiting" }]);
  expect([t.done, t.total, t.state]).toEqual([2, 4, "working"]);
});

it("shows tasks at work or waiting on you, and a finished one only for a while", () => {
  const now = Date.parse("2026-11-07T12:00:00Z");
  const runs = [
    build({ id: "a" }),
    build({ id: "b", status: "needs_you" }),
    build({ id: "c", status: "done", finished_at: new Date(now - LINGER_MS / 2).toISOString() }),
    build({ id: "d", status: "done", finished_at: new Date(now - LINGER_MS * 2).toISOString() }),
  ];
  expect(visibleTasks(runs, now).map(r => r.id)).toEqual(["a", "b", "c"]);
});

it("task teams sit on a ring of their own, outside the agents, so agents never move for them", () => {
  const ids = ["a", "b", "c"];
  const without = radialLayout(ids, [], 0);
  const withTask = radialLayout(ids, [], 0, [3]);
  const d = (l: typeof without, p: { x: number; y: number }) => Math.hypot((p.x - l.c.x) / 1.45, (p.y - l.c.y) / 0.8);
  expect(withTask.tasks).toHaveLength(1);
  expect(withTask.tasks[0].members).toHaveLength(3);
  expect(d(withTask, withTask.tasks[0].hub)).toBeGreaterThan(d(withTask, withTask.agents.a) + 300);
  expect(d(withTask, withTask.agents.a)).toBeCloseTo(d(without, without.agents.a)); // agents stay where they were
});
