import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CrewFan, agentHue, bubbleFor, nearestPairs, nerve, scatterLayout, talks } from "./CrewFan";
import type { CrewInstance } from "../types";

const W = 1920, H = 1032, CORE = { x: 1862, y: 960 };
const ids = (n: number) => Array.from({ length: n }, (_, i) => `crew-${i}-${(i * 7919).toString(36)}`);

it("spreads a hundred agents over the screen, inside it, never one on another", () => {
  const spots = scatterLayout(ids(100), W, H, CORE).map(s => ({ x: CORE.x + s.x, y: CORE.y + s.y }));
  const b = bubbleFor(100);
  expect(b).toBe(32);
  for (const s of spots) {
    expect(s.x).toBeGreaterThanOrEqual(b / 2);
    expect(s.x).toBeLessThanOrEqual(W - b / 2);
    expect(s.y).toBeGreaterThanOrEqual(b / 2);
    expect(s.y).toBeLessThanOrEqual(H - b / 2);
  }
  let closest = Infinity;
  spots.forEach((a, i) => spots.forEach((c, j) => { if (i < j) closest = Math.min(closest, Math.hypot(a.x - c.x, a.y - c.y)); }));
  expect(closest).toBeGreaterThan(b); // badges never overlap
  // Spread wide: from near the icon to the far side of the screen.
  expect(Math.min(...spots.map(s => s.x))).toBeLessThan(W * 0.25);
  expect(Math.min(...spots.map(s => s.y))).toBeLessThan(H * 0.25);
});

it("keeps each agent in its own spot, and a small team near the icon", () => {
  const once = scatterLayout(ids(5), W, H, CORE);
  expect(scatterLayout(ids(5), W, H, CORE)).toEqual(once); // the same agents land in the same places
  for (const s of once) expect(Math.hypot(s.x, s.y)).toBeLessThanOrEqual(200 + 200 * Math.sqrt(5));
  expect(bubbleFor(3)).toBe(44);
});

it("joins each agent to its nearest neighbour once, for the brain's mesh", () => {
  expect(nearestPairs([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 100, y: 0 }])).toEqual([[0, 1], [1, 2]]);
});

it("gives each agent a stable individual identity rather than recycling its template color", () => {
  const a = agentHue({ id: "news-1", template_id: "research" });
  const b = agentHue({ id: "jobs-2", template_id: "research" });
  expect(a).not.toBe(b);
  expect(a).toBeGreaterThanOrEqual(0);
  expect(a).toBeLessThan(360);
  expect(agentHue({id:'news-1',template_id:'research'})).toBe(a);
});

it("draws every agent, and a synapse only where one really hands work to another", () => {
  const crews = [
    { id: "inbox", configuration_plan: {} },
    { id: "jobs", configuration_plan: { upstream: ["inbox", "not-shown"] } },
    { id: "news", configuration_plan: {} },
  ] as unknown as CrewInstance[];
  expect(talks(crews)).toEqual([{ from: 0, to: 1 }]);
  expect(nerve(0, 0, 100, 0, 0.1)).toBe("M0.0,0.0 Q50.0,10.0 100.0,0.0");
  const many = Array.from({ length: 60 }, (_, i) => ({ id: "c" + i, name: "Watch " + i, template_id: "research", status: "active", is_running: true, runtime_state: i === 0 ? "working" : "waiting", trigger_type: "interval", next_run_at: null, configuration_plan: i === 1 ? { upstream: ["c0"] } : {} }) as unknown as CrewInstance);
  const markup = renderToStaticMarkup(<CrewFan initial={many} width={W} height={H} core={CORE} onList={() => undefined} onOpen={() => undefined} onClose={() => undefined} />);
  expect(markup.match(/class="fan-agent"/g)).toHaveLength(60); // no "+N" until 150
  expect(markup.match(/class="brain-talk"/g)).toHaveLength(1);
  expect(markup.match(/class="brain-signal"/g)!.length).toBeLessThanOrEqual(25); // signals capped
  expect(markup).toContain('class="fan-backdrop"');
});

it("says what each agent does in one plain line, without its schedule", async () => {
  const { doesLine } = await import("./CrewFan");
  expect(doesLine("Every 3 hours: clear the Windows temp folder of old files. Then report the free space.")).toBe("Clear the Windows temp folder of old files.");
  expect(doesLine("Whenever Lunch Reminder goes off, put the laptop to sleep.")).toBe("Put the laptop to sleep.");
  expect(doesLine("Read my inbox and group new mail by importance " + "very ".repeat(40))).toMatch(/…$/);
});
