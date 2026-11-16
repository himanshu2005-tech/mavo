import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { parseTime } from "../lib/time";
import { plainError } from "../lib/shownText";
import type { CrewInstance, CrewRun, TaskRun, Thought } from "../types";
import { Report, tidyReport } from "./Report";
import { ThoughtCard, isCard } from "./ThoughtCard";
import { ReplayButton } from "./ReplayButton";

/** One past run, as the person reads it: when, what came of it, and (on opening it) the whole
 *  report, the steps it took, and the technical details folded away. */
export interface PastRun {
  id: string; at: string; state: "done" | "failed" | "needs" | "stopped" | "running";
  report: string | null; error: string | null; cards: Thought[];
  calls: number; model?: string; took?: number; steps?: { title: string; status: string }[];
  traces?: string[];
}

/** Runs from either record (older crews logged them one way, crews with a plan another), oldest
 *  first: a room reads top to bottom, newest last. */
export function pastRuns(crewRuns: CrewRun[], tasks: TaskRun[]): PastRun[] {
  const took = (a: string, b: string | null) => b ? Math.max(0, Math.round((parseTime(b).getTime() - parseTime(a).getTime()) / 1000)) : undefined;
  const fromCrew = crewRuns.map((r): PastRun => ({
    id: r.id, at: r.created_at, report: r.result, error: r.error, cards: [], calls: r.model_calls,
    model: [r.route_name, r.model].filter(Boolean).join(" · ") || undefined, took: took(r.created_at, r.finished_at),
    state: r.status === "completed" ? "done" : r.status === "running" ? "running" : r.status === "failed" ? "failed" : "stopped",
  }));
  const fromTasks = tasks.map((t): PastRun => ({
    id: t.id, at: t.created_at, report: t.result, error: t.error, calls: t.used_calls, took: took(t.created_at, t.finished_at),
    cards: t.steps.flatMap(s => s.thinking ?? []).filter(isCard), traces: t.traces ?? [],
    steps: t.steps.map(s => ({ title: s.run_text || s.title, status: s.status })),
    state: t.status === "done" ? "done" : t.status === "running" ? "running" : t.status === "needs_you" ? "needs" : t.status === "failed" ? "failed" : "stopped",
  }));
  // The runs list already carries task runs too (same ids): each once, the task's own record kept
  // (it has the steps it took).
  const taskIds = new Set(fromTasks.map(t => t.id));
  return [...fromCrew.filter(r => !taskIds.has(r.id)), ...fromTasks].sort((a, b) => parseTime(a.at).getTime() - parseTime(b.at).getTime());
}

/** "Today 3:05 PM", "Yesterday 8:00 AM", "Fri 13 Nov, 9:30 AM". */
export function whenWords(iso: string, now = new Date()): string {
  const at = parseTime(iso);
  const time = at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const day = (d: Date) => d.toDateString();
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (day(at) === day(now)) return `Today ${time}`;
  if (day(at) === day(yesterday)) return `Yesterday ${time}`;
  return `${at.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
}

/** The one line a run is summed up by: what it found, or why it could not finish. */
export function runLine(run: PastRun): string {
  if (run.state === "running") return "Working…";
  const first = tidyReport(run.report ?? "").split("\n").map(l => l.replace(/^[#>\-*\d.)\s]+/, "").replace(/\*\*/g, "").trim()).find(Boolean);
  if (run.state === "done") return first || "Done, nothing new.";
  const why = plainError((run.error ?? "").split("\n").map(l => l.trim()).find(Boolean) ?? "");
  if (run.state === "needs") return why ? `Needed you: ${why}` : "Needed you";
  if (run.state === "stopped") return "Stopped";
  return why ? `Couldn't finish: ${why}` : "Couldn't finish";
}

const SHOWN = 5;

/** An agent's history in its room: every past run, newest last, the latest open. */
export function RunHistory({ crew, onCount }: { crew: CrewInstance; onCount?: (n: number) => void }) {
  const [runs, setRuns] = useState<PastRun[]>([]);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([api.crewRuns(crew.id).catch(() => []), api.crewTasks(crew.id).catch(() => [])]).then(([a, b]) => {
      if (!active) return;
      // What it is doing now shows live below; the history is what it has done.
      const done = pastRuns(a, b).filter(r => r.state !== "running");
      setRuns(done); onCount?.(done.length);
    });
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crew.id, crew.last_run_at, crew.runtime_state]);
  if (!runs.length) return null;
  const shown = all ? runs : runs.slice(-SHOWN);
  return <ol className="runs" aria-label="What it did">
    {runs.length > shown.length && <li className="runs-more"><button type="button" onClick={() => setAll(true)}>{runs.length - shown.length} earlier runs</button></li>}
    {shown.map((run, i) => <li key={run.id} className="run" data-state={run.state}>
      <details open={i === shown.length - 1 || undefined}>
        <summary>
          <time dateTime={run.at}>{whenWords(run.at)}</time><span className="run-line">{runLine(run)}</span></summary>
        <div className="run-body">
          {run.report && <Report compact text={run.report} />}
          {run.cards.length > 0 && <div className="run-cards">{run.cards.map((card, n) => <ThoughtCard key={n} thought={card} />)}</div>}
          {!run.report && run.error && <p className="run-error">{plainError(run.error)}</p>}
          {!!run.traces?.length && <ReplayButton runId={run.id} steps={run.traces} />}
          <details className="run-tech"><summary>Details</summary>
            <dl>
              {run.model && <div><dt>Model</dt><dd>{run.model}</dd></div>}
              <div><dt>Model calls</dt><dd>{run.calls}</dd></div>
              {run.took !== undefined && <div><dt>Took</dt><dd>{run.took < 60 ? `${run.took}s` : `${Math.round(run.took / 60)} min`}</dd></div>}
              {run.steps?.map((s, n) => <div key={n}><dt>{s.title}</dt><dd>{s.status}</dd></div>)}
              {run.report && run.error && <div><dt>Note</dt><dd>{run.error}</dd></div>}
            </dl>
          </details>
        </div>
      </details>
    </li>)}
  </ol>;
}
