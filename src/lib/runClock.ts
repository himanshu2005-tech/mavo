import type { CrewInstance } from "../types";
import { clockTime, parseTime } from "./time";

/** Where a scheduled agent is between one run and the next. */
export type RunClock = {
  /** 0 just ran, 1 runs now. */
  progress: number;
  /** Milliseconds to the next run (0 or less: due). */
  left: number;
  /** "in 12 min", "at 8:00 am", "any moment". */
  label: string;
  /** "every 30 min", "every day". */
  every: string;
  /** Whether you can ask for a run now (a reminder runs at its time only). */
  canRunNow: boolean;
};

const MIN = 60_000, HOUR = 60 * MIN, DAY = 24 * HOUR;

/** How long one turn of the clock is, or null when the agent waits for something instead
 *  (a mail, a calendar change, you). */
export function runPeriod(crew: CrewInstance): number | null {
  if (crew.trigger_type === "interval") return Math.max(1, crew.review_interval_minutes || 30) * MIN;
  if (crew.trigger_type === "daily") return DAY;
  return null;
}

export function timeLeft(ms: number, at?: string | null): string {
  if (ms <= 0) return "any moment";
  if (ms < MIN) return "in under a minute";
  if (ms < HOUR) return `in ${Math.ceil(ms / MIN)} min`;
  if (ms < 12 * HOUR || !at) {
    // Round the whole first: 4 h 59.6 min is "5 h", never "4 h 60 min".
    const total = Math.round(ms / MIN), h = Math.floor(total / 60), m = total % 60;
    return m ? `in ${h} h ${m} min` : `in ${h} h`;
  }
  return "at " + clockTime(at);
}

function everyText(crew: CrewInstance): string {
  if (crew.trigger_type === "daily") return "every day";
  const m = Math.max(1, crew.review_interval_minutes || 30);
  if (m % 60) return `every ${m} min`;
  return m === 60 ? "every hour" : `every ${m / 60} h`;
}

/** The clock for an agent that runs on a schedule and is waiting for its turn; null otherwise
 *  (stopped, a draft, busy, stuck on a question, or not on a schedule). */
export function runClock(crew: CrewInstance, now = Date.now()): RunClock | null {
  if (crew.status === "draft" || crew.status === "paused" || !crew.is_running || crew.configuration_plan?.finished) return null;
  if (crew.runtime_state && crew.runtime_state !== "waiting") return null; // working, stuck, or waiting on something else
  const period = runPeriod(crew);
  if (!period || !crew.next_run_at) return null;
  const next = parseTime(crew.next_run_at).getTime();
  if (Number.isNaN(next)) return null;
  // The real gap since the last run, when there is one that makes sense; the schedule's otherwise.
  const last = crew.last_run_at ? parseTime(crew.last_run_at).getTime() : NaN;
  const span = next - last > MIN && next - last < period * 2 ? next - last : period;
  const left = next - now;
  return {
    progress: Math.min(1, Math.max(0, 1 - left / span)),
    left, label: timeLeft(left, crew.next_run_at), every: everyText(crew),
    canRunNow: crew.trigger_type !== "daily",
  };
}

/** Waits for something to happen (a mail, a calendar change) rather than a time. */
export function listens(crew: CrewInstance): boolean {
  return crew.is_running && crew.status !== "draft" && /^(gmail_message|calendar_changed|tasks_changed)$/.test(crew.trigger_type);
}
