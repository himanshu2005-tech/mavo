import { ArrowUp } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import type { TaskStepView } from "../types";

/** A task's question, answered from the desktop: its buttons when it offers a choice, otherwise a
 *  short answer box. The answer goes to that task step, never to a Crew. */
export function TaskAnswer({ runId, stepId, onAnswered }: { runId: string; stepId: string; onAnswered: () => void }) {
  const [step, setStep] = useState<TaskStepView | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    let active = true;
    api.task(runId).then(run => {
      if (!active) return;
      const found = run.steps.find(s => s.id === stepId) ?? null;
      setStep(found);
      setGone(run.status !== "running" || !found?.ask);
    }).catch(() => active && setGone(true));
    return () => { active = false; };
  }, [runId, stepId]);

  async function send(answer: string) {
    if (!answer.trim() || busy) return;
    setBusy(true); setError(null);
    try { await api.answerTask(runId, answer.trim(), stepId); onAnswered(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not send your answer."); setBusy(false); }
  }

  if (gone) return <p className="toast-note">Already answered, or the task has ended.</p>;
  if (!step) return null;
  return <div className="ask ask-compact">
    {error && <p role="alert" className="ask-error">{error}</p>}
    {step.choices?.length ? <div className="toast-choices" role="group" aria-label="Your call">{step.choices.map((choice, i) =>
      <button key={choice} className={i === 0 ? "toast-done" : "toast-chip"} disabled={busy} onClick={() => void send(choice)}>{choice}</button>)}</div> : null}
    {/* Always a box too: "yes continue", or "fix only the timer bug". */}
    <form className="ask-box" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(text); }}>
      <input aria-label="Your answer" placeholder={step.choices?.length ? "Or in your words…" : "Your answer"} value={text} maxLength={2000} autoFocus={!step.choices?.length} onChange={e => setText(e.target.value)} />
      <button type="submit" aria-label="Send" disabled={busy || !text.trim()}><ArrowUp size={15} /></button>
    </form>
  </div>;
}
