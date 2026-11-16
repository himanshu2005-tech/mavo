import { Maximize2, MessageCircle, Minimize2, Send, Volume2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { TelegramStatus } from "../types";
import type { Reminder } from "../types";
import { localTimestamp, parseTime } from "../lib/time";
import { CrewAsk } from "./CrewAsk";
import { TaskAnswer } from "./TaskAnswer";
import { AlertAnswer, actionOf, isAgentQuestion } from "./AlertAnswer";
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
export function DesktopUpdateCard({ reminder, busy, onDismiss, onOpen, onClose, onSpeak, fullScreen = false, onToggleFullScreen, resizing = false, onSetupTelegram }: {
  reminder: Reminder; busy: boolean; onDismiss: () => void; onOpen: () => void; onClose?: () => void; onSpeak: () => void;
  /** Opens Telegram's setup in Mavo: offered on the alert while it is not set up. */ onSetupTelegram?: () => void;
  fullScreen?: boolean; onToggleFullScreen?: () => void; resizing?: boolean;
}) {
  const update = reminder.kind === "crew_update" && !reminder.preview;
  const [asking, setAsking] = useState(false);
  // A one-time task's question is answered right here, to that task; it is not a Crew to ask.
  // A waiting change or a crew agent's question is answered on the alert itself; a one-time
  // task's question goes to its step. An approval once showed the task box ("already answered").
  const answerable = !!actionOf(reminder) || isAgentQuestion(reminder);
  const question = !answerable && !!reminder.task_run_id && !!reminder.task_step_id;
  const fromTask = !!reminder.task_run_id;
  const canAsk = update && !!reminder.crew_id && !fromTask;
  return <div className="toast" data-kind={update ? "update" : "reminder"} data-answerable={answerable || question || undefined} aria-label={`From ${reminder.crew_name}`}>
    <header className="toast-head">
      <span className="toast-dot" aria-hidden="true" />
      <h1 className="toast-name">{reminder.crew_name}</h1>
      <time className="toast-time" dateTime={reminder.scheduled_for}>{reminder.preview ? "Preview" : ago(reminder.scheduled_for)}</time>
      {onToggleFullScreen && <button className="toast-resize" aria-label={fullScreen ? "Restore notification size" : "Full screen"}
        title={fullScreen ? "Restore size (Esc)" : "Full screen"} aria-pressed={fullScreen} disabled={resizing} onClick={onToggleFullScreen}>
        {fullScreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
      </button>}
      {onClose && <button className="toast-x" aria-label="Close" onClick={onClose}><X size={13} /></button>}
    </header>
    <div className="toast-body" role="region" aria-label="Notification contents" tabIndex={0}>
      {update ? <Report compact={!fullScreen} truncate={false} text={reminder.message} /> : <p className="toast-text">{reminder.message}</p>}
      {canAsk && asking && <CrewAsk compact autoFocus crewId={reminder.crew_id} name={reminder.crew_name} />}
      {question && <TaskAnswer runId={reminder.task_run_id!} stepId={reminder.task_step_id!} onAnswered={onDismiss} />}
      {answerable && <AlertAnswer reminder={reminder} onAnswered={onDismiss} />}
    </div>
    <footer className="toast-foot">
      {canAsk && !asking && <button className="toast-chip" onClick={() => setAsking(true)}><MessageCircle size={13} />Ask</button>}
      {!reminder.preview && reminder.crew_id && !fromTask && <button className="toast-chip" disabled={busy} onClick={onOpen}>Open</button>}
      <button className="toast-icon" aria-label="Read aloud" title="Read aloud" onClick={onSpeak}><Volume2 size={14} /></button>
      {onSetupTelegram && !reminder.preview && <TelegramMark onSetup={onSetupTelegram} />}
      {/* Closing a question or a change is never an answer: "Later" only hides it. */}
      {answerable ? onClose && <button className="toast-chip" disabled={busy} onClick={onClose}>Later</button>
        : <button className="toast-done" disabled={busy} onClick={onDismiss}>Done</button>}
    </footer>
  </div>;
}

// Read once in a while, not for every alert: Telegram is rarely changed.
let telegramSeen: { at: number; status: TelegramStatus | null } | null = null;

/** On an alert: that it also went to Telegram, or a quiet button to set Telegram up. */
export function TelegramMark({ onSetup, initial }: { onSetup: () => void; initial?: TelegramStatus | null }) {
  const fresh = telegramSeen && Date.now() - telegramSeen.at < 60_000 ? telegramSeen.status : undefined;
  const [status, setStatus] = useState<TelegramStatus | null | undefined>(initial ?? fresh);
  useEffect(() => {
    if (status !== undefined) return;
    let live = true;
    void api.telegram().then(s => { telegramSeen = { at: Date.now(), status: s }; if (live) setStatus(s); })
      .catch(() => { if (live) setStatus(null); });
    return () => { live = false; };
  }, [status]);
  if (!status) return null;  // unknown (Mavo still starting): say nothing
  if (status.linked && status.alerts) return <span className="toast-tg" title={`Also sent to @${status.bot_username} on Telegram`}><Send size={12} />On Telegram</span>;
  return <button type="button" className="toast-chip toast-tg-setup" title="Get your alerts on Telegram too" onClick={onSetup}><Send size={12} />{status.linked ? "Telegram is off" : "Set up Telegram"}</button>;
}
