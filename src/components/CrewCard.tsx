import { LoaderCircle, Play, Plus, Square } from "lucide-react";
import { workerLabel } from "../lib/crewStatus";
import { clockTime } from "../lib/time";
import type { CrewInstance } from "../types";
import { CrewArt, artStyle } from "./CrewArt";
import { identityColor, identityStyle } from "../lib/identityColors";

const clock = (value: string) => { const [h, m] = value.split(":").map(Number); return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "pm" : "am"}`; };
const every = (minutes: number) => minutes % 60 === 0 ? (minutes === 60 ? "Every hour" : `Every ${minutes / 60} hours`) : `Every ${minutes} min`;

/** One line that says when a Crew acts. */
/** When the job ends, if it does: "until a guest is booked, or 30 Nov"; "Done" once it has. */
export function endLine(plan?: { run_until?: string | null; goal?: string | null; finished?: unknown; draft?: { run_until?: string | null; goal?: string | null } } | null): string {
  if (!plan) return "";
  if (plan.finished) return "Done";
  const until = plan.run_until ?? plan.draft?.run_until;
  const goal = plan.goal ?? plan.draft?.goal;
  const day = until ? new Date(until + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
  if (goal && day) return `until ${goal}, or ${day}`;
  if (goal) return `until ${goal}`;
  return day ? `until ${day}` : "";
}

export function triggerLine(crew: CrewInstance): string {
  switch (crew.trigger_type) {
    case "daily": {
      const times = crew.daily_triggers?.length ? crew.daily_triggers.filter(t => t.enabled).map(t => t.time) : crew.schedule_time ? [crew.schedule_time] : [];
      return times.length ? "Daily at " + times.map(clock).join(", ") : "Daily";
    }
    case "interval": return crew.schedule_time ? "Every day at " + clock(crew.schedule_time) : every(crew.review_interval_minutes);
    case "gmail_message": return "When mail arrives";
    case "calendar_changed": return "When your calendar changes";
    case "tasks_changed": return "When your tasks change";
    default: return "When you ask";
  }
}

function tone(crew: CrewInstance): "run" | "work" | "warn" | "off" {
  if (crew.status === "draft") return "off";
  if (crew.last_runtime_error || crew.runtime_state === "error" || crew.runtime_state === "needs_input" || crew.configuration_plan?.phase === "needs_access") return "warn";
  if (!crew.is_running) return "off";
  return crew.runtime_state === "working" ? "work" : "run";
}

export function CrewCard({ crew, busy, onOpen, onToggle }: { crew: CrewInstance; busy: boolean; onOpen: () => void; onToggle: () => void }) {
  const state = tone(crew);
  return <article className="crew-card" style={{...artStyle(crew.template_id),...identityStyle(identityColor(`crew:${crew.id}`))}} data-running={crew.is_running} data-tone={state}>
    <button className="crew-card-hit" aria-label={`Open ${crew.name}`} onClick={onOpen} />
    <div className="crew-art">
      <CrewArt template={crew.template_id} />
      <span className="crew-chip"><i />{workerLabel(crew)}</span>
    </div>
    <div className="crew-body">
      <h3>{crew.name}</h3>
      <p>{[triggerLine(crew), endLine(crew.configuration_plan)].filter(Boolean).join(" · ")}</p>
      <div className="crew-foot">
        <span>{crew.is_running && crew.next_run_at && crew.trigger_type !== "daily" ? "Next " + clockTime(crew.next_run_at) : crew.last_runtime_error ? "Needs attention" : " "}</span>
        <button className="crew-toggle" data-on={crew.is_running} aria-label={`${crew.is_running ? "Stop" : "Run"} ${crew.name}`} disabled={busy} onClick={onToggle}>
          {busy ? <LoaderCircle size={13} className="animate-spin" /> : crew.is_running ? <Square size={11} fill="currentColor" /> : <Play size={11} fill="currentColor" />}
          {crew.is_running ? "Stop" : "Run"}
        </button>
      </div>
    </div>
  </article>;
}

export function DraftCard({ crew, onContinue, onDiscard }: { crew: CrewInstance; onContinue: () => void; onDiscard?: () => void }) {
  return <article className="crew-card is-draft" style={{...artStyle(crew.template_id),...identityStyle(identityColor(`crew:${crew.id}`))}} data-running="false" data-tone="off">
    <button className="crew-card-hit" aria-label={`Continue ${crew.name}`} onClick={onContinue} />
    <div className="crew-art"><CrewArt template={crew.template_id} /><span className="crew-chip"><i />Not added yet</span></div>
    <div className="crew-body"><h3>{crew.name}</h3><p>Finish setup in chat</p>
      <div className="crew-foot">{onDiscard ? <button type="button" className="crew-discard" onClick={onDiscard}>Discard</button> : <span>&nbsp;</span>}<span className="crew-toggle" data-on="false">Continue</span></div></div>
  </article>;
}

export function NewCrewCard({ onClick }: { onClick: () => void }) {
  return <button className="crew-card crew-new" onClick={onClick}><span className="new-orb"><Plus size={26} strokeWidth={1.6} /></span><span className="new-title">New Crew</span><span className="new-note">Tell Mavo what you need</span></button>;
}
