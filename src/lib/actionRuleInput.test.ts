import { expect, it } from "vitest";
import { RUN_TOOLS, SCHEDULES, actionRuleInput, automationNameFor, bulkSummary, isLocal, scheduleLabel, scopeField } from "./actionRuleInput";
import { emptyRule } from "../components/ActionAccessEditor";

it("saves editable settings without echoing server revision metadata", () => {
  const serverRule = {...emptyRule(), enabled: true, mode: "unrestricted" as const, revision: "server-owned"};
  const payload = actionRuleInput(serverRule);
  expect(payload).toEqual({...emptyRule(), enabled: true, mode: "unrestricted"});
  expect(payload).not.toHaveProperty("revision");
  expect(serverRule.revision).toBe("server-owned");
});

it("limits each action by the one list that suits it", () => {
  expect(scopeField("browser.drag")).toBe("domains");
  expect(scopeField("terminal.run")).toBe("folders");
  expect(scopeField("windows.focus")).toBe("apps");
  expect(scopeField("gmail.send")).toBe("recipients");
  expect(scopeField("github.comment")).toBe("repositories");
  expect(scopeField("calendar.create")).toBeNull();
  // Where projects are kept is set once, on creating one; running a script is limited by its tool.
  expect(scopeField("project.create")).toBe("folders");
  expect(scopeField("project.run")).toBe("apps");
  expect(scopeField("project.write")).toBeNull();
  expect(scopeField("project.read")).toBeNull();
});

it("only python and blender can be named as tools that run scripts", () => {
  expect(RUN_TOOLS).toEqual(["python.exe", "blender.exe"]);
});

it("knows which actions run on this computer and need no account", () => {
  for (const op of ["terminal.run", "browser.hover", "windows.key", "project.run"]) expect(isLocal(op)).toBe(true);
  for (const op of ["gmail.send", "calendar.delete", "github.comment"]) expect(isLocal(op)).toBe(false);
});

it("says plainly what setting everything at once did and what is left", () => {
  expect(bulkSummary({changed: [], skipped: []})).toBe("Everything was already set that way.");
  expect(bulkSummary({changed: [1, 2, 3], skipped: []})).toBe("3 set.");
  const skipped = ["Open page", "Read page", "Run a command", "Find windows"].map(label => ({label, reason: "Choose allowed browser sites"}));
  expect(bulkSummary({changed: [1], skipped})).toBe("1 set. 4 left as they were because they need a list first (Open page, Read page, Run a command, …).");
});

it("offers schedules no tighter than five minutes and names them plainly", () => {
  expect(SCHEDULES.every(o => o.minutes === null || o.minutes >= 5)).toBe(true);
  expect(scheduleLabel(null)).toBe("Only when I ask");
  expect(scheduleLabel(60)).toBe("Every hour");
  expect(scheduleLabel(45)).toBe("Every 45 minutes");
});

it("suggests a readable automation name from the script that was approved", () => {
  expect(automationNameFor("scripts/build_drone.py", "drone")).toBe("build drone");
  expect(automationNameFor("backup-notes.PY", "notes")).toBe("backup notes");
  expect(automationNameFor(null, "my project")).toBe("my project");
  expect(automationNameFor("x".repeat(60) + ".py", "p")).toHaveLength(40);
});
