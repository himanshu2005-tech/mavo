import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AgentBot, botParts, orbHues, paletteStyle } from "./AgentBot";

it("an agent always gets the same bot, and agents rarely look alike", () => {
  expect(renderToStaticMarkup(<AgentBot seed="crew-1" />)).toBe(renderToStaticMarkup(<AgentBot seed="crew-1" />));
  const looks = new Set(Array.from({ length: 60 }, (_, i) => JSON.stringify({ ...botParts(`crew-${i}`), blink: 0 })));
  expect(looks.size).toBeGreaterThan(30);
});

it("the orb shows what the agent is doing, in its own colours, and Mavo has its own", () => {
  const working = renderToStaticMarkup(<AgentBot seed="a" state="working" />);
  expect(working).toContain('data-state="working"');
  expect(working).toContain("agent-orb");
  expect(renderToStaticMarkup(<AgentBot seed="a" state="needs" />)).toContain("orb-call"); // amber glow
  expect(renderToStaticMarkup(<AgentBot seed="a" state="ready" />)).not.toContain("orb-call");
  // Two agents mix different colours; the same agent always the same.
  expect(orbHues("crew-1")).toEqual(orbHues("crew-1"));
  expect(orbHues("crew-1")).not.toEqual(orbHues("crew-2"));
  // Mavo itself: its own aurora orb, not an agent's.
  const mavo = renderToStaticMarkup(<AgentBot seed="mavo" state="ready" />);
  expect(mavo).toContain("mavo-orb");
  expect(mavo).toContain("mavo-ribbons");
});

it("a bot is decoration unless it is given a name", () => {
  expect(renderToStaticMarkup(<AgentBot seed="a" />)).toContain('aria-hidden="true"');
  expect(renderToStaticMarkup(<AgentBot seed="a" label="Inbox Summary" />)).toContain('aria-label="Inbox Summary"');
});

it("a library agent wears one of Mavo's six colours", () => {
  expect(String(Object.values(paletteStyle("research"))[0])).toMatch(/^var\(--c-(blue|violet|teal|orange|amber|rose)\)$/);
});
