/** Where things sit on Mavo's graph: you in the middle, what you told Mavo around you, your agents
 *  in a ring, and what they read and can do outside it, each near the agents that use it. */

export type Point = { x: number; y: number };
const TAU = Math.PI * 2;
const wrap = (a: number) => ((a % TAU) + TAU) % TAU;

/** An agent's initials: "Inbox Summary" is IS ("the", "my", "a" and symbols like "&" are
 *  skipped: "Accessibility & UX reviewer" is AU, not A&). */
export function initials(name: string): string {
  const words = name.split(/\s+/).filter(w => w && /[\p{L}\p{N}]/u.test(w) && !/^(the|my|a|an)$/i.test(w));
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

/** A person's initials: "Himanshu" is H, "Himanshu Madhunala" is HM. */
export function myInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] ?? "?")[0]).toUpperCase();
}

/** Spreads angles that want to sit at `want` so neighbours are at least `gap` apart, keeping each
 *  as near its wish as it can. */
export function spread(want: number[], gap: number): number[] {
  const n = want.length;
  if (n < 2) return want.map(wrap);
  gap = Math.min(gap, TAU / n);
  const order = want.map((a, i) => ({ a: wrap(a), i }));
  for (let round = 0; round < 200; round++) {
    let moved = false;
    order.sort((p, q) => p.a - q.a);
    for (let k = 0; k < n; k++) {
      const p = order[k], q = order[(k + 1) % n];
      const d = k === n - 1 ? wrap(q.a - p.a) || TAU : q.a - p.a;
      if (d < gap - 1e-6) { const push = (gap - d) / 2; p.a = wrap(p.a - push); q.a = wrap(q.a + push); moved = true; }
    }
    if (!moved) break;
  }
  const out = new Array<number>(n);
  order.forEach(o => { out[o.i] = o.a; });
  return out;
}

export interface GraphLayout {
  W: number; H: number; c: Point;
  agents: Record<string, Point>;
  /** Where a line from an agent bends, so lines run around the map, not across the middle. */
  bends: Record<string, Point>;
  nodes: Record<string, Point>;
  /** Shared by many agents: these sit on the inner ring around you, not at the edge. */
  hubs: string[];
  /** How many rings the agents take (one for a few, a dozen for three hundred). */
  rings: number;
  memory: Point[];
  /** One-time task teams: the task, and its members around it, outside everything else. */
  tasks: { hub: Point; members: Point[] }[];
  newAgent: Point;
  pool: { rx: number; ry: number };
}

const SX = 1.45, SY = 0.8; // a wide oval: screens are wide
const SLOT = 165; // room one agent card takes along its ring
const RING_GAP = 150; // between rings: at the oval's sides that is 1.45 x 150 = 218px, wider than a card
const capacity = (r: number) => Math.max(6, Math.floor((TAU * r * 1.1) / SLOT));

/** Is this shared by enough agents to sit in the middle with you? A thing most agents use
 *  (desktop alerts, say) drawn at the edge sends a line across the whole map from every agent. */
export function isHub(users: number, agents: number): boolean {
  return agents >= 4 && users >= 3 && (users >= 6 || users / agents >= 0.25);
}

/** How many agents go on each ring: as many rings as it takes, each holding its share by size,
 *  so the outermost is not left with a lonely few. */
export function ringCounts(n: number, first: number): { radius: number; count: number }[] {
  const rings: { radius: number; cap: number }[] = [];
  let room = 0;
  for (let r = first; room < n || !rings.length; r += RING_GAP) { const cap = capacity(r); rings.push({ radius: r, cap }); room += cap; }
  let left = n;
  return rings.map((ring, i) => {
    const count = i === rings.length - 1 ? left : Math.min(left, Math.round((n * ring.cap) / room));
    left -= count;
    return { radius: ring.radius, count };
  });
}

/** `agentIds` in ring order (a chain stays together); `outer` the resources and actions with the
 *  agents that use them; `facts` how many remembered things to show around you.
 *
 *  You in the middle; what you told Mavo just around you; what many agents share on the next ring
 *  (short lines in, not lines across the map); then your agents, ring after ring, each ring turned
 *  half a slot from the last so cards never sit on top of each other; and outside, what only a few
 *  agents use, next to them. */
export function radialLayout(agentIds: string[], outer: { key: string; users: string[] }[], facts: number, taskTeams: number[] = []): GraphLayout {
  const n = agentIds.length;
  // Mavo's models ("route:" keys: AIRouter, Ollama) always sit in the middle: every agent thinks
  // through one, so they belong between you and your agents, however few agents use them.
  const models = outer.filter(o => o.key.startsWith("route:"));
  const hubs = [...models, ...outer.filter(o => !o.key.startsWith("route:") && isHub(o.users.length, n)).sort((a, b) => b.users.length - a.users.length)].slice(0, 8);
  const hubKeys = new Set(hubs.map(h => h.key));
  const rest = outer.filter(o => !hubKeys.has(o.key));
  // Rings far enough apart at the oval's sides (x 1.45) that a note, a shared thing and an agent
  // card never touch: 106px between the notes and the shared ring, 135px on to the agents.
  const r0 = 94, rh = 200;
  const first = hubs.length ? rh + 135 : 215;
  const ids = [...agentIds, "__new__"]; // the last place is "New agent"
  const rings = ringCounts(ids.length, first);
  const angle: Record<string, number> = {}, radius: Record<string, number> = {};
  let next = 0;
  rings.forEach(({ radius: r, count }, k) => {
    const turn = k % 2 ? Math.PI / Math.max(count, 1) : 0; // half a slot on every other ring
    for (let i = 0; i < count; i++, next++) { angle[ids[next]] = -Math.PI / 2 + turn + (i * TAU) / count; radius[ids[next]] = r; }
  });
  const last = rings[rings.length - 1].radius;
  const ro = last + 170;
  // A ring of their own for one-time tasks, outside the things agents use: a task coming and
  // going never moves an agent.
  const rt = ro + 160;
  const reach = taskTeams.length ? rt + 60 : ro;
  const W = Math.round(2 * (reach * SX + 130)), H = Math.round(2 * (reach * SY + 60));
  const c = { x: W / 2, y: H / 2 };
  const at = (r: number, a: number): Point => ({ x: c.x + r * SX * Math.cos(a), y: c.y + r * SY * Math.sin(a) });
  const agents = Object.fromEntries(agentIds.map(id => [id, at(radius[id], angle[id])]));
  const bends = Object.fromEntries(agentIds.map(id => [id, at(ro - 25, angle[id])]));
  // Each thing goes where its agents are: the average of their directions.
  // When the agents are all round (a hub most of them use), there is no one direction: the
  // fallback (an even share of the ring) is used instead.
  const toward = (users: string[], fallback: number, weak = 1e-6) => {
    const xs = users.reduce((s, u) => s + Math.cos(angle[u] ?? 0), 0), ys = users.reduce((s, u) => s + Math.sin(angle[u] ?? 0), 0);
    return Math.hypot(xs, ys) <= weak * Math.max(users.length, 1) ? fallback : Math.atan2(ys, xs);
  };
  const outerAt = spread(rest.map(o => toward(o.users, angle[o.users[0]] ?? 0)), Math.min(0.3, 170 / (ro * 1.15)));
  const hubAt = spread(hubs.map((h, i) => toward(h.users, Math.PI / 2 + (i * TAU) / hubs.length, 0.35)), Math.min(0.78, TAU / Math.max(hubs.length, 1)));
  const nodes = Object.fromEntries([
    ...rest.map((o, i) => [o.key, at(ro, outerAt[i])] as const),
    ...hubs.map((h, i) => [h.key, at(rh, hubAt[i])] as const),
  ]);
  const shown = Math.min(facts, 6);
  const memory = Array.from({ length: shown }, (_, i) => at(r0, -Math.PI / 2 + (TAU * (i + 0.5)) / Math.max(shown, 1)));
  // Tasks start at the top right and go round; each one's members fan out away from the middle.
  const tasks = taskTeams.map((size, i) => {
    const a = -Math.PI / 3 + (i * TAU) / Math.max(taskTeams.length, 6);
    const hub = at(rt, a);
    const members = Array.from({ length: size }, (_, k) => {
      // Fanned out beyond the task's card, about 26 degrees apart so dots and names never touch.
      const spread = Math.min(2.6, 0.46 * Math.max(size - 1, 1));
      const t = size === 1 ? 0 : -spread / 2 + (spread * k) / (size - 1);
      return { x: hub.x + 165 * Math.cos(a + t), y: hub.y + 100 * Math.sin(a + t) };
    });
    return { hub, members };
  });
  return { W, H, c, agents, bends, nodes, hubs: [...hubKeys], rings: rings.length, memory, tasks, newAgent: at(radius.__new__, angle.__new__), pool: { rx: (r0 + 28) * SX, ry: (r0 + 28) * SY } };
}

export type View = { x: number; y: number; k: number };

/** As far out as the view goes: three hundred agents fit on a laptop screen. */
export const MIN_ZOOM = 0.08;

/** The view that shows the whole drawing in a box, centred, never blown up past 1.1x. */
export function fitView(W: number, H: number, width: number, height: number, pad = 24): View {
  const k = Math.max(MIN_ZOOM, Math.min(1.1, (width - pad * 2) / W, (height - pad * 2) / H));
  return { k, x: (width - W * k) / 2, y: (height - H * k) / 2 };
}

/** How much to draw at this zoom: everything close up; names without their schedule further out;
 *  far out, agents are their initials and only what you point at is named. */
export function detailAt(k: number): "near" | "mid" | "far" {
  return k >= 0.75 ? "near" : k >= 0.45 ? "mid" : "far";
}

/** Zooms by `factor` keeping the point under the cursor still. */
export function zoomAt(view: View, factor: number, at: Point, min = MIN_ZOOM, max = 2.5): View {
  const k = Math.max(min, Math.min(max, view.k * factor));
  const f = k / view.k;
  return { k, x: at.x - (at.x - view.x) * f, y: at.y - (at.y - view.y) * f };
}
