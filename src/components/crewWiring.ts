import type { CrewInstance } from "../types";

/** One thing an agent reads (a resource) or can do (an action), as the map shows it. */
export interface Wire { key: string; label: string; icon: string; allowed: boolean; kind: "resource" | "action" }

const GOOGLE: Record<string, [string, string]> = {
  gmail: ["Gmail", "mail"], calendar: ["Calendar", "calendar"], tasks: ["Tasks", "tasks"],
  drive: ["Drive", "drive"], contacts: ["Contacts", "contacts"], youtube: ["YouTube", "youtube"],
};

const ACTIONS: Record<string, [string, string, string]> = {
  // operation: [map key, label, icon]
  "gmail.send": ["gmail.send", "Send mail", "send"],
  "gmail.draft": ["gmail.draft", "Draft mail", "draft"],
  "gmail.archive": ["gmail.tidy", "Tidy mail", "mail"], "gmail.mark_read": ["gmail.tidy", "Tidy mail", "mail"], "gmail.trash": ["gmail.tidy", "Tidy mail", "mail"],
  "calendar.create": ["calendar.edit", "Change calendar", "calendarPlus"], "calendar.update": ["calendar.edit", "Change calendar", "calendarPlus"], "calendar.delete": ["calendar.edit", "Change calendar", "calendarPlus"],
  "tasks.create": ["tasks.edit", "Change tasks", "tasks"], "tasks.update": ["tasks.edit", "Change tasks", "tasks"], "tasks.complete": ["tasks.edit", "Change tasks", "tasks"], "tasks.delete": ["tasks.edit", "Change tasks", "tasks"],
  "windows.sleep": ["windows.sleep", "Sleep the PC", "moon"],
  "windows.shutdown": ["windows.shutdown", "Shut down the PC", "power"],
  "windows.type": ["windows.apps", "Use other apps", "app"], "windows.key": ["windows.apps", "Use other apps", "app"], "windows.focus": ["windows.apps", "Use other apps", "app"], "windows.minimize": ["windows.apps", "Use other apps", "app"],
  "terminal.run": ["terminal.run", "Run commands", "terminal"],
  "project.create": ["project", "Project files", "folder"], "project.write": ["project", "Project files", "folder"], "project.run": ["project.run", "Run project scripts", "terminal"],
  "github.create_issue": ["github", "GitHub changes", "code"], "github.comment": ["github", "GitHub changes", "code"],
  "windows.lock": ["windows.lock", "Lock the PC", "lock"],
  "windows.volume": ["windows.volume", "Set the volume", "volume"],
  "windows.media": ["windows.media", "Play & pause music", "music"],
  "windows.speak": ["windows.speak", "Speak aloud", "speak"],
  "notes.write": ["notes.write", "Write notes", "notesWrite"],
  "files.move": ["files.organise", "Organise files", "organise"],
  "files.backup": ["files.backup", "Back up folders", "backup"],
};
// Local actions that only look (search and read notes, list a folder): on the map they are
// something the agent reads, like Gmail.
const LOCAL_READS: Record<string, [string, string, string]> = {
  "notes.find": ["notes", "Notes", "notes"], "notes.read": ["notes", "Notes", "notes"],
  "files.list": ["files", "Folders", "files"],
};
// Browser actions that change a page (clicks, typing): "Act on <site>". Opening and reading a
// site is reading it, so that site is a resource.
const ACTING = new Set(["browser.click", "browser.double_click", "browser.fill", "browser.select", "browser.press", "browser.drag", "browser.hover"]);
const READING = new Set(["browser.open", "browser.read", "browser.navigate", "browser.search", "browser.scroll", "browser.wait"]);

/** The operations behind one action node ("Change calendar" is create, update and delete). */
export function opsFor(key: string): string[] {
  if (key === "web.act" || key.startsWith("act:")) return [...ACTING];
  if (key === "web" || key.startsWith("site:")) return [...READING];
  if (key === "notes" || key === "files") return Object.entries(LOCAL_READS).filter(([, [k]]) => k === key).map(([op]) => op);
  return Object.entries(ACTIONS).filter(([, [k]]) => k === key).map(([op]) => op);
}

export const ACTION_LABELS: Record<string, [string, string]> = Object.fromEntries(Object.values(ACTIONS).map(([k, label, icon]) => [k, [label, icon]]));

function grantFor(op: string): string[] {
  const [service, verb] = op.split(".");
  if (service in GOOGLE) return [`google:${service}:${verb === "send" ? "send" : service === "gmail" ? "modify" : "write"}`];
  return [`action:${op}`];
}

/** What a Crew reads and what it can do, from what it was given (permissions) and what it asked
 *  for (its requested actions, with their sites). Asked for but not given: shown, not allowed. */
export function wiring(crew: Pick<CrewInstance, "permissions" | "configuration_plan">): { reads: Wire[]; does: Wire[] } {
  const perms = new Set(crew.permissions ?? []);
  const requested = (crew.configuration_plan?.draft?.requested_actions ?? []) as { operation: string; domains?: string[] }[];
  const reads = new Map<string, Wire>();
  const does = new Map<string, Wire>();
  const read = (w: Omit<Wire, "kind">) => { const had = reads.get(w.key); reads.set(w.key, { ...w, allowed: w.allowed || !!had?.allowed, kind: "resource" }); };
  const act = (w: Omit<Wire, "kind">) => { const had = does.get(w.key); does.set(w.key, { ...w, allowed: w.allowed || !!had?.allowed, kind: "action" }); };

  for (const p of perms) {
    const [scope, name, mode] = p.split(":");
    if (scope === "google" && name in GOOGLE && (mode === "read" || mode === "body")) read({ key: "google." + name, label: GOOGLE[name][0], icon: GOOGLE[name][1], allowed: true });
    else if (scope === "feeds") read({ key: "feeds", label: "News feeds", icon: "rss", allowed: true });
    else if (scope === "github") read({ key: "github", label: "GitHub", icon: "code", allowed: true });
    else if (p === "weather:read") read({ key: "weather", label: "Weather", icon: "weather", allowed: true });
    else if (p === "device:read") read({ key: "device", label: "This computer", icon: "monitor", allowed: true });
    else if (p === "device:clean_temp") act({ key: "device.clean", label: "Free up space", icon: "trash", allowed: true });
    else if (p === "notifications:show") act({ key: "notify", label: "Desktop alerts", icon: "bell", allowed: true });
    else if (scope === "action" && name) {
      const op = p.slice("action:".length);
      if (LOCAL_READS[op]) read({ key: LOCAL_READS[op][0], label: LOCAL_READS[op][1], icon: LOCAL_READS[op][2], allowed: true });
      else if (READING.has(op)) read({ key: "web", label: "Websites", icon: "globe", allowed: true });
      else if (ACTING.has(op)) act({ key: "web.act", label: "Act on websites", icon: "click", allowed: true });
      else if (ACTIONS[op]) act({ key: ACTIONS[op][0], label: ACTIONS[op][1], icon: ACTIONS[op][2], allowed: true });
    } else if (scope === "google" && name in GOOGLE && (mode === "write" || mode === "modify" || mode === "send")) {
      // A write grant without a listed action: it can change that service.
      const key = name === "gmail" ? (mode === "send" ? "gmail.send" : "gmail.tidy") : `${name}.edit`;
      act({ key, label: name === "gmail" ? (mode === "send" ? "Send mail" : "Tidy mail") : `Change ${GOOGLE[name][0].toLowerCase()}`, icon: name === "gmail" ? (mode === "send" ? "send" : "mail") : name === "calendar" ? "calendarPlus" : GOOGLE[name][1], allowed: true });
    }
  }
  for (const r of requested) {
    const op = r.operation, sites = (r.domains ?? []).filter(Boolean);
    const allowed = grantFor(op).some(g => perms.has(g));
    if (LOCAL_READS[op]) read({ key: LOCAL_READS[op][0], label: LOCAL_READS[op][1], icon: LOCAL_READS[op][2], allowed });
    else if (READING.has(op)) {
      if (sites.length) { reads.delete("web"); sites.forEach(s => read({ key: "site:" + s, label: s, icon: "globe", allowed: allowed || perms.has("action:" + op) })); }
      else read({ key: "web", label: "Websites", icon: "globe", allowed });
    } else if (ACTING.has(op)) {
      if (sites.length) { does.delete("web.act"); sites.forEach(s => act({ key: "act:" + s, label: "Act on " + s, icon: "click", allowed })); }
      else act({ key: "web.act", label: "Act on websites", icon: "click", allowed });
    } else if (ACTIONS[op]) act({ key: ACTIONS[op][0], label: ACTIONS[op][1], icon: ACTIONS[op][2], allowed });
  }
  return { reads: [...reads.values()], does: [...does.values()] };
}

/** Agents in the order work flows: a Crew after the ones it hears from, then by name. */
export function flowOrder<T extends Pick<CrewInstance, "id" | "name" | "configuration_plan">>(crews: T[]): T[] {
  const byId = new Map(crews.map(c => [c.id, c]));
  const depth = new Map<string, number>();
  const level = (c: T, seen = new Set<string>()): number => {
    if (depth.has(c.id)) return depth.get(c.id)!;
    if (seen.has(c.id)) return 0;
    seen.add(c.id);
    const ups = (c.configuration_plan?.upstream ?? []).map(id => byId.get(id)).filter((u): u is T => !!u);
    const d = ups.length ? 1 + Math.max(...ups.map(u => level(u, seen))) : 0;
    depth.set(c.id, d);
    return d;
  };
  crews.forEach(c => level(c));
  // Keep a chain together: a Crew sits right under the one it hears from where it can.
  return [...crews].sort((a, b) => {
    const root = (c: T): string => {
      const seen = new Set<string>();
      let current = c;
      while (!seen.has(current.id)) {
        seen.add(current.id);
        const up = (current.configuration_plan?.upstream ?? []).find(id => byId.has(id));
        if (!up) return current.name.toLowerCase();
        current = byId.get(up)!;
      }
      // The API rejects cycles; stale/imported data must not crash the entire map either.
      return [...seen].map(id => byId.get(id)!.name.toLowerCase()).sort()[0];
    };
    return root(a).localeCompare(root(b)) || depth.get(a.id)! - depth.get(b.id)! || a.name.localeCompare(b.name);
  });
}
