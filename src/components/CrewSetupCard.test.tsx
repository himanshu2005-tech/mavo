import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { CrewSetupCard } from "./CrewSetupCard";
import { CrewReplyBox } from "./CrewReplyBox";

describe("existing manual Crew without a coordinator plan", () => {
  const crew = {
    id: "manual-crew", permissions: ["model:invoke"], configuration_plan: {},
  } as CrewInstance;

  it("renders without treating the database's empty JSON object as a setup plan", () => {
    expect(renderToString(<CrewSetupCard crew={crew} />)).toBe("");
  });

  it("keeps the Crew message box available", () => {
    expect(renderToString(<CrewReplyBox crew={crew} />)).toContain("Message this Crew");
  });
});

describe("skipping the setup test", () => {
  const draft = { name: "Booker", responsibilities: "Book meetings.", template_id: "schedule", trigger_type: "manual", requested_google_capabilities: [], requested_google_resources: [], requested_actions: [], requested_notifications: false, requested_device_metrics: false, daily_call_limit: 10, max_model_calls: 5 };
  const crew = (plan: Record<string, unknown>, status = "draft") => ({
    id: "c1", name: "Booker", status, template_id: "schedule", trigger_type: "manual", responsibilities: "Book meetings.", permissions: ["model:invoke"], agents: [],
    configuration_plan: { phase: "needs_access", questions: [], missing: ["Complete first-run verification before adding this Crew"], draft, ...plan },
  } as unknown as CrewInstance);

  it("offers to skip it, and says an agent that acts will ask first", () => {
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: true })} />);
    expect(html).toContain("Skip test");
    expect(html).toContain("If you skip it, it asks you before its first 3 changes.");
  });

  it("says nothing more for an agent that only reads", () => {
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: false })} />);
    expect(html).toContain("Skip test");
    expect(html).not.toContain("asks before");
  });

  it("while it tests, says only Testing…: no example, nothing to press again", () => {
    const sample = { gmail: [{ sender: "Asha <asha@example.com>", subject: "Quick sync?", text: "Free at 10:00 or 14:00?" }] };
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "running", phase: "writing_sample", message: "Writing a sample email to test it with…", tested_with: "sample", sample } })} />);
    expect(html).toContain("Testing…");
    expect(html).not.toContain("Quick sync?");
    expect(html).not.toContain("sample");
    expect(html).not.toContain("Skip test");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>.*Testing…/);
  });

  it("after a test on an example, says how it went, not the example", () => {
    const sample = { gmail: [{ sender: "Asha <asha@example.com>", subject: "Quick sync?", text: "Free at 10:00?" }] };
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "passed", message: "", tested_with: "sample", sample, preview: "Booked 10:00 with Asha." }, missing: [] })} />);
    expect(html).toContain("Test passed.");
    expect(html).not.toContain("Quick sync?");
    expect(html).not.toContain("Booked 10:00");
  });

  it("a workflow's scenarios: how many passed, each one behind Tests", () => {
    const tests = [{ name: "Second offered time free", passed: true, problems: [] }, { name: "Every time busy", passed: false, problems: ["calendar.create: did 1, should do 0"] }];
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "passed", kind: "workflow", message: "All 2 tests passed.", tests }, missing: [] })} />);
    expect(html).toContain("All 2 tests passed.");
    expect(html).toContain("<summary>Tests</summary>");
    expect(html).toContain("✗ Every time busy");
    expect(html).toContain("calendar.create: did 1, should do 0");
  });

  it("granting is one step: everything Mavo decided it needs is listed and on, the details fold away", () => {
    const workflow_draft = { final: "desk", steps: [{ id: "read_gmail", kind: "read", source: "gmail" }, { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail"] }] };
    const asks = { ...draft, requested_google_resources: ["gmail", "calendar"], requested_actions: [{ operation: "gmail.send" }, { operation: "calendar.create" }] };
    const html = renderToString(<CrewSetupCard crew={{ ...crew({ changes_things: true, workflow_draft }), configuration_plan: { phase: "needs_access", questions: [], missing: [], draft: asks, changes_things: true, workflow_draft } } as unknown as CrewInstance} />);
    expect(html).toContain("It will be allowed to");
    for (const item of ["Gmail: Read inbox headers", "Calendar: Read events"]) expect(html).toContain(item);
    expect(html.match(/<li(?![^>]*data-off)[^>]*><svg/g)?.length).toBeGreaterThanOrEqual(4);  // reads and both actions, all on
    expect(html).not.toContain("data-off");
    expect(html).toContain("<summary>Change what it can do</summary>");
  });

  it("asks each thing once: reading here, sending and booking with the actions", () => {
    const asks = { ...draft, requested_google_resources: ["gmail", "calendar"], requested_google_capabilities: ["gmail:read", "gmail:send", "calendar:read", "calendar:write"], requested_actions: [{ operation: "gmail.send" }, { operation: "calendar.create" }] };
    const html = renderToString(<CrewSetupCard crew={{ ...crew({ changes_things: true }), configuration_plan: { phase: "needs_access", questions: [], missing: [], draft: asks, changes_things: true } } as unknown as CrewInstance} />);
    expect(html).toContain("Read inbox headers");
    expect(html).not.toContain("Send emails");  // the Send email action carries it
    expect(html).not.toContain("Create, edit, and delete events");
    expect(html).toContain("<summary>Change what it can do</summary>");  // folded even before its plan
  });

  it("a crew run by the guarded agent is turned on directly, with no test of its own", () => {
    const workflow_draft = { final: "desk", steps: [{ id: "read_gmail", kind: "read", source: "gmail" }, { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail"] }] };
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: true, workflow_draft })} />);
    expect(html).toContain("Turn it on");
    expect(html).toContain("Decides what to do");
    expect(html).not.toContain("Test it first");
  });

  it("a crew that books shows its hours in one line, with Change", () => {
    const workflow_draft = { final: "desk", steps: [{ id: "read_gmail", kind: "read", source: "gmail" }, { id: "read_calendar", kind: "read", source: "calendar" }, { id: "desk", kind: "automation", automation: "agent.desk", uses: ["read_gmail"], params: { start: "09:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"], duration_minutes: 45 } }] };
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: true, workflow_draft })} />);
    expect(html).toContain("Mon–Fri, 9:00 AM–6:00 PM, your time zone · 45 min meetings");
    expect(html).toContain(">Change</button>");
    const noCalendar = { ...workflow_draft, steps: workflow_draft.steps.filter(s => s.source !== "calendar") };
    expect(renderToString(<CrewSetupCard crew={crew({ changes_things: true, workflow_draft: noCalendar })} />)).not.toContain("your time zone");
  });

  it("once its path passed its tests, the card draws the path instead of the agent list", () => {
    const recipe = { final: "send", steps: [{ id: "mail", kind: "read", source: "gmail" }, { id: "send", kind: "act", operation: "gmail.send", args_from: "mail" }] };
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "passed", kind: "workflow", message: "All 8 tests passed." }, recipe, recipe_tested: true, missing: [] })} />);
    expect(html).toContain("Your mail");
    expect(html).toContain("Send the email");
  });

  it("shows the path Core wrote while its tests run, not only once they pass", () => {
    const workflow_draft = { final: "send", steps: [{ id: "mail", kind: "read", source: "gmail" }, { id: "send", kind: "act", operation: "gmail.send", args_from: "mail" }] };
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "running", phase: "testing", kind: "workflow", message: "" }, workflow_draft })} />);
    expect(html).toContain("Testing…");
    expect(html).toContain("Send the email");
    expect(html).not.toContain("3 agents");
  });

  it("says while Core writes the whole path of a draft that changes things", () => {
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: true, workflow_writing: "2026-11-14T12:00:00" })} />);
    expect(html).toContain("Writing its full path, every case it handles…");
  });

  it("a failed test says what email it met, what it should do, what it did and where it stopped", () => {
    const tests = [{ name: "Accepted time is booked", passed: false, problems: ["Bookings: did 0, expected 1"], email: "From Meera: Re: Interview", expected: ["Book Mon 16 Nov 10:00", "Send an email to meera@example.com"], did: ["Nothing"], stopped: ["Understand each email: thread_id is missing"] }];
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "failed", kind: "workflow", message: "0 of 1 tests passed.", tests } })} />);
    for (const text of ["From Meera: Re: Interview", "Should: Book Mon 16 Nov 10:00, Send an email to meera@example.com", "Did: Nothing", "Stopped at Understand each email: thread_id is missing"]) expect(html).toContain(text);
  });

  it("reassures the person while it tries different emails", () => {
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "running", phase: "testing", kind: "workflow", message: "", done: 3, total: 12 } })} />);
    expect(html).toContain("Testing… 3 of 12");
    expect(html).toContain("Sit back. Mavo is trying this crew on different emails so it handles real ones well.");
  });

  it("most tests passed: names the cases it may get wrong and lets the person turn it on anyway", () => {
    const tests = [{ name: "Free time", passed: true, problems: [] }, { name: "Busy time", passed: false, problems: ["x"] }, { name: "Cancel", passed: true, problems: [] }];
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "partial", kind: "workflow", message: "Passed 2 of 3 tests. It may not handle some cases well.", tests } })} />);
    expect(html).toContain("Passed 2 of 3 tests. It may not handle some cases well.");
    expect(html).toContain("It may not handle these well:");
    expect(html).toContain("<li>Busy time</li>");
    expect(html).toContain("Turn it on anyway");
  });

  it("under half passed: not recommended, and no turning it on anyway", () => {
    const tests = [{ name: "Free time", passed: false, problems: ["x"] }, { name: "Busy time", passed: false, problems: ["x"] }, { name: "Cancel", passed: true, problems: [] }];
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "failed", kind: "workflow", message: "Passed 1 of 3 tests. Not recommended to turn on yet.", tests } })} />);
    expect(html).toContain("Not recommended to turn on yet.");
    expect(html).not.toContain("Turn it on anyway");
    expect(html).not.toContain("The test didn’t work");
  });

  it("lets the person choose how thoroughly to test a crew that changes things", () => {
    const workflow_draft = { final: "send", steps: [{ id: "mail", kind: "read", source: "gmail" }, { id: "send", kind: "act", operation: "gmail.send", args_from: "mail" }] };
    const html = renderToString(<CrewSetupCard crew={crew({ changes_things: true, test_level: "quick", workflow_draft })} />);
    for (const text of ["Turn it on", "Test it first", "Quick", "Standard", "Thorough", "4 test emails, 1 fix round. Fastest and cheapest.", "Start the test"]) expect(html).toContain(text);
    expect(html).not.toContain("Skip test");
    const writing = renderToString(<CrewSetupCard crew={crew({ changes_things: true, workflow_writing: "2026-11-14T12:00:00" })} />);
    expect(writing).toContain("Writing its plan…");
    expect(renderToString(<CrewSetupCard crew={crew({ changes_things: false })} />)).not.toContain("Thorough");
  });

  it("tells the person it was turned on without a test", () => {
    const html = renderToString(<CrewSetupCard crew={crew({ first_run: { status: "skipped", message: "" }, ask_first: { since: "x", count: 3 }, missing: [] }, "active")} />);
    expect(html).toContain("Turned on without a test.");
    expect(html).toContain("It asks you before its first 3 changes.");
    expect(html).not.toContain("Skip test");
  });
});

describe("a crew that reads a website", () => {
  const draft = { name: "Credit Meter", responsibilities: "Check credits on airouter.in every hour.", template_id: "research", trigger_type: "interval", requested_google_capabilities: [], requested_google_resources: [], requested_actions: [{ operation: "browser.open", domains: ["airouter.in"] }, { operation: "browser.read", domains: ["airouter.in"] }], requested_notifications: true, requested_device_metrics: false, daily_call_limit: 10, max_model_calls: 5 };
  const crew = (plan: Record<string, unknown>) => ({
    id: "c2", name: "Credit Meter", status: "draft", template_id: "research", trigger_type: "interval", responsibilities: draft.responsibilities, permissions: ["model:invoke"], agents: [],
    configuration_plan: { phase: "needs_access", questions: [], missing: ["Allow desktop updates"], draft, ...plan },
  } as unknown as CrewInstance);

  it("does not ask to allow desktop updates when its switch is already on", () => {
    expect(renderToString(<CrewSetupCard crew={crew({})} />)).not.toContain("Allow desktop updates");
  });

  it("one way to test it: no Try it first, a quiet link to sign in to its site", () => {
    const html = renderToString(<CrewSetupCard crew={crew({})} />);
    expect(html).not.toContain("Try it first");
    expect(html).toContain("Sign in to airouter.in");
    expect(html).toContain("<summary>More</summary>");
    expect(html).not.toContain("understood-label");
  });
});

describe("the moment a test passes", () => {
  const draft = { name: "Inbox Watch", responsibilities: "Watch my inbox.", template_id: "research", trigger_type: "interval", requested_google_capabilities: [], requested_google_resources: [], requested_actions: [], requested_notifications: true, requested_device_metrics: false, daily_call_limit: 48, max_model_calls: 5 };
  const crew = (status: string) => ({ id: "celebrate-1", name: "Inbox Watch", status: "active", template_id: "research", trigger_type: "interval", responsibilities: "Watch my inbox.", permissions: ["model:invoke"], agents: [],
    configuration_plan: { phase: "needs_access", questions: [], missing: [], draft, first_run: { status, message: "" } } } as unknown as CrewInstance);

  it("celebrates when it sees the test go from running to passed, and not otherwise", () => {
    expect(renderToString(<CrewSetupCard crew={crew("passed")} />)).not.toContain("celebrate");  // never seen running
    renderToString(<CrewSetupCard crew={crew("running")} />);
    const html = renderToString(<CrewSetupCard crew={crew("passed")} />);
    expect(html).toContain('class="celebrate"');
    expect(html).toContain("It works");
    expect(html).toContain("Inbox Watch works on its own from now.");
    expect(html).toContain("Test passed.");  // the result line stays, with its details
  });
});
