import { MessageCircle, Volume2, X } from "lucide-react";
import { useState } from "react";
import type { Reminder } from "../types";
import { localTimestamp, parseTime } from "../lib/time";
import { CrewAsk } from "./CrewAsk";
import { TaskAnswer } from "./TaskAnswer";
import { Report } from "./Report";

/** "now", "4m", "2h", or the time: how long ago, at a glance. The server's times are UTC, often
 *  without a zone; read as local time they were 5½ hours off in India ("5h" on a fresh update). */
export function ago(iso: string, now = Date.now()): string {
  const minutes = Math.floor((now - parseTime(iso).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 12 * 60) return `${Math.floor(minutes / 60)}h`;
  return localTimestamp(iso);
}

/** What a Crew has to tell you, on the desktop: who, when, the news itself, and a way to ask
 *  about it without opening anything. */
export function DesktopUpdateCard({ reminder, busy, onDismiss, onOpen, onClose, onSpeak }: {
  reminder: Reminder; busy: boolean; onDismiss: () => void; onOpen: () => void; onClose?: () => void; onSpeak: () => void;
}) {
  const update = reminder.kind === "crew_update" && !reminder.preview;
  const [asking, setAsking] = useState(false);
  // A one-time task's question is answered right here, to that task; it is not a Crew to ask.
  const question = !!reminder.task_run_id && !!reminder.task_step_id;
  const fromTask = !!reminder.task_run_id;
  const canAsk = update && !!reminder.crew_id && !fromTask;
  return <div className="toast" data-kind={update ? "update" : "reminder"} aria-label={`From ${reminder.crew_name}`}>
    <header className="toast-head">
      <span className="toast-dot" aria-hidden="true" />
      <h1 className="toast-name">{reminder.crew_name}</h1>
      <time className="toast-time" dateTime={reminder.scheduled_for}>{reminder.preview ? "Preview" : ago(reminder.scheduled_for)}</time>
      {onClose && <button className="toast-x" aria-label="Close" onClick={onClose}><X size={13} /></button>}
    </header>
    <div className="toast-body" role="status">
      {update ? <Report compact text={reminder.message} /> : <p className="toast-text">{reminder.message}</p>}
      {canAsk && asking && <CrewAsk compact autoFocus crewId={reminder.crew_id} name={reminder.crew_name} />}
      {question && <TaskAnswer runId={reminder.task_run_id!} stepId={reminder.task_step_id!} onAnswered={onDismiss} />}
    </div>
    <footer className="toast-foot">
      {canAsk && !asking && <button className="toast-chip" onClick={() => setAsking(true)}><MessageCircle size={13} />Ask</button>}
      {!reminder.preview && reminder.crew_id && !fromTask && <button className="toast-chip" disabled={busy} onClick={onOpen}>Open</button>}
      <button className="toast-icon" aria-label="Read aloud" title="Read aloud" onClick={onSpeak}><Volume2 size={14} /></button>
      <button className="toast-done" disabled={busy} onClick={onDismiss}>Done</button>
    </footer>
  </div>;
}
