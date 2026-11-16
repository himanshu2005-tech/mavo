import { ChevronRight, Clock3, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { CrewCycle, Thought } from "../types";
import { ThoughtCard, isCard } from "./ThoughtCard";

export interface LiveRow { key: string; text: string; state: "running" | "done" | "waiting" | "failed"; detail?: Thought }

/** What a run did and is doing, as rows the person reads: each action in its own words (its
 *  run_text), how it ended, and what it found (a page it read) to open. Lines a step says about
 *  itself ("Read 14 emails") are rows too; only the last can still be running. */
export function liveRows(thoughts: Thought[] = [], finished = false): LiveRow[] {
  const rows: LiveRow[] = [];
  const byAction = new Map<string, LiveRow>();
  for (const [n, t] of thoughts.entries()) {
    if (t.tool === "action" && t.aid) {
      const state = (["running", "done", "waiting", "failed"].includes(t.outcome ?? "") ? t.outcome : "done") as LiveRow["state"];
      const row = byAction.get(t.aid);
      if (row) { row.state = state; row.text = t.text || row.text; }
      else { const fresh = { key: t.aid, text: t.text, state }; byAction.set(t.aid, fresh); rows.push(fresh); }
    } else if (isCard(t)) {
      const owner = t.aid ? byAction.get(t.aid) : undefined;
      if (owner) owner.detail = t;
      else rows.push({ key: `c${n}`, text: t.label || t.about || t.text, state: "done", detail: t });
    } else if (!t.label && t.text?.trim()) {
      if (rows.at(-1)?.text !== t.text) rows.push({ key: `l${n}`, text: t.text, state: "done" });
    }
  }
  // A plain line is done once anything comes after it; the last is happening now.
  const last = rows.at(-1);
  if (!finished && last && last.key.startsWith("l")) last.state = "running";
  return rows;
}

function Mark({ state }: { state: LiveRow["state"] }) {
  if (state === "running") return <span className="la-mark la-spin" aria-label="Working" />;
  if (state === "waiting") return <span className="la-mark la-wait" aria-label="Waiting for your OK"><Clock3 size={11} /></span>;
  if (state === "failed") return <span className="la-mark la-fail" aria-label="Not done"><X size={11} strokeWidth={3} /></span>;
  return <span className="la-mark la-done" aria-label="Done"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 8.5l2.6 2.6L12 5.6" /></svg></span>;
}

function seconds(from: string): number {
  const at = Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(from) ? from : from + "Z");
  return Math.max(0, Math.round((Date.now() - at) / 1000));
}

/** A run, live: its steps along the top with a progress bar and a clock, then each action as it
 *  happens, ticked off as it finishes. A row with something to show opens on a click. */
export function LiveActions({ cycle }: { cycle: CrewCycle }) {
  const [open, setOpen] = useState<string | null>(null);
  const [now, setNow] = useState(() => seconds(cycle.started_at));
  useEffect(() => { const t = window.setInterval(() => setNow(seconds(cycle.started_at)), 1000); return () => window.clearInterval(t); }, [cycle.started_at]);
  const rows = liveRows(cycle.thoughts);
  const total = Math.max(1, cycle.total), done = cycle.done;
  const current = cycle.steps.find(s => s.status === "running" || s.status === "asking");
  return <div className="live-actions" aria-live="polite">
    <div className="la-top">
      <span className="la-now">{current?.title ?? "Starting"}</span>
      <span className="la-clock">{now < 60 ? `${now}s` : `${Math.floor(now / 60)}m ${now % 60}s`}</span>
    </div>
    <div className="la-bar" aria-hidden="true"><i style={{ width: `${Math.round(((done + (current ? .5 : 0)) / total) * 100)}%` }} /></div>
    {cycle.steps.length > 1 && <ol className="la-steps" aria-label="Steps">{cycle.steps.map((s, i) =>
      <li key={i} data-status={s.status}><span>{i + 1}</span>{s.title}</li>)}</ol>}
    <ol className="la-rows">
      {rows.map(row => <li key={row.key} data-state={row.state} data-open={open === row.key || undefined}>
        <button type="button" className="la-row" disabled={!row.detail} aria-expanded={row.detail ? open === row.key : undefined} onClick={() => setOpen(o => o === row.key ? null : row.key)}>
          <Mark state={row.state} /><span className="la-text">{row.text}</span>
          {row.detail && <ChevronRight size={13} className="la-more" aria-hidden="true" />}
        </button>
        {row.detail && open === row.key && <div className="la-detail"><ThoughtCard thought={row.detail} /></div>}
      </li>)}
      {!rows.length && <li data-state="running"><span className="la-row"><Mark state="running" /><span className="la-text">Getting started</span></span></li>}
    </ol>
  </div>;
}
