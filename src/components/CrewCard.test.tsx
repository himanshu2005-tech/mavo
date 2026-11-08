import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { CrewArt } from "./CrewArt";
import { CrewCard, triggerLine } from "./CrewCard";

const crew = (over: Partial<CrewInstance>) => ({ id: "c", name: "Meals", template_id: "schedule", status: "active", is_running: true, runtime_state: "waiting", trigger_type: "daily", review_interval_minutes: 30, schedule_time: null, daily_triggers: [], next_run_at: null, last_runtime_error: null, configuration_plan: undefined, ...over }) as unknown as CrewInstance;

describe("Crew cards", () => {
  it("says in one line when a Crew acts", () => {
    expect(triggerLine(crew({ daily_triggers: [{ time: "09:00", message: "", weekdays: [0], enabled: true }, { time: "19:45", message: "", weekdays: [0], enabled: true }] }))).toBe("Daily at 9:00 am, 7:45 pm");
    expect(triggerLine(crew({ trigger_type: "interval", review_interval_minutes: 30 }))).toBe("Every 30 min");
    expect(triggerLine(crew({ trigger_type: "interval", review_interval_minutes: 120 }))).toBe("Every 2 hours");
    expect(triggerLine(crew({ trigger_type: "gmail_message" }))).toBe("When mail arrives");
    expect(triggerLine(crew({ trigger_type: "manual" }))).toBe("When you ask");
  });
  it("draws every kind of Crew and falls back for unknown ones", () => {
    const kinds = ["schedule", "communication", "computer", "coding", "health", "fitness", "learning", "research", "project", "files", "operator", "travel", "home", "wellbeing", "personal-admin", "something-new"];
    for (const kind of kinds) expect(renderToStaticMarkup(<CrewArt template={kind} />)).toContain("<svg");
  });
  it("shows the name, status and the right button", () => {
    const html = renderToStaticMarkup(<CrewCard crew={crew({})} busy={false} onOpen={() => {}} onToggle={() => {}} />);
    expect(html).toContain("Meals");
    expect(html).toContain('aria-label="Stop Meals"');
    expect(renderToStaticMarkup(<CrewCard crew={crew({ is_running: false })} busy={false} onOpen={() => {}} onToggle={() => {}} />)).toContain('aria-label="Run Meals"');
  });
});
