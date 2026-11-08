import type { CrewInstance } from "../types";

export function workerLabel(crew: CrewInstance): string {
  if (crew.status === "draft") return "Draft · Not yet added";
  if (crew.configuration_plan?.phase === "needs_access") return "Needs access";
  if (crew.configuration_plan?.phase === "needs_input") return "Needs input";
  if (!crew.is_running) return "Stopped";
  return ({working:"Working",waiting:"Running",waiting_browser:"Waiting for browser",waiting_event:"Listening for changes",needs_input:"Needs input",budget_paused:"Back tomorrow",error:"Needs attention"} as Record<string,string>)[crew.runtime_state] ?? "Running";
}
