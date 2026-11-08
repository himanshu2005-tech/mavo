import type { TaskRun } from "../types";
import { parseTime } from "./time";

/** How long a finished one-time task stays on the map, dimmed, before it goes. */
export const LINGER_MS = 10 * 60_000;

export type MemberState = "working" | "done" | "waiting" | "failed";
export interface TaskMember { name: string; state: MemberState }
export interface TaskOnMap { run: TaskRun; members: TaskMember[]; done: number; total: number; state: "working" | "needs" | "done" | "failed" }

/** The tasks the map shows: those working or waiting on you, and those that finished a little
 *  while ago (so the result does not vanish the moment it lands). */
export function visibleTasks(runs: TaskRun[], now = Date.now()): TaskRun[] {
  return runs.filter(r => r.status === "running" || r.status === "needs_you"
    || (r.finished_at && now - parseTime(r.finished_at).getTime() < LINGER_MS))
    .slice(0, 6);
}

/** A one-time task as a temporary team: who is on it (as its lead picked them), how each is doing
 *  (from the steps named after them: "React developer writes it"), and how far it has got. */
export function taskOnMap(run: TaskRun): TaskOnMap {
  const names = [...new Set(run.steps.flatMap(s => s.team ?? []))].slice(0, 8);
  const members = names.map(name => {
    const mine = run.steps.filter(s => s.title.toLowerCase().startsWith(name.toLowerCase()));
    const state: MemberState = mine.some(s => s.status === "running") ? "working"
      : mine.some(s => s.status === "failed") ? "failed"
      : mine.length && mine.every(s => s.status === "done" || s.status === "skipped") ? "done"
      : "waiting";
    return { name, state };
  });
  const counted = run.steps.filter(s => s.status !== "skipped");
  return {
    run, members,
    done: counted.filter(s => s.status === "done").length,
    total: counted.length,
    state: run.status === "running" ? "working" : run.status === "needs_you" ? "needs" : run.status === "done" ? "done" : "failed",
  };
}
