import { ArrowUp } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "../lib/api";
import type { Reminder } from "../types";
import { AlertChange } from "./WaitingChanges";

/** The change an alert waits on ("action:<id>"), or null. */
export const actionOf = (reminder: Reminder) =>
  reminder.kind === "approval" && reminder.task_step_id?.startsWith("action:") ? reminder.task_step_id.slice(7) : null;

/** Whether this alert is a crew agent's question, answered here. */
export const isAgentQuestion = (reminder: Reminder) => reminder.kind === "question";

/** Whether an alert waits for the person's answer (it is never replaced while they read it). */
export const isAnswerable = (reminder: Pick<Reminder, "kind" | "task_step_id">) =>
  reminder.kind === "question" || (reminder.kind === "approval" && !!reminder.task_step_id?.startsWith("action:"));

/** Answering an alert on the desktop: Run, Approve or Deny a waiting change, or answer an agent's
 *  question with one of its choices or in the person's own words. Closing is never an answer. */
export function AlertAnswer({ reminder, onAnswered }: { reminder: Reminder; onAnswered: () => void }) {
  const action = actionOf(reminder);
  // A held command or script runs by its fingerprint, shown on the alert; another change is
  // approved as it is. Both answered here, never only "Open Actions".
  if (action) return <AlertChange actionId={action} onAnswered={onAnswered} />;
  if (!isAgentQuestion(reminder)) return null;
  return <QuestionAnswer id={reminder.id} choices={reminder.choices ?? []} compact onAnswered={onAnswered} />;
}

/** An agent's question answered: one of its choices, or the person's own words. The same on its
 *  desktop alert and in the agent's window; answered in one, it is done in the other. */
export function QuestionAnswer({ id, choices, compact = false, onAnswered }: { id: string; choices: string[]; compact?: boolean; onAnswered: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(answer: string) {
    if (busy || !answer.trim()) return;
    setBusy(true); setError(null);
    try { await api.answerReminder(id, answer.trim()); onAnswered(); }
    catch (e) {
      // Answered a moment ago somewhere else (its alert, or the agent's window): that is done.
      if (e instanceof ApiError && (e.status === 409 || e.status === 404)) { onAnswered(); return; }
      setError(e instanceof Error ? e.message : "Could not send your answer.");
      setBusy(false);
    }
  }
  return <div className={"ask" + (compact ? " ask-compact" : "")}>
    {error && <p role="alert" className="ask-error">{error}</p>}
    {choices.length > 0 && <div className="toast-choices" role="group" aria-label="Your call">{choices.map((choice, i) =>
      <button key={choice} type="button" className={i === 0 ? "toast-done" : "toast-chip"} disabled={busy} onClick={() => void send(choice)}>{choice}</button>)}</div>}
    <form className="ask-box" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(text); }}>
      <input aria-label="Your answer" placeholder={choices.length ? "Or in your words…" : "Your answer"} value={text} maxLength={1000} disabled={busy} onChange={e => setText(e.target.value)} />
      <button type="submit" aria-label="Send" disabled={busy || !text.trim()}><ArrowUp size={15} /></button>
    </form>
  </div>;
}
