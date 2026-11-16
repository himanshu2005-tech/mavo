import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { CrewDetail } from "./CrewDetail";

it("an agent's waiting question can be answered in its own window, not only on the alert", () => {
  const crew = { id: "c1", name: "Meeting Desk", status: "active", is_running: true, runtime_state: "waiting", trigger_type: "gmail_message", template_id: "research", responsibilities: "Book meetings.", permissions: [], agents: [], configuration_plan: {}, daily_calls_used: 0, daily_triggers: [],
    asks: [{ id: "r1", question: "Ravi: Roadmap: Thursday 2 PM is busy. What should I do?", choices: ["Offer other times", "Decline politely"] }] } as unknown as CrewInstance;
  const html = renderToStaticMarkup(<CrewDetail crew={crew} />);
  expect(html).toContain("Thursday 2 PM is busy. What should I do?");
  expect(html).toContain(">Offer other times<");
  expect(html).toContain('placeholder="Or in your words…"');
  expect(renderToStaticMarkup(<CrewDetail crew={{ ...crew, asks: [] }} />)).not.toContain("It asks you");
});
