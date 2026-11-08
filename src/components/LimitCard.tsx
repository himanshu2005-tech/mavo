import { Gauge, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CallLimit, CrewInstance } from "../types";

/** The AI-call limit that stopped an agent, if one did: from the agent itself, or read from the
 *  older wording ("session model-call budget", "daily model-call budget") so agents stuck before
 *  this change get the same plain card and one-click fix. */
export function limitOf(crew: Pick<CrewInstance, "limit" | "question" | "last_runtime_error" | "max_model_calls" | "daily_call_limit">): CallLimit | null {
  if (crew.limit) return crew.limit;
  const text = `${crew.question ?? ""} ${crew.last_runtime_error ?? ""}`;
  if (/session model-call budget/i.test(text)) {
    const current = Math.max(crew.max_model_calls ?? 1, 1);
    return { kind: "run", current, suggested: Math.min(Math.max(current + 2, 4), 50) };
  }
  if (/daily model-call budget/i.test(text)) {
    const current = crew.daily_call_limit ?? 0;
    return { kind: "day", current, suggested: Math.min(Math.max(current * 2, current + 4), 100) };
  }
  return null;
}

/** What stopped it, in plain words, and the one change that lets it go on. */
export function LimitCard({ crew }: { crew: CrewInstance }) {
  const limit = limitOf(crew)!;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = limit.kind === "run";
  async function raise() {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.updateCrew(crew.id, run ? { max_model_calls: limit.suggested } : { daily_call_limit: limit.suggested })); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change it."); }
    finally { setBusy(false); }
  }
  return <section className="limit-card" aria-label="AI call limit">
    <div className="limit-head"><Gauge size={15} aria-hidden="true" /><b>{run ? "Stopped before it could finish" : "Used today's AI calls"}</b></div>
    <p>{run
      ? `${crew.name} may think through only ${limit.current} step${limit.current === 1 ? "" : "s"} each time it runs, and this job needed one more. Each step is one AI call.`
      : `${crew.name} used all ${limit.current} AI calls it may make today. It carries on by itself tomorrow.`}</p>
    <div className="limit-actions">
      <button className="btn-primary !py-1.5" disabled={busy} onClick={() => void raise()}>
        {busy && <LoaderCircle size={13} className="animate-spin" />}{run ? `Allow ${limit.suggested} per run` : `Raise to ${limit.suggested} a day`}
      </button>
      <span className="limit-note">{run ? "It tries again right away." : "Or leave it: nothing is lost."}</span>
    </div>
    {error && <p role="alert" className="access-hint">{error}</p>}
  </section>;
}
