import { LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ActionRequest, RunApproval } from "../types";
import { titles as actionTitles } from "./GoogleActionsPanel";

/** A change waiting for the person's OK, as it is answered: a held command or script (run by its
 *  fingerprint, so only what was shown can run) or another change (approved as it is). */
export interface Waiting { id: string; title: string; detail?: string; reason?: string | null; run?: RunApproval }

const RUNS = new Set(["terminal.run", "project.run"]);

export function waitingOf(runs: RunApproval[], actions: ActionRequest[], keep: (crewId: string | null, id: string) => boolean): Waiting[] {
  const held = runs.filter(r => keep(r.crew_id, r.id)).map(r => ({
    id: r.id, run: r, reason: r.reason ?? r.error ?? null,
    title: r.kind === "command" ? "Run a command" : `Run ${r.script || r.file || "a script"}${r.project ? ` in ${r.project}` : ""}`,
    detail: r.kind === "command" ? r.command : undefined,
  }));
  const ids = new Set(held.map(h => h.id));
  const others = actions.filter(a => a.status === "pending" && !ids.has(a.id) && !RUNS.has(a.operation) && keep(a.crew_id, a.id)).map(a => ({
    id: a.id, title: actionTitles[a.operation] ?? a.operation.replace(".", " "), detail: a.target_summary ?? undefined,
  }));
  return [...held, ...others];
}

/** The changes waiting for the person, loaded fresh; `keep` picks which. */
function useWaiting(keep: (crewId: string | null, id: string) => boolean, key: string) {
  const [items, setItems] = useState<Waiting[] | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(() => {
    // Not loading is not "nothing waits": an alert said "Already answered" when Mavo was busy.
    Promise.all([api.runApprovals(), api.actions()])
      .then(([runs, actions]) => { setFailed(false); setItems(waitingOf(runs, actions, keep)); })
      .catch(() => setFailed(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => { load(); }, [load]);
  return { items, load, failed };
}

/** One waiting change with its two buttons. */
export function WaitingRow({ item, onDone }: { item: Waiting; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canRun = !item.run || (!!item.run.sha256 && !item.run.changed && !item.run.error);
  async function answer(yes: boolean) {
    setBusy(true); setError(null);
    try {
      if (!yes) await api.rejectAction(item.id);
      else if (item.run) await api.approveRun(item.id, item.run.sha256 ?? "", false);
      else await api.approveChange(item.id);
      onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not send your answer."); setBusy(false); }
  }
  return <div className="waiting-row">
    <p className="waiting-title">{item.title}</p>
    {item.detail && <pre className="waiting-detail">{item.detail}</pre>}
    {item.reason && <p className="waiting-reason">{item.reason}</p>}
    {error && <p role="alert" className="waiting-error">{error}</p>}
    <div className="waiting-buttons">
      <button type="button" className="btn-primary !py-1.5" disabled={busy || !canRun} onClick={() => void answer(true)}>{busy && <LoaderCircle size={12} className="animate-spin" />}{item.run ? "Run" : "Approve"}</button>
      <button type="button" className="btn-secondary !py-1.5" disabled={busy} onClick={() => void answer(false)}>Deny</button>
    </div>
  </div>;
}

/** In the agent's window: everything it waits on you for, each answered right there. */
export function WaitingChanges({ crewId, onAnswered }: { crewId: string; onAnswered?: () => void }) {
  const { items, load } = useWaiting(crewId_ => crewId_ === crewId, crewId);
  if (!items?.length) return null;
  return <div className="waiting" aria-label="Waiting for your OK">
    {items.map(item => <WaitingRow key={item.id} item={item} onDone={() => { load(); onAnswered?.(); }} />)}
  </div>;
}

/** On a desktop alert: the one change it is about. */
export function AlertChange({ actionId, onAnswered }: { actionId: string; onAnswered: () => void }) {
  const { items, load, failed } = useWaiting((_, id) => id === actionId, actionId);
  if (failed && items === null) return <p className="toast-note">Could not load it. <button type="button" className="toast-chip" onClick={load}>Try again</button></p>;
  if (items === null) return <p className="toast-note">Loading…</p>;
  if (!items.length) return <p className="toast-note">Already answered.</p>;
  return <WaitingRow item={items[0]} onDone={onAnswered} />;
}
