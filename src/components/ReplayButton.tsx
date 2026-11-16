import { LoaderCircle, PlayCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";

/** Opens the recording of a run's browser turn in Playwright's trace viewer: each page, click
 *  and screenshot as it happened, to see exactly where it went right or wrong. */
export function ReplayButton({ runId, steps }: { runId: string; steps: string[] }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!steps.length) return null;
  async function open(step: string) {
    setBusy(true); setError(null);
    try { await api.openTrace(runId, step); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the recording."); }
    finally { window.setTimeout(() => setBusy(false), 1500); }
  }
  return <span className="replay">
    {steps.map((step, i) => <button key={step} type="button" className="btn-quiet !py-1" disabled={busy} onClick={() => void open(step)}>
      {busy ? <LoaderCircle size={13} className="animate-spin" /> : <PlayCircle size={13} />}{steps.length > 1 ? `Replay part ${i + 1}` : "Replay what it did"}</button>)}
    {error && <span role="alert" className="text-[12px] text-rose-300">{error}</span>}
  </span>;
}
