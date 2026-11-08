import type { ActionRule } from "../components/ActionAccessEditor";

// Server policy revisions are read-only fencing metadata, not editable settings.
export function actionRuleInput(rule: ActionRule): ActionRule {
  return {enabled: rule.enabled, mode: rule.mode, folders: [...rule.folders],
    domains: [...rule.domains], apps: [...rule.apps], recipients: [...rule.recipients],repositories:[...(rule.repositories??[])]};
}

export type ScopeField = "folders" | "domains" | "apps" | "recipients" | "repositories";

/** The tools a script may be run with. Nothing else can run, whatever is typed. */
export const RUN_TOOLS = ["python.exe", "blender.exe"];

/** Which list of limits an action takes, if any. Where projects are kept is one setting, saved on
 *  "Create a project folder"; running a script is limited by the tools it may use. */
export function scopeField(operation: string): ScopeField | null {
  const service = operation.split(".")[0];
  if (service === "project") return operation === "project.create" ? "folders" : operation === "project.run" ? "apps" : null;
  if (service === "terminal" || service === "notes" || service === "files") return "folders";
  if (service === "browser") return "domains";
  if (service === "windows") return "apps";
  if (service === "github") return "repositories";
  return ["gmail.send", "gmail.draft"].includes(operation) ? "recipients" : null;
}

/** Services whose actions run on this computer, so they need no account connection. */
export const LOCAL_SERVICES = ["terminal", "browser", "windows", "project", "notes", "files"];
export const isLocal = (operation: string) => LOCAL_SERVICES.includes(operation.split(".")[0]);

/** One line for what a bulk "set all" did, so the person knows what is left to do by hand. */
export function bulkSummary(result: { changed: unknown[]; skipped: { label: string; reason: string }[] }): string {
  const done = result.changed.length;
  const left = result.skipped.length;
  if (!done && !left) return "Everything was already set that way.";
  const first = result.skipped.slice(0, 3).map(s => s.label).join(", ");
  return `${done} set` + (left ? `. ${left} left as they were because they need a list first (${first}${left > 3 ? ", …" : ""}).` : ".");
}

/** How often a saved automation may run, as the person picks it. Not more often than every five minutes. */
export const SCHEDULES: { label: string; minutes: number | null }[] = [
  { label: "Only when I ask", minutes: null }, { label: "Every 15 minutes", minutes: 15 }, { label: "Every hour", minutes: 60 },
  { label: "Every 6 hours", minutes: 360 }, { label: "Every day", minutes: 1440 },
];
export const scheduleLabel = (minutes: number | null) => SCHEDULES.find(s => s.minutes === minutes)?.label ?? (minutes ? `Every ${minutes} minutes` : "Only when I ask");

/** A name to offer for an automation made from a script: "build_drone.py" becomes "build drone". */
export const automationNameFor = (script: string | null | undefined, project: string) =>
  ((script ?? "").split("/").pop() ?? "").replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").trim().slice(0, 40) || project.slice(0, 40);
