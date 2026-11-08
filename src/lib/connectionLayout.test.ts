import { expect, it } from "vitest";
import { attachConnections, frameAgents } from "./connectionLayout";
import { radialLayout } from "./radialGraph";

it("attaches graph endpoints to dock rows without changing radial agent positions", () => {
  const base = radialLayout(["a", "b"], [{ key: "gmail", users: ["a"] }], 0);
  const view = { x: 260, y: 50, k: .45 };
  const result = attachConnections(base, view, { gmail: { x: 240, y: 180 } });
  expect(result.nodes.gmail.x * view.k + view.x).toBeCloseTo(240);
  expect(result.nodes.gmail.y * view.k + view.y).toBeCloseTo(180);
  expect(result.agents).toBe(base.agents);
  expect(result.nodes).not.toBe(base.nodes);
  expect(base.nodes.gmail).not.toEqual(result.nodes.gmail);
});

it("keeps 2 through 500 agents in the area beside the connections box", () => {
  for (const count of [2,14,50,500]) {
    const base = radialLayout(Array.from({ length:count }, (_,i) => `a${i}`), [], 0);
    for (const width of [920,1280,1920]) {
      const left = 260, view = frameAgents(base, left, width-left, 740);
      for (const p of Object.values(base.agents)) {
        expect(view.x+p.x*view.k).toBeGreaterThanOrEqual(left+100);
        expect(view.x+p.x*view.k).toBeLessThanOrEqual(width-100);
        expect(view.y+p.y*view.k).toBeGreaterThanOrEqual(50);
        expect(view.y+p.y*view.k).toBeLessThanOrEqual(740-100);
      }
    }
  }
});
