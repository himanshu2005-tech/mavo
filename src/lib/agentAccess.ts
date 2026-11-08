/** Switching what an agent may read or do, from the graph.
 *
 * Every switch goes through the same places the rest of Mavo uses: the agent's own permissions
 * (PATCH /crew) and its action rules (PUT /action-policies). Nothing here grants more than the
 * person asked for, and an action that needs limits it does not have yet (which sites, which
 * folders, which recipients) is not switched on blindly: it asks to be set up instead. */
import type { ActionRule } from "../components/ActionAccessEditor";
import { ACTION_LABELS, opsFor, wiring } from "../components/crewWiring";
import type { CrewInstance, GoogleCapability } from "../types";
import { api } from "./api";
import { scopeField } from "./actionRuleInput";

export type AccessKind = "resource" | "action";
export interface AccessOption {
  key: string; label: string; icon: string; kind: AccessKind;
  on: boolean;
  /** A short reason it cannot be switched here, when it cannot. */
  blocked?: string;
  /** Switching it on needs a second look: it acts as the person, or cannot be undone. */
  risky?: boolean;
}

/** What Mavo has: connected Google capabilities ("gmail:read"), connector capabilities
 *  ("feeds:hn:read", "github:repo:read") and whether weather is on. */
export interface AccessContext { google: string[]; connectors: string[]; weather: boolean }

const GOOGLE_READS: Record<string, [string, string]> = {
  gmail: ["Gmail", "mail"], calendar: ["Calendar", "calendar"], tasks: ["Tasks", "tasks"], drive: ["Drive", "drive"], contacts: ["Contacts", "contacts"], youtube: ["YouTube", "youtube"],
};
// The Google capability an action node needs, and the rule operations behind it.
const GOOGLE_ACTION_CAP: Record<string, GoogleCapability> = {
  "gmail.send": "gmail:send", "gmail.draft": "gmail:modify", "gmail.tidy": "gmail:modify", "calendar.edit": "calendar:write", "tasks.edit": "tasks:write",
};
const RISKY = new Set(["gmail.send", "gmail.tidy", "calendar.edit", "tasks.edit", "windows.sleep", "windows.shutdown", "windows.apps", "terminal.run", "project.run", "project", "web.act", "files.organise"]);
export const isRisky = (key: string) => RISKY.has(key) || key.startsWith("act:");
// Actions whose rule has nothing to limit: on means on.
const PC_CONTROLS = ["windows.lock", "windows.volume", "windows.media", "windows.speak"];
const NO_SCOPE = new Set(["windows.sleep", "windows.shutdown", ...PC_CONTROLS]);

const googleCaps = (crew: Pick<CrewInstance, "permissions">): GoogleCapability[] =>
  (crew.permissions ?? []).filter(p => p.startsWith("google:")).map(p => p.slice(7) as GoogleCapability);

/** Everything this agent could be connected to, each marked on or off: what it has now, plus
 *  what Mavo can offer it. */
export function accessOptions(crew: CrewInstance, ctx: AccessContext): AccessOption[] {
  const { reads, does } = wiring(crew);
  const on = new Map([...reads, ...does].map(w => [w.key, w]));
  const out = new Map<string, AccessOption>();
  const add = (o: Omit<AccessOption, "on"> & { on?: boolean }) => {
    if (out.has(o.key)) return;
    const had = on.get(o.key);
    out.set(o.key, { ...o, on: o.on ?? !!had?.allowed, risky: o.kind === "action" && isRisky(o.key) });
  };
  // What it uses now comes first, in the order it was given.
  [...reads, ...does].forEach(w => add({ key: w.key, label: w.label, icon: w.icon, kind: w.kind }));
  const computer = crew.template_id === "computer";
  for (const [svc, [label, icon]] of Object.entries(GOOGLE_READS))
    if (ctx.google.includes(`${svc}:read`)) add({ key: "google." + svc, label, icon, kind: "resource" });
  if (ctx.connectors.some(c => c.startsWith("feeds:"))) add({ key: "feeds", label: "News feeds", icon: "rss", kind: "resource" });
  if (ctx.connectors.some(c => c.startsWith("github:"))) add({ key: "github", label: "GitHub", icon: "code", kind: "resource" });
  add({ key: "weather", label: "Weather", icon: "weather", kind: "resource", blocked: ctx.weather ? undefined : "Weather is off for Mavo" });
  if (computer) add({ key: "device", label: "This computer", icon: "monitor", kind: "resource" });
  add({ key: "web", label: "Websites", icon: "globe", kind: "resource" });
  add({ key: "notes", label: "Notes", icon: "notes", kind: "resource" });
  add({ key: "files", label: "Folders", icon: "files", kind: "resource" });
  add({ key: "notify", label: "Desktop alerts", icon: "bell", kind: "action" });
  if (computer) add({ key: "device.clean", label: "Free up space", icon: "trash", kind: "action" });
  for (const [key, cap] of Object.entries(GOOGLE_ACTION_CAP))
    if (ctx.google.includes(cap)) add({ key, label: ACTION_LABELS[key][0], icon: ACTION_LABELS[key][1], kind: "action" });
  for (const key of ["notes.write", "files.organise", "files.backup", ...PC_CONTROLS])
    add({ key, label: ACTION_LABELS[key][0], icon: ACTION_LABELS[key][1], kind: "action" });
  for (const key of ["windows.sleep", "windows.shutdown", "windows.apps", "web.act", "terminal.run"])
    add({ key, label: key === "web.act" ? "Act on websites" : ACTION_LABELS[key][0], icon: key === "web.act" ? "click" : ACTION_LABELS[key][1], kind: "action" });
  return [...out.values()];
}

export type ChangeResult = { done: true } | { setup: string; rule: ActionRule };

async function rulesFor(crewId: string) {
  const listed = await api.actionPolicies(crewId, true);
  return new Map(listed.actions.map(a => [a.operation, a.rule]));
}

/** Turns an action's rules on (or off) for this agent. On: each rule keeps the limits it has;
 *  a site node adds its site. An action that needs limits and has none is not saved: the first
 *  such operation comes back to be set up, with its rule. */
async function setRules(crewId: string, ops: string[], enabled: boolean, site?: string): Promise<ChangeResult> {
  const rules = await rulesFor(crewId);
  const next: [string, ActionRule][] = [];
  for (const op of ops) {
    const rule = rules.get(op);
    if (!rule) continue; // not available on this computer
    if (!enabled) { if (rule.enabled) next.push([op, { ...rule, enabled: false }]); continue; }
    let changed: ActionRule = { ...rule, enabled: true };
    if (site) changed = { ...changed, mode: "restricted", domains: [...new Set([...(rule.domains ?? []), site])] };
    if (NO_SCOPE.has(op)) changed = { ...changed, mode: "unrestricted" };
    const field = scopeField(op);
    if (changed.mode === "restricted" && field && !(changed[field] ?? []).length) return { setup: op, rule: changed };
    next.push([op, changed]);
  }
  for (const [op, rule] of next) await api.saveActionPolicy(op, rule, crewId);
  return { done: true };
}

/** Switches one thing on or off for one agent. */
export async function changeAccess(crew: CrewInstance, key: string, enabled: boolean, ctx: AccessContext): Promise<ChangeResult> {
  const caps = googleCaps(crew);
  const saveGoogle = (next: GoogleCapability[]) => api.updateCrew(crew.id, {
    google_capabilities: next, google_resources: next.filter(c => c.endsWith(":read")).map(c => c.split(":")[0]) as never,
  });
  if (key.startsWith("google.")) {
    const svc = key.slice(7);
    await saveGoogle(enabled ? [...new Set([...caps, `${svc}:read` as GoogleCapability])] : caps.filter(c => !c.startsWith(svc + ":")));
    return { done: true };
  }
  if (key === "feeds" || key === "github") {
    const mine = (crew.permissions ?? []).filter(p => p.startsWith("github:") || p.startsWith("feeds:"));
    const kept = mine.filter(p => !p.startsWith(key + ":"));
    const added = enabled ? ctx.connectors.filter(c => c.startsWith(key + ":") && c.endsWith(":read")) : [];
    await api.updateCrew(crew.id, { connector_capabilities: [...kept, ...added] });
    return { done: true };
  }
  if (key === "weather") { await api.updateCrew(crew.id, { allow_weather: enabled }); return { done: true }; }
  if (key === "device") { await api.updateCrew(crew.id, { allow_device_metrics: enabled }); return { done: true }; }
  if (key === "notify") { await api.updateCrew(crew.id, { allow_notifications: enabled }); return { done: true }; }
  if (key === "device.clean") { await api.updateCrew(crew.id, { allow_temp_cleanup: enabled }); return { done: true }; }
  const cap = GOOGLE_ACTION_CAP[key];
  if (cap) {
    // The rules first: if they need limits, nothing is granted until they are set.
    const ruled = await setRules(crew.id, opsFor(key), enabled);
    if ("setup" in ruled) return ruled;
    // Off: the capability goes only when no other action node still needs it (Draft and Tidy
    // share "modify").
    const stillNeeded = !enabled && Object.entries(GOOGLE_ACTION_CAP).some(([k, c]) => k !== key && c === cap && wiring(crew).does.some(d => d.key === k && d.allowed));
    if (enabled && !caps.includes(cap)) await saveGoogle([...caps, cap]);
    if (!enabled && !stillNeeded) await saveGoogle(caps.filter(c => c !== cap));
    return { done: true };
  }
  const site = key.startsWith("site:") ? key.slice(5) : key.startsWith("act:") ? key.slice(4) : undefined;
  return setRules(crew.id, opsFor(key), enabled, enabled ? site : undefined);
}

/** Saves a rule the person set up by hand, then the rest of that action's operations with the
 *  same limits. */
export async function saveSetup(crew: CrewInstance, key: string, op: string, rule: ActionRule): Promise<ChangeResult> {
  await api.saveActionPolicy(op, rule, crew.id);
  const field = scopeField(op);
  const rest = opsFor(key).filter(o => o !== op);
  const rules = await rulesFor(crew.id);
  for (const other of rest) {
    const current = rules.get(other);
    if (!current) continue;
    const otherField = scopeField(other);
    const shared = field && otherField === field ? { [field]: rule[field] } : {};
    await api.saveActionPolicy(other, { ...current, enabled: true, mode: rule.mode, ...shared }, crew.id);
  }
  const cap = GOOGLE_ACTION_CAP[key];
  if (cap && !googleCaps(crew).includes(cap)) await api.updateCrew(crew.id, { google_capabilities: [...googleCaps(crew), cap], google_resources: googleCaps(crew).filter(c => c.endsWith(":read")).map(c => c.split(":")[0]) as never });
  return { done: true };
}

/** The pool resource behind a node, for its global Off / Read / Read & act level. */
export function poolIdFor(key: string): string | null {
  if (key.startsWith("google.")) return key.slice(7);
  if (key === "feeds" || key === "github" || key === "weather" || key === "device") return key;
  if (key === "web" || key === "web.act" || key.startsWith("site:") || key.startsWith("act:")) return "browser";
  if (key === "terminal.run") return "terminal";
  if (key === "notes" || key.startsWith("notes.")) return "notes";
  if (key === "files" || key.startsWith("files.")) return "files";
  if (key.startsWith("windows.")) return "windows";
  if (key.startsWith("gmail.")) return "gmail";
  if (key === "calendar.edit") return "calendar";
  if (key === "tasks.edit") return "tasks";
  if (key === "device.clean") return "device";
  return null;
}

/** What Mavo has connected, for the switches. */
export async function loadAccessContext(): Promise<AccessContext> {
  const [connectors, access] = await Promise.all([api.connectors().catch(() => []), api.resourceAccess().catch(() => ({ weather: false, location: false }))]);
  const google = connectors.find(c => c.provider === "google" && c.connected)?.capabilities.map(c => c.replace(/^google:/, "")) ?? [];
  const others = connectors.filter(c => c.provider !== "google" && c.connected).flatMap(c => c.capabilities);
  return { google, connectors: others, weather: access.weather };
}
