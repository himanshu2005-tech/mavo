import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { AgentLife, lifeState, path } from "./AgentLife";

const crew = (id: string, extra: Partial<CrewInstance> = {}) => ({ id, name: id, status: "active", is_running: true, runtime_state: "waiting", ...extra } as CrewInstance);

it("each crew's bot does what its crew does", () => {
  expect(lifeState(crew("a", { runtime_state: "running" }))).toBe("working");
  expect(lifeState(crew("a", { runtime_state: "needs_input" }))).toBe("needs");
  expect(lifeState(crew("a", { is_running: false }))).toBe("stopped");
  expect(lifeState(crew("a"))).toBe("idle");
});

it("each bot keeps its own path, inside the window", () => {
  expect(path("crew-1")).toEqual(path("crew-1"));
  expect(path("crew-1")).not.toEqual(path("crew-2"));
  for (const value of Object.entries(path("x")).filter(([k]) => k.startsWith("--x") || k.startsWith("--y")).map(([, v]) => parseInt(String(v)))) {
    expect(value).toBeGreaterThanOrEqual(4);
    expect(value).toBeLessThan(90);
  }
});

it("shows up to six active crews, or Mavo alone, and stays out of the way", () => {
  const many = renderToStaticMarkup(<AgentLife crews={Array.from({ length: 8 }, (_, i) => crew(`c${i}`))} />);
  expect(many.match(/class="life-bot"/g)).toHaveLength(6);
  expect(many).toContain('aria-hidden="true"');
  expect(renderToStaticMarkup(<AgentLife crews={[crew("d", { status: "draft" })]} />).match(/class="life-bot"/g)).toHaveLength(1);
});
