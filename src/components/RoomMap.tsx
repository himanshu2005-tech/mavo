import { Check, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import type { ReactNode } from "react";
import type { CrewInstance, Thought } from "../types";
import { isCard } from "./ThoughtCard";
import { look, type FlowStep } from "./WorkflowFlow";

export interface MapNode { title: string; status: "waiting" | "running" | "done" | "failed" | "asking" | "skipped" }

/** The steps the map draws: what it is doing now, step by step, while it works; between runs,
 *  the path it follows (its plan's steps, in its own words). */
export function mapNodes(crew: Pick<CrewInstance, "cycle" | "configuration_plan">): MapNode[] {
  if (crew.cycle?.steps?.length) {
    return crew.cycle.steps.map(s => ({ title: s.title, status: (["running", "done", "failed", "asking", "skipped"].includes(s.status) ? s.status : "waiting") as MapNode["status"] }));
  }
  const plan = crew.configuration_plan as { recipe?: { steps?: FlowStep[] }; workflow_draft?: { steps?: FlowStep[] } } | undefined;
  const steps = plan?.recipe?.steps ?? plan?.workflow_draft?.steps ?? [];
  return steps.map(step => ({ title: step.run_text || step.title || look(step)[0], status: "waiting" }));
}

/** What it has said it is doing, in order ("Reading your Gmail", "Read 14 emails", "Writing it
 *  up"): the last one is happening now. */
export function progressLines(thoughts: Thought[] = []): string[] {
  const lines = thoughts.filter(t => !isCard(t) && t.tool !== "action").map(t => [t.label, t.text].filter(Boolean).join(" ").trim()).filter(Boolean);
  return lines.filter((l, i) => l !== lines[i - 1]).slice(-5);
}

const STEP = { type: "spring", stiffness: 420, damping: 36 } as const;

/** The agent's line map, beside its room: its steps as a line of stops, the one it is on
 *  pulsing, and under it what it is doing right now. */
export function RoomMap({ crew, children }: { crew: CrewInstance; children?: ReactNode }) {
  const nodes = mapNodes(crew);
  const cycle = crew.cycle;
  const live = !!cycle;
  const lines = live ? progressLines(cycle?.thoughts) : [];
  const on = nodes.findIndex(n => n.status === "running" || n.status === "asking");
  const head = live ? (cycle?.asked ? "Answering" : "Working") : "Steps";
  return <aside className="room-map" aria-label="What it does" data-live={live || undefined}>
    <p className="room-map-head">{live && <i className="room-map-pulse" aria-hidden="true" />}{head}</p>
    {cycle?.asked && <p className="room-map-asked">{cycle.asked}</p>}
    {nodes.length === 0 ? <p className="room-map-empty">{live ? "Starting…" : "Its steps show here when it runs."}</p>
      : <ol className="line-map">
        {nodes.map((node, i) => <motion.li key={i} layout="position" transition={STEP} data-status={node.status}>
          <span className="lm-dot" aria-hidden="true">{node.status === "done" ? <Check size={11} strokeWidth={3} /> : node.status === "failed" ? <X size={11} strokeWidth={3} /> : null}</span>
          <div className="lm-text">
            <b>{node.title}</b>
            {i === on && lines.length > 0 && <ol className="lm-now" aria-live="polite">
              <AnimatePresence initial={false}>{lines.map((line, n) => <motion.li key={line + n} data-now={n === lines.length - 1 || undefined}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
                {n < lines.length - 1 ? <Check size={11} aria-hidden="true" /> : <span className="lm-spin" aria-hidden="true" />}{line}
              </motion.li>)}</AnimatePresence>
            </ol>}
            {node.status === "asking" && <span className="lm-word">Needs your answer</span>}
          </div>
        </motion.li>)}
      </ol>}
    {children}
  </aside>;
}
