import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ActionRequest, RunApproval } from "../types";
import { WaitingRow, waitingOf } from "./WaitingChanges";

const command = { id: "a1", kind: "command", crew_id: "c1", crew_name: "Disk", created_at: "", sha256: "f".repeat(64), changed: false, trusted: false, project: "", command: "Get-ChildItem C:\ -Directory", folder: "C:\\", reason: "Sizing system folders can take a long time." } as RunApproval;
const change = { id: "a2", crew_id: "c1", crew_name: "Disk", source: "crew", operation: "calendar.create", arguments: {}, status: "pending", target_summary: "Fri 20 Nov, 10:00 AM", result: null, error: null, created_at: "", finished_at: null } as ActionRequest;

it("lists what a crew waits on: a held command with its reason, and other changes", () => {
  const items = waitingOf([command], [change, { ...change, id: "a3", status: "completed" }, { ...change, id: "a1", operation: "terminal.run" }], crew => crew === "c1");
  expect(items.map(i => i.id)).toEqual(["a1", "a2"]);
  expect(items[0]).toMatchObject({ title: "Run a command", detail: "Get-ChildItem C:\ -Directory", reason: "Sizing system folders can take a long time." });
  expect(items[1].detail).toBe("Fri 20 Nov, 10:00 AM");
  expect(waitingOf([command], [change], crew => crew === "other")).toEqual([]);
});

it("each with its own two buttons: Run for a command, Approve for a change, and Deny", () => {
  const [held, other] = waitingOf([command], [change], () => true);
  const run = renderToStaticMarkup(<WaitingRow item={held} onDone={() => undefined} />);
  expect(run).toContain(">Run<");
  expect(run).toContain(">Deny<");
  expect(run).toContain("Get-ChildItem C:\ -Directory");
  expect(renderToStaticMarkup(<WaitingRow item={other} onDone={() => undefined} />)).toContain(">Approve<");
  const changed = renderToStaticMarkup(<WaitingRow item={{ ...held, run: { ...command, changed: true } }} onDone={() => undefined} />);
  expect(changed).toMatch(/<button[^>]*disabled[^>]*>Run</);  // a command that changed is never run unseen
});
