import { Check } from "lucide-react";
import type { CrewInstance, Thought } from "../types";
import { TaskAnswer } from "./TaskAnswer";
import { ago } from "./DesktopUpdateCard";
import { ThoughtCard, isCard } from "./ThoughtCard";
import { WorkflowFlow, crewActions, liveNodes, type FlowStep } from "./WorkflowFlow";

const SHOWN = 6;  // the latest steps in view; earlier ones are only counted

/** What an agent is doing right now, simply: one line and a bar, then what it has done so far,
 *  in view (a reply sent, a booking, a page read). The guarded agent's path, step by step, folds
 *  away. When a step waits on you, the answer is right here. Nothing between runs. */
export function LiveCycle({ crew }: { crew: CrewInstance }) {
  const cycle = crew.cycle;
  if (!cycle) return null;
  const asking = !!cycle.question;
  const recipe = (crew.configuration_plan as { recipe?: { steps?: FlowStep[]; final?: string } } | undefined)?.recipe;
  const desk = recipe?.steps?.some(s => s.automation === "agent.desk") ? recipe : null;
  const live = desk ? liveNodes(desk.steps!, cycle.thoughts ?? []) : null;
  const thoughts = cycle.thoughts ?? [];
  const recent = thoughts.slice(-SHOWN);
  return <section className="live-cycle" data-asking={asking} aria-label="What it is doing now">
    <p className="live-line"><b>{asking ? "Needs your answer" : "Working now"}</b><span>started {ago(cycle.started_at)} ago</span></p>
    <div className="cycle-bar wide" aria-hidden="true"><i style={{ width: `${Math.round(cycle.done / Math.max(1, cycle.total) * 100)}%` }} /></div>
    {/* A run of several steps lists them; the guarded agent's own path folds away below. */}
    {!desk && cycle.steps.length > 1 && <ol className="live-steps">{cycle.steps.map((step, i) =>
      <li key={i} data-status={step.status}>
        <span className="live-step-mark" aria-hidden="true">{step.status === "done" ? <Check size={11} /> : i + 1}</span>
        <span className="live-step-title">{step.title}</span>
        <span className="live-step-word">{step.status === "done" ? "Done" : step.status === "running" ? "Working" : step.status === "asking" ? "Needs you" : step.status === "skipped" ? "Skipped" : step.status === "failed" ? "Stopped" : ""}</span>
      </li>)}</ol>}
    {recent.length > 0 && <ol className="live-feed" aria-label="What it has done">
      {thoughts.length > SHOWN && <li className="live-more">{thoughts.length - SHOWN} earlier steps</li>}
      {recent.map((t: Thought, i) => isCard(t)
        ? <li key={i}><ThoughtCard thought={t} /></li>
        : <li key={i} className="live-note">{t.label && <b>{t.label} </b>}{t.text}</li>)}
    </ol>}
    {desk && live && <details className="live-how"><summary>How it works</summary>
      <WorkflowFlow steps={desk.steps!} final={desk.final} actions={crewActions(crew.configuration_plan)} active={live.active} done={live.done} refused={live.refused} />
    </details>}
    {asking && <div className="live-ask">
      <p>{cycle.question}</p>
      {cycle.question_step && <TaskAnswer runId={cycle.run_id} stepId={cycle.question_step} onAnswered={() => undefined} />}
    </div>}
  </section>;
}
