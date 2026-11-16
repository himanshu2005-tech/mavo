import type { CrewRun } from "../types";

/** One short line for how a run the person started ended, so "Run now" never ends in silence. */
export function runOutcome(name: string, run: Pick<CrewRun, "status" | "result" | "error">): string {
  if (run.status === "failed" || run.status === "interrupted" || run.status === "cancelled") {
    const why = (run.error ?? "").split("\n").find(Boolean)?.trim();
    return `${name} stopped${why ? `: ${why.slice(0, 160)}` : "."}`;
  }
  const said = (run.result ?? "").split("\n").map(l => l.replace(/[*#_`>]+/g, "").trim()).find(Boolean) ?? "";
  if (!said || /^nothing new\.?$/i.test(said)) return `${name} checked: nothing new this time.`;
  return `${name}: ${said.slice(0, 180)}`;
}

/** The run started at or after `since` that has finished, newest first; null while none has. */
export function finishedSince(runs: CrewRun[], since: number): CrewRun | null {
  return runs.find(r => Date.parse(r.created_at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(r.created_at) ? r.created_at : r.created_at + "Z") >= since && r.status !== "running") ?? null;
}

/** What an agent that works on arrival found when there was nothing to work on. */
export const EVENT_WORDS: Record<string, string> = {
  gmail_message: "no new mail", calendar_changed: "no calendar changes", tasks_changed: "no task changes",
};

/** After "Run now" or "Check now": says it is running, then how the run ended ("checked:
 *  nothing new", what it did, or "looked: no new mail"), so a press never ends in silence. */
export async function watchRun(crewId: string, name: string, trigger: string | undefined, note: (text: string | null) => void) {
  const { api } = await import("./api");
  const since = Date.now() - 5000;
  note(`${name} is running…`);
  const done = (text: string) => { note(text); window.setTimeout(() => note(null), 12000); };
  for (let waited = 0; waited < 180_000; waited += 3000) {
    await new Promise(resolve => window.setTimeout(resolve, 3000));
    const run = finishedSince(await api.crewRuns(crewId).catch(() => []), since);
    if (run) return done(runOutcome(name, run));
    // An agent that works when something arrives looks, and with nothing new it goes back to
    // waiting without a run: say so, rather than leave "running…" up.
    if (waited >= 6000 && EVENT_WORDS[trigger ?? ""]) {
      const now = (await api.crew().catch(() => [])).find(c => c.id === crewId);
      if (now?.runtime_state === "waiting_event") return done(`${name} looked: ${EVENT_WORDS[trigger ?? ""]} since its last check. It runs when there is.`);
    }
  }
  done(`${name} is still working. Its result will show in its panel.`);
}
