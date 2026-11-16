import type { CSSProperties } from "react";
import type { CrewInstance } from "../types";
import { AgentBot, type BotState } from "./AgentBot";
import { identityColor, identityStyle } from "../lib/identityColors";

function hash(seed: string): number {
  let value = 2166136261;
  for (const ch of seed) value = Math.imul(value ^ ch.charCodeAt(0), 16777619);
  return (value ^ (value >>> 13)) >>> 0;
}

/** How a crew's bot behaves while it roams: busy ones bustle, one that needs you hops, stopped
 *  ones doze. */
export function lifeState(crew: Pick<CrewInstance, "is_running" | "runtime_state" | "status">): BotState {
  if (!crew.is_running || crew.status !== "active") return "stopped";
  if (crew.runtime_state === "needs_input" || crew.runtime_state === "error") return "needs";
  if (crew.runtime_state === "running") return "working";
  return "idle";
}

/** Where a bot wanders: four stops of its own (from its id), and its own pace. */
export function path(seed: string): CSSProperties {
  const h = hash(seed);
  const at = (n: number, lo: number, span: number) => `${lo + ((h >>> (n * 4)) % span)}%`;
  return {
    ["--x0" as string]: at(0, 4, 84), ["--y0" as string]: at(1, 6, 70),
    ["--x1" as string]: at(2, 4, 84), ["--y1" as string]: at(3, 6, 70),
    ["--x2" as string]: at(4, 4, 84), ["--y2" as string]: at(5, 6, 70),
    ["--x3" as string]: at(6, 4, 84), ["--y3" as string]: at(7, 6, 70),
    ["--roam" as string]: `${48 + (h % 30)}s`,
    ["--late" as string]: `-${h % 40}s`,
  };
}

/** The person's crews as small bots wandering behind the chat, each doing what its crew is doing.
 *  Decoration only: never in the way of a click, still for reduced motion. */
export function AgentLife({ crews }: { crews: CrewInstance[] }) {
  const shown = crews.filter(c => c.status === "active").slice(0, 6);
  const bots = shown.length ? shown.map(c => ({ id: c.id, seed: c.id, state: lifeState(c), style: identityStyle(identityColor(`crew:${c.id}`)) }))
    : [{ id: "mavo", seed: "mavo", state: "idle" as BotState, style: { ["--identity" as string]: "var(--accent)" } as CSSProperties }];
  return <div className="agent-life" aria-hidden="true">
    {bots.map(bot => <span key={bot.id} className="life-bot" data-state={bot.state} style={{ ...bot.style, ...path(bot.seed) }}>
      <span className="life-body"><AgentBot seed={bot.seed} state={bot.state} /></span>
    </span>)}
  </div>;
}
