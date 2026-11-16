import { Gauge } from "lucide-react";
import type { CallLimit, CrewInstance } from "../types";

/** The AI-call limit that stopped an agent, if one did. Only the daily safety ceiling is left:
 *  per-run budgets are gone (Mavo caps a run itself), so an old "run" limit is never shown and
 *  the agent carries on by itself. The older "daily model-call budget" wording still counts. */
export function limitOf(crew: Pick<CrewInstance, "limit" | "question" | "last_runtime_error" | "daily_call_limit">): CallLimit | null {
  if (crew.limit) return crew.limit.kind === "day" ? crew.limit : null;
  const text = `${crew.question ?? ""} ${crew.last_runtime_error ?? ""}`;
  if (/daily model-call budget/i.test(text)) {
    const current = crew.daily_call_limit ?? 0;
    return { kind: "day", current, suggested: current };
  }
  return null;
}

/** What stopped it, in plain words. Nothing to set: it carries on by itself tomorrow. */
export function LimitCard({ crew }: { crew: CrewInstance }) {
  const limit = limitOf(crew)!;
  return <section className="limit-card" aria-label="AI call limit">
    <div className="limit-head"><Gauge size={15} aria-hidden="true" /><b>Used today's AI calls</b></div>
    <p>{`${crew.name} reached Mavo's daily safety limit of ${limit.current} AI calls. It carries on by itself tomorrow.`}</p>
    <div className="limit-actions"><span className="limit-note">Nothing is lost.</span></div>
  </section>;
}
