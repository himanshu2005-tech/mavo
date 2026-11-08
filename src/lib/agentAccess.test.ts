import { beforeEach, expect, it, vi } from "vitest";
import type { CrewInstance } from "../types";

const calls: { update: [string, Record<string, unknown>][]; rules: [string, Record<string, unknown>, string | undefined][] } = { update: [], rules: [] };
let rules: Record<string, Record<string, unknown>> = {};
vi.mock("./api", () => ({
  api: {
    updateCrew: vi.fn(async (id: string, change: Record<string, unknown>) => { calls.update.push([id, change]); return {}; }),
    saveActionPolicy: vi.fn(async (op: string, rule: Record<string, unknown>, crewId?: string) => { calls.rules.push([op, rule, crewId]); rules[op] = rule; return rule; }),
    actionPolicies: vi.fn(async () => ({ paused: false, actions: Object.entries(rules).map(([operation, rule]) => ({ operation, rule })) })),
  },
}));
const { accessOptions, changeAccess, poolIdFor, saveSetup } = await import("./agentAccess");

const off = (extra: Record<string, unknown> = {}) => ({ enabled: false, mode: "restricted", folders: [], domains: [], apps: [], recipients: [], repositories: [], ...extra });
const crew = (over: Partial<CrewInstance> = {}) => ({ id: "c1", template_id: "communication", name: "Inbox Summary", status: "active", permissions: [], configuration_plan: {}, ...over }) as unknown as CrewInstance;
const ctx = { google: ["gmail:read", "gmail:send", "gmail:modify", "calendar:read", "calendar:write"], connectors: ["feeds:hn:read", "feeds:bbc:read", "github:repo:read", "github:issues:write"], weather: true };

beforeEach(() => {
  calls.update = []; calls.rules = [];
  rules = { "gmail.send": off(), "windows.sleep": off({ apps: [] }), "browser.open": off(), "browser.read": off(), "browser.navigate": off(), "browser.search": off(), "browser.scroll": off(), "browser.wait": off(), "terminal.run": off() };
});

it("offers what an agent uses, then what Mavo can give it, each on or off", () => {
  const options = accessOptions(crew({ permissions: ["google:gmail:read", "notifications:show"] }), ctx);
  const byKey = Object.fromEntries(options.map(o => [o.key, o]));
  expect(options[0].key).toBe("google.gmail"); // what it uses comes first
  expect(byKey["google.gmail"].on).toBe(true);
  expect(byKey.notify.on).toBe(true);
  expect(byKey["google.calendar"].on).toBe(false);
  expect(byKey["gmail.send"]).toMatchObject({ on: false, risky: true });
  expect(byKey["google.drive"]).toBeUndefined(); // not connected
  expect(byKey.device).toBeUndefined(); // only a Computer agent reads this computer
  expect(accessOptions(crew(), { ...ctx, weather: false }).find(o => o.key === "weather")?.blocked).toBe("Weather is off for Mavo");
});

it("switches a Google service on and off without touching the rest", async () => {
  const c = crew({ permissions: ["google:gmail:read", "google:gmail:send", "google:calendar:read"] });
  await changeAccess(c, "google.calendar", false, ctx);
  expect(calls.update[0]).toEqual(["c1", { google_capabilities: ["gmail:read", "gmail:send"], google_resources: ["gmail"] }]);
  await changeAccess(c, "google.tasks", true, ctx);
  expect(calls.update[1][1].google_capabilities).toEqual(["gmail:read", "gmail:send", "calendar:read", "tasks:read"]);
});

it("gives news feeds and GitHub to read, never GitHub writes", async () => {
  await changeAccess(crew({ permissions: ["github:repo:read"] }), "feeds", true, ctx);
  expect(calls.update[0][1]).toEqual({ connector_capabilities: ["github:repo:read", "feeds:hn:read", "feeds:bbc:read"] });
  await changeAccess(crew(), "github", true, ctx);
  expect(calls.update[1][1]).toEqual({ connector_capabilities: ["github:repo:read"] });
});

it("does not send mail on without the people it may write to", async () => {
  const result = await changeAccess(crew({ permissions: ["google:gmail:read"] }), "gmail.send", true, ctx);
  expect(result).toMatchObject({ setup: "gmail.send" });
  expect(calls.rules).toEqual([]);
  expect(calls.update).toEqual([]); // nothing granted until the limits are set
  await saveSetup(crew({ permissions: ["google:gmail:read"] }), "gmail.send", "gmail.send", { ...off(), enabled: true, recipients: ["mom@example.com"] } as never);
  expect(calls.rules[0]).toEqual(["gmail.send", expect.objectContaining({ enabled: true, recipients: ["mom@example.com"] }), "c1"]);
  expect(calls.update[0][1].google_capabilities).toEqual(["gmail:read", "gmail:send"]);
});

it("sleep is on or off: nothing to limit", async () => {
  expect(await changeAccess(crew(), "windows.sleep", true, ctx)).toEqual({ done: true });
  expect(calls.rules[0]).toEqual(["windows.sleep", expect.objectContaining({ enabled: true, mode: "unrestricted" }), "c1"]);
});

it("reading a site allows that site only, for every way of reading it", async () => {
  await changeAccess(crew(), "site:airouter.io", true, ctx);
  expect(calls.rules.map(r => r[0]).sort()).toEqual(["browser.navigate", "browser.open", "browser.read", "browser.scroll", "browser.search", "browser.wait"]);
  expect(calls.rules.every(([, rule]) => rule.mode === "restricted" && (rule.domains as string[]).includes("airouter.io"))).toBe(true);
});

it("off turns off only what was on", async () => {
  rules["browser.open"] = { ...off(), enabled: true, domains: ["x.com"] };
  await changeAccess(crew(), "web", false, ctx);
  expect(calls.rules).toEqual([["browser.open", expect.objectContaining({ enabled: false }), "c1"]]);
});

it("knows which of Mavo's resources a node is", () => {
  expect([poolIdFor("google.gmail"), poolIdFor("gmail.send"), poolIdFor("site:x.com"), poolIdFor("windows.sleep"), poolIdFor("notify")]).toEqual(["gmail", "gmail", "browser", "windows", null]);
  expect([poolIdFor("notes"), poolIdFor("notes.write"), poolIdFor("files.organise"), poolIdFor("windows.media")]).toEqual(["notes", "notes", "files", "windows"]);
});

it("notes are not opened up until the person picks the folders, then search and read share them", async () => {
  rules["notes.find"] = off(); rules["notes.read"] = off();
  expect(await changeAccess(crew(), "notes", true, ctx)).toMatchObject({ setup: "notes.find" });
  expect(calls.rules).toEqual([]);
  await saveSetup(crew(), "notes", "notes.find", { ...off(), enabled: true, folders: ["~/Documents/Notes"] } as never);
  expect(calls.rules.map(([op, rule]) => [op, rule.folders])).toEqual([["notes.find", ["~/Documents/Notes"]], ["notes.read", ["~/Documents/Notes"]]]);
});

it("lock, volume, music and speaking are on or off, and moving files asks first", async () => {
  rules["windows.lock"] = off({ apps: [] }); rules["files.move"] = off();
  expect(await changeAccess(crew(), "windows.lock", true, ctx)).toEqual({ done: true });
  expect(calls.rules[0]).toEqual(["windows.lock", expect.objectContaining({ enabled: true, mode: "unrestricted" }), "c1"]);
  const options = Object.fromEntries(accessOptions(crew(), ctx).map(o => [o.key, o]));
  expect(options["files.organise"]).toMatchObject({ risky: true, label: "Organise files" });
  expect(options["windows.media"]).toMatchObject({ risky: false, label: "Play & pause music" });
  expect(options.notes).toMatchObject({ kind: "resource", on: false });
});
