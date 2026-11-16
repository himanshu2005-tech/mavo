import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { AgentRoom } from "./AgentRoom";
import { Rooms, roomLine, roomOrder, waitingCount } from "./Rooms";

const crew = (id: string, extra: Partial<CrewInstance> = {}) => ({
  id, name: id, status: "active", is_running: true, runtime_state: "waiting", trigger_type: "interval", review_interval_minutes: 60,
  template_id: "research", responsibilities: `Job of ${id}`, permissions: [], agents: [], configuration_plan: {}, daily_calls_used: 0, daily_triggers: [], ...extra,
} as unknown as CrewInstance);

const asking = crew("Meeting Desk", { asks: [{ id: "r1", question: "Thursday 2 PM is busy. What should I do?", choices: ["Offer other times", "Decline"] }] });
const credits = crew("Credits Watch", { checkpoint: "Credits left: \\textbf{\\$0.50}\n- Last top-up: Nov 14" });
const draft = crew("Price Watch", { status: "draft" } as Partial<CrewInstance>);

it("puts what needs you first, then the other agents, then drafts", () => {
  const order = roomOrder([credits, asking, draft], []);
  expect(order.needs.map(c => c.id)).toEqual(["Meeting Desk"]);
  expect(order.rest.map(c => c.id)).toEqual(["Credits Watch"]);
  expect(order.drafts.map(c => c.id)).toEqual(["Price Watch"]);
  expect(waitingCount(asking)).toBe(1);
  expect(waitingCount(credits)).toBe(0);
});

it("says in one line what each agent is up to: its question, or what it last found", () => {
  expect(roomLine(asking)).toBe("Thursday 2 PM is busy. What should I do?");
  expect(roomLine(credits)).toBe("Credits left: $0.50");  // its report's first line, without markup
  expect(roomLine(draft)).toBe("Setting up");
  expect(roomLine(crew("New", {}))).toBe("Waiting for its first result");
});

it("lists Mavo first, then the rooms by what they need, with a count and the map one press away", () => {
  const html = renderToStaticMarkup(<Rooms crews={[credits, asking]} drafts={[]} onOverview={() => undefined} />);
  expect(html.indexOf("<b>Mavo</b>")).toBeLessThan(html.indexOf("Needs you"));
  expect(html.indexOf("Needs you")).toBeLessThan(html.indexOf("<b>Meeting Desk</b>"));
  expect(html.indexOf("<b>Meeting Desk</b>")).toBeLessThan(html.indexOf("<b>Credits Watch</b>"));
  expect(html).toContain('aria-label="1 waiting for you"');
  expect(html).toContain(">Map<");
  const narrowed = renderToStaticMarkup(<Rooms crews={[credits, asking]} drafts={[]} onOverview={() => undefined} query="credits" />);
  expect(narrowed).toContain("<b>Credits Watch</b>");
  expect(narrowed).not.toContain("<b>Meeting Desk</b>");
});

it("an agent's room: its timeline, what waits for you with its answers, and the box last", () => {
  const html = renderToStaticMarkup(<AgentRoom crew={asking} onSettings={() => undefined} />);
  expect(html).toContain("<h2>Meeting Desk</h2>");
  expect(html).toContain('aria-label="It needs you"');
  expect(html).toContain(">Offer other times<");
  expect(html).toContain('aria-label="Its settings"');
  expect(html.indexOf("It needs you")).toBeLessThan(html.indexOf('aria-label="Ask Meeting Desk"'));
});
