import { List, LoaderCircle } from "lucide-react";
import { useEffect, useId, useState, type CSSProperties } from "react";
import { api } from "../lib/api";
import type { CrewInstance } from "../types";
import { identityColor, identityStyle, persistIdentityColors, reserveIdentityColors } from "../lib/identityColors";
import { nextRun } from "./CrewQuickList";

const MAX = 150; // past this the last spot opens the full list
const SIGNALS = 24; // live signals drawn at once: a hundred agents still move smoothly

/** A badge's size for a team this big: roomy for a few, smaller for a crowd. */
export function bubbleFor(n: number): number {
  return n <= 20 ? 44 : n <= 50 ? 38 : 32;
}

function seeded(text: string): () => number {
  // A string's own random sequence (mulberry32): the same agent lands in the same spot each time.
  let h = 1779033703 ^ text.length;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 3432918353), h = (h << 13) | (h >>> 19);
  let a = h >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Where each agent goes on the screen: scattered, not in rows, yet each always in its own spot
 *  (drawn from its id), never on top of another (of a few tries, the spot farthest from the
 *  agents already placed), and never on the core. A small team stays near the icon; the more
 *  there are, the further out they spread, until a hundred cover the screen. Offsets are from
 *  the core's centre. */
export function scatterLayout(ids: string[], w: number, h: number, core: { x: number; y: number }): { x: number; y: number }[] {
  const n = ids.length;
  if (!n) return [];
  const bubble = bubbleFor(n);
  const margin = bubble / 2 + 16;
  const reach = Math.min(Math.hypot(w, h), 200 + 200 * Math.sqrt(n));
  const placed: { x: number; y: number }[] = [];
  for (const id of ids) {
    const random = seeded(id);
    let best: { x: number; y: number } | null = null, bestGap = -1;
    for (let tries = 0; tries < 48; tries++) {
      const x = margin + random() * Math.max(1, w - 2 * margin);
      const y = margin + random() * Math.max(1, h - 2 * margin);
      const fromCore = Math.hypot(x - core.x, y - core.y);
      if (fromCore < bubble * 2 || fromCore > reach) continue;
      const gap = placed.reduce((m, p) => Math.min(m, Math.hypot(p.x - x, p.y - y)), Infinity);
      if (gap > bestGap) { best = { x, y }; bestGap = gap; }
    }
    // Nowhere fitting within reach (a tiny window): anywhere off the core.
    if (!best) { const angle = Math.PI + random() * Math.PI / 2; best = { x: core.x + Math.cos(angle) * reach * 0.6, y: core.y + Math.sin(angle) * reach * 0.6 }; }
    placed.push(best);
  }
  return placed.map(p => ({ x: Math.round(p.x - core.x), y: Math.round(p.y - core.y) }));
}

/** One persistent, collision-checked identity per Crew, independent of its template. */
export function agentHue(crew: Pick<CrewInstance, "id" | "template_id">): number {
  return identityColor(`crew:${crew.id}`).hue;
}
export function agentStyle(crew: Pick<CrewInstance,"id">): CSSProperties { return identityStyle(identityColor(`crew:${crew.id}`)); }

/** A softly bent nerve between two points: a quadratic curve whose middle is pushed sideways
 *  (bend > 0 bows it away from the core), so the network looks grown, not drawn with a ruler. */
export function nerve(x1: number, y1: number, x2: number, y2: number, bend: number): string {
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
  const cx = mx - (dy / len) * bend * len, cy = my + (dx / len) * bend * len;
  return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
}

/** Who talks to whom among the shown agents: a Crew's result is handed to the Crew that list it
 *  as upstream (the links the person approved). From upstream to downstream. */
export function talks(crews: Pick<CrewInstance, "id" | "configuration_plan">[]): { from: number; to: number }[] {
  const at = new Map(crews.map((c, i) => [c.id, i]));
  return crews.flatMap((c, to) => (c.configuration_plan?.upstream ?? []).filter(id => at.has(id)).map(id => ({ from: at.get(id)!, to })));
}

/** The brain's mesh: each agent joined to its nearest neighbour, once per pair. */
export function nearestPairs(points: { x: number; y: number }[]): [number, number][] {
  const seen = new Set<string>();
  const pairs: [number, number][] = [];
  points.forEach((p, i) => {
    let best = -1, d = Infinity;
    points.forEach((q, j) => { if (j !== i) { const e = Math.hypot(p.x - q.x, p.y - q.y); if (e < d) { d = e; best = j; } } });
    const key = Math.min(i, best) + ":" + Math.max(i, best);
    if (best >= 0 && !seen.has(key)) { seen.add(key); pairs.push([Math.min(i, best), Math.max(i, best)]); }
  });
  return pairs;
}

/** What a Crew does, in one plain line: the first sentence of its job, without the schedule
 *  ("Every 3 hours: ..."), which the label already says. */
export function doesLine(job: string, most = 120): string {
  const text = (job || "").replace(/\s+/g, " ").replace(/^(every|each|whenever|at)\b[^:,]{0,40}[:,]\s*/i, "").trim();
  const first = (text.match(/^.+?[.!?](\s|$)/) ?? [text])[0].trim();
  const line = first.charAt(0).toUpperCase() + first.slice(1);
  return line.length <= most ? line : line.slice(0, line.lastIndexOf(" ", most)).replace(/[,;:]$/, "") + "…";
}

function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(w => w && !/^(the|my|a|an)$/i.test(w));
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

function stateOf(crew: CrewInstance): "working" | "running" | "needs" | "stopped" {
  const tone = nextRun(crew).tone;
  if (crew.runtime_state === "working") return "working";
  if (tone === "off") return "stopped";
  if (tone === "wait") return "needs";
  return "running";
}

/** The desktop icon's Crew, spread over the screen out of the icon like a brain around its core.
 *  Hover one: who it is and when it works next. Click: it runs now. Click anywhere empty to
 *  fold them back in. */
export function CrewFan({ initial, width, height, core, onList, onOpen, onClose }: {
  initial?: CrewInstance[]; width?: number; height?: number; core?: { x: number; y: number };
  onList: () => void; onOpen: (crew: CrewInstance) => void; onClose?: () => void;
}) {
  const [crews, setCrews] = useState<CrewInstance[] | null>(initial ?? null);
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, ""); // this view's own path ids
  const w = width ?? window.innerWidth;
  const h = height ?? window.innerHeight;
  const cx = core?.x ?? w - 30, cy = core?.y ?? h - 30;

  useEffect(() => {
    if (initial) return;
    let active = true;
    const load = () => api.crew().then(all => { if (active) setCrews(all.filter(c => c.status !== "draft")); }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Could not load your Crew."); });
    void load();
    const timer = window.setInterval(() => void load(), 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [initial]);
  // Out they come, one after another, once they are known.
  useEffect(() => { if (!crews) return; const frame = requestAnimationFrame(() => setOpen(true)); return () => cancelAnimationFrame(frame); }, [crews]);

  async function run(crew: CrewInstance) {
    setBusy(crew.id); setError(null);
    try {
      const updated = crew.is_running ? await api.checkCrew(crew.id) : await api.startCrew(crew.id);
      setCrews(list => (list ?? []).map(c => c.id === updated.id ? updated : c));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not run it."); }
    finally { setBusy(null); }
  }

  const all = crews ?? [];
  reserveIdentityColors(all.map(c=>`crew:${c.id}`));
  useEffect(() => { if (crews) persistIdentityColors(); }, [crews]);
  const shown = all.length > MAX ? all.slice(0, MAX - 1) : all;
  const more = all.length - shown.length;
  const ids = [...shown.map(c => c.id), ...(more ? ["__more__"] : [])];
  const spots = scatterLayout(ids, w, h, { x: cx, y: cy });
  const bubble = bubbleFor(ids.length);
  const point = (i: number) => ({ x: cx + spots[i].x, y: cy + spots[i].y });
  const place = (i: number): CSSProperties => ({
    left: cx - bubble / 2, top: cy - bubble / 2,
    ["--x" as string]: `${spots[i].x}px`, ["--y" as string]: `${spots[i].y}px`, ["--i" as string]: Math.min(i, 30),
  });
  const hovered = all.find(c => c.id === hover);
  const at = hovered ? point(shown.indexOf(hovered)) : null;
  const links = talks(shown);
  const mesh = nearestPairs(shown.map((_, i) => point(i)));
  const talkingTo = hovered ? links.filter(l => shown[l.from] === hovered).map(l => shown[l.to].name) : [];
  const hearingFrom = hovered ? links.filter(l => shown[l.to] === hovered).map(l => shown[l.from].name) : [];
  let signals = 0;

  const nerveAlpha = ids.length > 40 ? 0.26 : ids.length > 15 ? 0.4 : 0.55; // a crowd's nerves fainter
  return <div className="crew-fan" data-open={open} aria-label="Your Crew" style={{ ["--b" as string]: `${bubble}px`, ["--nerve" as string]: nerveAlpha }}>
    {onClose && <button type="button" className="fan-backdrop" aria-label="Close" onClick={onClose} />}
    <div className="brain-core" style={{ left: cx - 46, top: cy - 46 }} aria-hidden="true" />
    <svg className="brain-net" width={w} height={h} aria-hidden="true">
      {/* the mesh: each agent to its nearest neighbour */}
      {mesh.map(([i, j]) => { const a = point(i), b = point(j); return <path key={`m${i}-${j}`} className="brain-mesh" pathLength={1} style={{ ["--i" as string]: Math.min(i, 30) }} d={nerve(a.x, a.y, b.x, b.y, -0.18)} />; })}
      {/* a nerve from the core to every agent, with signals while it works */}
      {shown.map((crew, i) => {
        const p = point(i), state = stateOf(crew), id = `nerve-${uid}-${i}`;
        const live = (state === "working" || state === "running") && (state === "working" || signals++ < SIGNALS);
        return <g key={crew.id} data-state={state} style={{ ...agentStyle(crew), ["--i" as string]: Math.min(i, 30) }}>
          <path id={id} className="brain-nerve" pathLength={1} d={nerve(cx, cy, p.x, p.y, i % 2 ? 0.12 : -0.12)} />
          {live && <circle className="brain-signal" r={state === "working" ? 3.2 : 2.4} visibility="hidden">
            <set attributeName="visibility" to="visible" begin={`${0.6 + (i % 12) * 0.37}s`} />
            <animateMotion dur={state === "working" ? "1.1s" : "3.2s"} begin={`${0.6 + (i % 12) * 0.37}s`} repeatCount="indefinite" keyPoints="1;0" keyTimes="0;1" calcMode="linear"><mpath href={"#" + id} /></animateMotion>
          </circle>}
        </g>;
      })}
      {/* synapses: agents that talk to one another, a message travelling from one to the next */}
      {links.map(({ from, to }) => {
        const a = point(from), b = point(to), id = `talk-${uid}-${from}-${to}`;
        return <g key={id}>
          <path id={id} className="brain-talk" pathLength={1} d={nerve(a.x, a.y, b.x, b.y, -0.32)} />
          <circle className="brain-message" r={3} visibility="hidden">
            <set attributeName="visibility" to="visible" begin={`${1 + (from % 8) * 0.3}s`} />
            <animateMotion dur="2.4s" begin={`${1 + (from % 8) * 0.3}s`} repeatCount="indefinite"><mpath href={"#" + id} /></animateMotion>
          </circle>
        </g>;
      })}
    </svg>
    {crews !== null && all.length === 0 && <p className="fan-empty" style={{ left: Math.max(8, cx - 260), top: Math.max(8, cy - 90) }}>No Crew yet. Ask Mavo for one.</p>}
    {shown.map((crew, i) => {
      const state = stateOf(crew);
      return <button key={crew.id} className="fan-agent" data-state={state} style={{ ...place(i), ...agentStyle(crew) }}
        aria-label={`${crew.name}, ${nextRun(crew).text}. Run now`} disabled={busy !== null || state === "working"}
        onPointerEnter={() => setHover(crew.id)} onPointerLeave={() => setHover(h => h === crew.id ? null : h)}
        onClick={() => void run(crew)} onContextMenu={e => { e.preventDefault(); onOpen(crew); }}>
        <span className="fan-face">{busy === crew.id ? <LoaderCircle size={15} className="animate-spin" /> : initials(crew.name)}</span>
        {shown.length <= 60 && <span className="fan-name">{crew.name}</span>}
      </button>;
    })}
    {more > 0 && <button className="fan-agent fan-more" style={place(shown.length)} aria-label={`${more} more: show the list`} onClick={onList}>
      <span className="fan-face">+{more}</span>
    </button>}
    {hovered && at && <div className="fan-label" style={{
      // Beside the agent it is about, on the side with room, kept on the screen.
      left: at.x + bubble / 2 + 200 < w ? at.x + bubble / 2 + 10 : Math.max(8, at.x - bubble / 2 - 200),
      top: Math.min(h - 90, Math.max(8, at.y - 24)),
    }}>
      <b>{hovered.name}</b>
      {hovered.responsibilities && <span className="fan-does">{doesLine(hovered.responsibilities)}</span>}
      <span data-tone={nextRun(hovered).tone}>{nextRun(hovered).text} · click to run</span>
      {talkingTo.length > 0 && <span className="fan-talk">Tells {talkingTo.join(", ")}</span>}
      {hearingFrom.length > 0 && <span className="fan-talk">Hears from {hearingFrom.join(", ")}</span>}
    </div>}
    {all.length > 0 && <button className="fan-list" style={{ left: cx - 13, top: cy - 70 }} aria-label="Show as a list" title="Show as a list" onClick={onList}><List size={13} /></button>}
    {error && <p role="alert" className="fan-error">{error}</p>}
  </div>;
}
