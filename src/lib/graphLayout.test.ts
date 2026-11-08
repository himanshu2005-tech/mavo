import { describe, expect, it } from "vitest";
import { layoutGraph } from "./graphLayout";

const n = (id: string) => ({ id, name: id.toUpperCase() });

describe("Crew graph layout", () => {
  it("places each Crew one column right of its deepest upstream Crew", () => {
    const g = layoutGraph([n("a"), n("b"), n("c")], [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "a", to: "c" }]);
    const depth = Object.fromEntries(g.nodes.map(x => [x.id, x.depth]));
    expect(depth).toEqual({ a: 0, b: 1, c: 2 });
    expect(g.nodes.find(x => x.id === "c")!.x).toBeGreaterThan(g.nodes.find(x => x.id === "b")!.x);
  });
  it("stacks parallel Crew in one column without overlap", () => {
    const g = layoutGraph([n("a"), n("b"), n("c")], [{ from: "a", to: "b" }, { from: "a", to: "c" }]);
    const [b, c] = ["b", "c"].map(id => g.nodes.find(x => x.id === id)!);
    expect(b.x).toBe(c.x);
    expect(Math.abs(b.y - c.y)).toBeGreaterThanOrEqual(48);
  });
  it("ignores unknown or self edges and survives a cycle", () => {
    const g = layoutGraph([n("a"), n("b")], [{ from: "a", to: "ghost" }, { from: "a", to: "a" }, { from: "a", to: "b" }, { from: "b", to: "a" }]);
    expect(g.nodes).toHaveLength(2);
    expect(g.edges.every(e => e.from !== e.to)).toBe(true);
    expect(Number.isFinite(g.width) && Number.isFinite(g.height)).toBe(true);
  });
});
