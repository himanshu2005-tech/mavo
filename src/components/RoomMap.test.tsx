import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { CrewInstance, GoogleStatus } from "../types";
import { NeedsAccess, accessNeeds, needsAccess } from "./NeedsAccess";
import { RoomMap, mapNodes, progressLines } from "./RoomMap";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const crew = (over: Partial<CrewInstance> = {}) => ({
  id: "c1", name: "Priority Inbox Watch", runtime_state: "needs_setup", permissions: ["model:invoke"],
  configuration_plan: { missing: ["Allow gmail:read", "Allow gmail:body", "Allow desktop updates"] },
  ...over,
}) as unknown as CrewInstance;

const google = (status: Partial<GoogleStatus>) => ({
  status: { signed_in: true, capabilities: ["gmail:read", "gmail:body"], phase: "idle", ...status } as GoogleStatus,
  busy: false, pending: false, error: null, setError: () => undefined, refresh: async () => status as GoogleStatus,
  connect: async () => undefined, cancel: async () => undefined,
});

it("reads what an agent waits on from its setup's own list", () => {
  const needs = accessNeeds(crew({ configuration_plan: { missing: ["Allow gmail:read", "Allow gmail.send", "Allow desktop updates", "Connect the required Google services"] } } as Partial<CrewInstance>));
  expect(needs.reads).toEqual(["gmail:read"]);
  expect(needs.alerts).toBe(true);
  expect(needs.connect).toBe(true);
  expect(needs.other).toEqual(["Allow gmail.send"]);  // acting stays in its settings
});

it("asks for an OK with one Allow button when Google is connected", () => {
  const html = renderToStaticMarkup(<NeedsAccess crew={crew()} google={google({})} onSettings={() => undefined} />);
  expect(html).toContain("It needs your OK");
  expect(html).toContain("Read email contents");
  expect(html).toContain(">Allow</button>");
  expect(html).not.toContain("Connect Google");
});

it("offers Connect Google for a service not connected yet, and Reconnect when Google ended the sign-in", () => {
  const connect = renderToStaticMarkup(<NeedsAccess crew={crew()} google={google({ capabilities: [] })} onSettings={() => undefined} />);
  expect(connect).toContain("Connect Google");
  expect(connect).not.toContain(">Allow</button>");
  const expired = google({ expired: true });
  const live = crew({ runtime_state: "waiting", configuration_plan: { missing: [] } } as unknown as Partial<CrewInstance>);
  expect(needsAccess(live, expired.status)).toBe(true);
  expect(needsAccess(live, google({}).status)).toBe(false);
  const html = renderToStaticMarkup(<NeedsAccess crew={live} google={expired} onSettings={() => undefined} />);
  expect(html).toContain("Google access expired");
  expect(html).toContain("Reconnect Google");
});

it("draws the steps it is on, with what it is doing now under the current one", () => {
  const live = crew({ cycle: { run_id: "r", status: "running", started_at: "2026-11-15T13:17:00Z", current: 0, done: 0, total: 2, question: null,
    asked: "Give me inbox summary",
    steps: [{ title: "Read inbox", status: "running" }, { title: "Send summary", status: "waiting" }],
    thoughts: [{ label: "", text: "Reading your Gmail" }, { label: "", text: "Read 14 emails" }, { label: "", text: "Writing it up" }] } } as unknown as Partial<CrewInstance>);
  expect(mapNodes(live).map(n => n.status)).toEqual(["running", "waiting"]);
  expect(progressLines(live.cycle!.thoughts)).toEqual(["Reading your Gmail", "Read 14 emails", "Writing it up"]);
  const html = renderToStaticMarkup(<RoomMap crew={live} />);
  expect(html).toContain("Answering");
  expect(html).toContain("Give me inbox summary");
  expect(html).toContain("Read 14 emails");
  expect(html).toMatch(/data-now="true"[^>]*>.*Writing it up/);
});

it("between runs, shows the path it follows", () => {
  const idle = crew({ cycle: null, configuration_plan: { recipe: { steps: [{ id: "r", kind: "read", source: "gmail" }, { id: "s", kind: "agent", title: "Summarise" }] } } } as unknown as Partial<CrewInstance>);
  expect(mapNodes(idle)).toEqual([{ title: "Your mail", status: "waiting" }, { title: "Summarise", status: "waiting" }]);
  expect(renderToStaticMarkup(<RoomMap crew={idle} />)).toContain(">Steps<");
});
