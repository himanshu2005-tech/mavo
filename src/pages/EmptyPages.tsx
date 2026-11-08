import { ChevronRight, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { parseTime } from "../lib/time";
import { useAppStore } from "../store/app";
import type { ActionRequest, CrewRun, Reminder } from "../types";

/** One thing that happened: an agent did something, worked, or said something. */
export interface Happening {
  id: string; agentId: string | null; agent: string; what: string; target?: string;
  outcome: "done" | "failed" | "waiting" | "said"; reason?: string; detail?: string; at: string;
}

const LABELS: Record<string, string> = {
  "terminal.run": "Ran a command", "browser.open": "Opened a page", "browser.search": "Searched the web", "browser.navigate": "Went to a page",
  "browser.read": "Read a page", "browser.click": "Clicked on a page", "browser.fill": "Filled in a field", "browser.select": "Chose an option",
  "browser.press": "Pressed a key on a page", "browser.scroll": "Scrolled a page", "browser.wait": "Waited for a page", "browser.close": "Closed the browser",
  "browser.double_click": "Double-clicked on a page", "browser.hover": "Pointed at something on a page", "browser.drag": "Dragged on a page",
  "project.create": "Made a project folder", "project.write": "Wrote a project file", "project.read": "Read project files", "project.run": "Ran a project script",
  "windows.list": "Looked at open windows", "windows.focus": "Switched to a window", "windows.minimize": "Minimised a window", "windows.type": "Typed into an app",
  "windows.key": "Pressed a key in an app", "windows.sleep": "Put the PC to sleep", "windows.shutdown": "Shut down the PC", "windows.lock": "Locked the PC",
  "windows.volume": "Set the volume", "windows.media": "Controlled the music", "windows.speak": "Spoke aloud",
  "notes.find": "Searched notes", "notes.read": "Read a note", "notes.write": "Wrote a note", "files.list": "Looked in a folder", "files.move": "Moved a file", "files.backup": "Backed up a folder",
  "gmail.send": "Sent an email", "gmail.draft": "Drafted an email", "gmail.archive": "Archived an email", "gmail.mark_read": "Marked an email read", "gmail.trash": "Moved an email to trash",
  "calendar.create": "Added an event", "calendar.update": "Changed an event", "calendar.delete": "Deleted an event",
  "tasks.create": "Added a task", "tasks.update": "Changed a task", "tasks.complete": "Completed a task", "tasks.delete": "Deleted a task",
  "github.create_issue": "Opened a GitHub issue", "github.comment": "Commented on GitHub", "helper.ask": "Asked a helper agent",
};

/** What an action was about, in a few words: the site, file, person or command. */
function targetOf(a: ActionRequest): string | undefined {
  if (a.target_summary) return a.target_summary;
  const g = a.arguments ?? {};
  const url = typeof g.url === "string" ? g.url : undefined;
  if (url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } }
  for (const key of ["query", "to", "path", "folder", "source", "title", "summary", "project", "command"]) if (typeof g[key] === "string" && g[key]) return String(g[key]).slice(0, 80);
  return undefined;
}

function resultText(a: ActionRequest): string | undefined {
  const r = a.result;
  if (!r) return undefined;
  const text = r.stdout ?? r.text ?? r.title ?? r.url;
  return typeof text === "string" ? text.slice(0, 1200) : JSON.stringify(r, null, 2).slice(0, 1200);
}

const firstLine = (text?: string | null) => (text ?? "").split(/\r?\n/).map(l => l.trim()).find(Boolean) ?? "";

/** Everything that happened, newest first, in one shape. */
export function happenings(actions: ActionRequest[], runs: CrewRun[], reminders: Reminder[]): Happening[] {
  const fromActions: Happening[] = actions.map(a => ({
    id: "a" + a.id, agentId: a.crew_id, agent: a.crew_name ?? "You", what: LABELS[a.operation] ?? a.operation, target: targetOf(a),
    outcome: a.status === "completed" ? "done" : a.status === "pending" || a.status === "executing" ? "waiting" : "failed",
    reason: a.status === "completed" ? undefined : a.error ?? (a.status === "unknown" ? "Mavo could not tell whether it finished" : undefined),
    detail: a.status === "completed" ? resultText(a) : a.error ?? undefined, at: a.created_at,
  }));
  const fromRuns: Happening[] = runs.map(r => ({
    id: "r" + r.id, agentId: r.crew_id, agent: r.crew_name, what: r.status === "running" ? "Is working" : r.status === "completed" ? "Did its work" : "Could not finish its work",
    outcome: r.status === "completed" ? "done" : r.status === "running" ? "waiting" : "failed",
    reason: r.status === "completed" || r.status === "running" ? undefined : firstLine(r.error) || (r.status === "interrupted" ? "Stopped before it finished" : r.status === "cancelled" ? "Cancelled" : undefined),
    detail: r.result ?? r.error ?? undefined, at: r.created_at,
  }));
  const fromReminders: Happening[] = reminders.map(m => ({
    id: "m" + m.id, agentId: m.crew_id, agent: m.crew_name, what: firstLine(m.message).slice(0, 140) || "Sent an update",
    outcome: "said", detail: m.message, at: m.scheduled_for,
  }));
  return [...fromActions, ...fromRuns, ...fromReminders].sort((a, b) => parseTime(b.at).getTime() - parseTime(a.at).getTime());
}

/** What went wrong lately, one entry per agent: how many times, the latest reason, when. An
 *  agent whose latest try worked again is left out: it is fine now. */
export function troubles(list: Happening[], now = Date.now(), days = 7) {
  const since = now - days * 86_400_000;
  const byAgent = new Map<string, { agentId: string | null; agent: string; count: number; reason: string; at: string; fixed: boolean }>();
  for (const h of list) { // newest first
    if (parseTime(h.at).getTime() < since || h.outcome === "said" || h.outcome === "waiting") continue;
    const key = h.agentId ?? h.agent;
    const had = byAgent.get(key);
    if (!had) byAgent.set(key, { agentId: h.agentId, agent: h.agent, count: h.outcome === "failed" ? 1 : 0, reason: h.reason ?? "", at: h.at, fixed: h.outcome === "done" });
    else if (h.outcome === "failed" && !had.fixed) { had.count++; if (!had.reason) had.reason = h.reason ?? ""; }
  }
  return [...byAgent.values()].filter(t => !t.fixed && t.count > 0);
}

function dayOf(at: string): string {
  const d = parseTime(at), today = new Date();
  const same = (x: Date, y: Date) => x.toDateString() === y.toDateString();
  if (same(d, today)) return "Today";
  if (same(d, new Date(today.getTime() - 86_400_000))) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}
const clock = (at: string) => parseTime(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** What happened: first what went wrong and where (one line per agent, with why, and a way to
 *  it); then everything, one quiet line each, newest first, by day. Raw output only on a click. */
export function ActivityView({ initial }: { initial?: Happening[] } = {}) {
  const [list, setList] = useState<Happening[] | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  async function load() {
    setLoading(true); setError(null);
    try {
      const [actions, runs, reminders] = await Promise.all([api.actions().catch(() => []), api.crewRuns().catch(() => []), api.reminderHistory().catch(() => [])]);
      setList(happenings(actions, runs, reminders));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load what happened."); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (!initial) void load(); }, [initial]);
  const problems = useMemo(() => troubles(list ?? []), [list]);
  const shown = (list ?? []).filter(h => !onlyProblems || h.outcome === "failed").slice(0, 200);
  const days = shown.reduce<[string, Happening[]][]>((out, h) => {
    const day = dayOf(h.at);
    if (out.length && out[out.length - 1][0] === day) out[out.length - 1][1].push(h); else out.push([day, [h]]);
    return out;
  }, []);
  // Only your own agents can be opened; one-time helpers are not on the map.
  const yours = new Set(useAppStore(s => s.crew).map(c => c.id));
  const canOpen = (id: string | null): id is string => !!id && yours.has(id);
  const openAgent = (id: string | null) => { if (canOpen(id)) useAppStore.getState().openAgent(id); };

  return <div className="happened">
    {problems.length > 0 && <section className="happened-attention" aria-label="Needs attention">
      <h3>Needs attention</h3>
      {problems.map(t => <button key={t.agentId ?? t.agent} className="trouble" onClick={() => openAgent(t.agentId)} disabled={!canOpen(t.agentId)}>
        <CircleAlert size={16} aria-hidden="true" />
        <span className="trouble-text"><b>{t.agent}</b><span>{t.count > 1 ? `${t.count} tries failed · ` : ""}{t.reason || "Something did not work"}</span></span>
        <time>{dayOf(t.at) === "Today" ? clock(t.at) : dayOf(t.at)}</time>
        {canOpen(t.agentId) && <ChevronRight size={15} className="trouble-go" aria-hidden="true" />}
      </button>)}
    </section>}

    <div className="happened-bar">
      <div className="dmenu-seg" role="group" aria-label="Show">
        <button aria-pressed={!onlyProblems} onClick={() => setOnlyProblems(false)}>Everything</button>
        <button aria-pressed={onlyProblems} onClick={() => setOnlyProblems(true)}>Only problems</button>
      </div>
      <button className="btn-quiet" disabled={loading} onClick={() => void load()}><RefreshCw size={13} className={loading ? "animate-spin" : ""} />Refresh</button>
    </div>
    {error && <p role="alert" className="notice error-notice">{error}</p>}
    {list === null && !error && <p role="status" className="happened-empty"><LoaderCircle size={14} className="animate-spin" />Loading…</p>}
    {list !== null && !shown.length && <p className="happened-empty">{onlyProblems ? "No problems. Everything worked." : "Nothing yet. What your agents do will show up here."}</p>}

    {days.map(([day, items]) => <section key={day} className="happened-day" aria-label={day}>
      <h3>{day}</h3>
      <ul>{items.map(h => <li key={h.id} data-outcome={h.outcome}>
        <button className="happening" aria-expanded={open === h.id} onClick={() => setOpen(open === h.id ? null : h.id)}>
          <time>{clock(h.at)}</time>
          <i className="happening-dot" aria-hidden="true" />
          <span className="happening-text"><b>{h.agent}</b><span>{h.what}{h.target ? ` · ${h.target}` : ""}</span></span>
          {h.outcome === "failed" && <span className="happening-tag">Failed</span>}
          {h.outcome === "waiting" && <span className="happening-tag" data-tone="wait">Waiting</span>}
        </button>
        {open === h.id && <div className="happening-more">
          {h.reason && <p className="happening-reason">{h.reason}</p>}
          {h.detail && h.detail !== h.reason && <pre>{h.detail}</pre>}
          {!h.reason && !h.detail && <p>No more detail was kept.</p>}
          {canOpen(h.agentId) && <button className="dmenu-pill" onClick={() => openAgent(h.agentId)}>Open {h.agent}</button>}
        </div>}
      </li>)}</ul>
    </section>)}
  </div>;
}
