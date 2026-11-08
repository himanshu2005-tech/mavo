import { ChevronLeft, LoaderCircle, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { clockTime, parseTime } from "../lib/time";
import type { CrewInstance } from "../types";
import { triggerLine } from "./CrewCard";
import { runClock } from "../lib/runClock";
import { limitOf } from "./LimitCard";

const EVENTS = new Set(["gmail_message", "calendar_changed", "tasks_changed"]);

/** When a Crew does its next piece of work, as a person reads it: "in 12 min", "at 14:30",
 *  "tomorrow 09:00", "When mail arrives", or what it is waiting for. */
export function nextRun(crew: CrewInstance, now = Date.now()): { text: string; tone: "ok" | "wait" | "off" } {
  if (crew.status === "paused" || !crew.is_running) return { text: "Stopped", tone: "off" };
  if (crew.runtime_state === "working") return { text: "Working now", tone: "ok" };
  if (crew.runtime_state === "needs_input" || crew.runtime_state === "error" || crew.configuration_plan?.phase === "needs_input") return { text: "Needs you", tone: "wait" };
  // Out of today's AI calls: it carries on by itself tomorrow, nothing for the person to do.
  if (crew.runtime_state === "budget_paused") return { text: "Back tomorrow (today's AI calls used)", tone: "ok" };
  if (EVENTS.has(crew.trigger_type) || crew.trigger_type === "manual" || !crew.next_run_at) return { text: triggerLine(crew), tone: "ok" };
  const at = parseTime(crew.next_run_at).getTime();
  const minutes = Math.round((at - now) / 60000);
  if (minutes <= 0) return { text: "Any moment", tone: "ok" };
  if (minutes < 60) return { text: `in ${minutes} min`, tone: "ok" };
  const day = (t: number) => new Date(t).toDateString();
  if (day(at) === day(now)) return { text: "at " + clockTime(crew.next_run_at), tone: "ok" };
  if (day(at) === day(now + 86_400_000)) return { text: "tomorrow " + clockTime(crew.next_run_at), tone: "ok" };
  return { text: parseTime(crew.next_run_at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }), tone: "ok" };
}

/** Why an agent is waiting for you, in one short line: its question, or what stopped it. */
export function needsReason(crew: Pick<CrewInstance, "runtime_state" | "question" | "last_runtime_error">): string {
  if (crew.runtime_state === "needs_setup") return "Finish setting it up";
  if (crew.runtime_state === "budget_paused") return "Used today's AI calls; it carries on tomorrow";
  const limit = limitOf(crew as CrewInstance);
  if (limit?.kind === "run") return `Needs more AI calls per run to finish (allow ${limit.suggested})`;
  if (limit?.kind === "day") return "Used today's AI calls; it carries on tomorrow";
  const text = (crew.runtime_state === "needs_input" ? crew.question : null) || crew.last_runtime_error || "";
  const line = text.split(/\r?\n/).map(l => l.trim()).find(Boolean) ?? "";
  if (!line) return crew.runtime_state === "needs_input" ? "Has a question" : "Stopped by a problem";
  return line.length <= 90 ? line : line.slice(0, line.lastIndexOf(" ", 87)) + "…";
}

/** The desktop icon's Crew list: every Crew, when it runs next, and a button to run it now. */
export function CrewQuickList({ onBack, onOpen, initial }: { onBack: () => void; onOpen: (crew: CrewInstance) => void; initial?: CrewInstance[] }) {
  const [crews, setCrews] = useState<CrewInstance[] | null>(initial ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, tick] = useState(0);

  useEffect(() => {
    if (initial) return;
    let active = true;
    const load = () => api.crew().then(all => { if (active) setCrews(all.filter(c => c.status !== "draft")); }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Could not load your Crew."); });
    void load();
    const timer = window.setInterval(() => { void load(); tick(n => n + 1); }, 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [initial]);

  async function run(crew: CrewInstance) {
    setBusy(crew.id); setError(null);
    try {
      // A stopped Crew is started (it runs at once); a running one is asked to look now.
      const updated = crew.is_running ? await api.checkCrew(crew.id) : await api.startCrew(crew.id);
      setCrews(list => (list ?? []).map(c => c.id === updated.id ? updated : c));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not run it."); }
    finally { setBusy(null); }
  }

  // Working and waiting first, then the rest; each group by name.
  const order = (c: CrewInstance) => c.runtime_state === "working" ? 0 : nextRun(c).tone === "wait" ? 1 : nextRun(c).tone === "off" ? 3 : 2;
  const sorted = crews && [...crews].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
  return <section className="surface orb-menu dmenu dlist" aria-label="Your agents">
    <header className="dmenu-head">
      <button className="dmenu-back" aria-label="Back" onClick={onBack}><ChevronLeft size={15} /></button>
      <span className="dmenu-title">Agents</span>
      {crews && <span className="dmenu-status">{crews.length}</span>}
    </header>
    {sorted === null ? <p className="dlist-empty">Loading…</p>
      : sorted.length === 0 ? <p className="dlist-empty">No agents yet. Ask Mavo for one.</p>
      : <ul className="dlist-rows">{sorted.map(crew => {
          const next = nextRun(crew), clock = runClock(crew);
          const state = crew.runtime_state === "working" ? "working" : next.tone === "wait" ? "needs" : next.tone === "off" ? "stopped" : "running";
          return <li key={crew.id} className="dlist-row" data-state={state}>
            <button className="dlist-open" onClick={() => onOpen(crew)} title={`Open ${crew.name} in Mavo`}>
              <span className="dlist-line"><i className="dlist-dot" /><b>{crew.name}</b></span>
              <span className="dlist-next" data-tone={next.tone} title={next.tone === "wait" ? needsReason(crew) : undefined}>{next.tone === "wait" ? needsReason(crew) : next.text}{clock ? ` · ${clock.every}` : ""}</span>
              {clock && <span className="dlist-bar" aria-hidden="true"><i style={{ width: `${clock.progress * 100}%` }} /></span>}
            </button>
            <button className="dlist-run" aria-label={`Run ${crew.name} now`} title="Run now" disabled={busy !== null || crew.runtime_state === "working"} onClick={() => void run(crew)}>
              {busy === crew.id ? <LoaderCircle size={13} className="animate-spin" /> : <Play size={11} />}
            </button>
          </li>;
        })}</ul>}
    {error && <p role="alert" className="toast-error">{error}</p>}
  </section>;
}
