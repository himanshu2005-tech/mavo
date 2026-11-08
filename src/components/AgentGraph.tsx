import { Brain, Check, ListChecks, LoaderCircle, Maximize2, Minus, Plus, ShieldCheck, UserCheck } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import type { CrewInstance, MemoryFact } from "../types";
import { triggerLine } from "./CrewCard";
import { agentStyle } from "./CrewFan";
import { nextRun } from "./CrewQuickList";
import { workerLabel } from "../lib/crewStatus";
import { GraphConnections } from "./GraphConnections";
import { flowOrder, wiring, type Wire } from "./crewWiring";
import { detailAt, initials, myInitials, radialLayout, zoomAt, type GraphLayout, type Point, type View } from "../lib/radialGraph";
import { attachConnections, frameAgents } from "../lib/connectionLayout";
import { identityColor, identityStyle, persistIdentityColors, reserveIdentityColors } from "../lib/identityColors";
import { listens, runClock } from "../lib/runClock";
import type { TaskOnMap } from "../lib/taskTeams";
import {executionLabel,executionSignals,type ExecutionFeed} from '../lib/executionSignals';

export type Selection = { kind: "me" } | { kind: "agent"; id: string } | { kind: "resource" | "action"; key: string } | { kind: "task"; id: string } | { kind: "route"; key: string } | null;
/** One of Mavo's models (a route: AIRouter in the cloud, Ollama on this computer). */
export interface Brain { id: string; name: string; provider: string; model: string; connected?: boolean; signature?: string }
export type GraphNode = Wire & { users: string[] };

export function agentState(crew: CrewInstance): "working" | "running" | "needs" | "stopped" | "done" | "draft" {
  if (crew.status === "draft") return "draft";
  if (crew.configuration_plan?.finished) return "done";
  if (!crew.is_running || crew.status === "paused") return "stopped";
  if (crew.runtime_state === "working") return "working";
  const tone = nextRun(crew).tone;
  return tone === "off" ? "stopped" : tone === "wait" ? "needs" : "running";
}
export const STATE_WORD = { working: "Working now", running: "Running", needs: "Needs you", stopped: "Stopped", done: "Done", draft: "Draft" };

/** Enabled responsibilities and actual worker execution are different visual states. */
export function agentActivity(crew: CrewInstance): "off" | "ready" | "working" | "waiting" {
  const state=agentState(crew);
  if (["stopped","draft","done"].includes(state)) return "off";
  if (state==="working") return "working";
  if (state==="needs" || ["budget_paused","waiting_browser","needs_setup"].includes(crew.runtime_state)
    || ["needs_access","needs_input"].includes(crew.configuration_plan?.phase??"")) return "waiting";
  return "ready";
}

/** Every resource or action the agents use, with the agents that use it. */
export function gatherNodes(rows: { crew: CrewInstance; list: Wire[] }[]): GraphNode[] {
  const nodes = new Map<string, GraphNode>();
  rows.forEach(({ crew, list }) => list.forEach(w => {
    const had = nodes.get(w.key);
    if (had) { had.users.push(crew.id); had.allowed ||= w.allowed; }
    else nodes.set(w.key, { ...w, users: [crew.id] });
  }));
  return [...nodes.values()];
}

export function useGraphModel(crews: CrewInstance[], drafts: CrewInstance[]) {
  return useMemo(() => {
    const ordered = [...flowOrder(crews), ...drafts];
    const wired = ordered.map(crew => ({ crew, ...wiring(crew) }));
    const outer = [...gatherNodes(wired.map(w => ({ crew: w.crew, list: w.reads }))), ...gatherNodes(wired.map(w => ({ crew: w.crew, list: w.does })))];
    const ids = new Set(ordered.map(c => c.id));
    // Who hands work to whom: a fixed hand-over (upstream), and a team lead to each member.
    const talks = ordered.flatMap(c => [
      ...(c.configuration_plan?.upstream ?? []).filter(id => ids.has(id)).map(from => ({ from, to: c.id })),
      ...(c.configuration_plan?.lead && ids.has(c.configuration_plan.lead) ? [{ from: c.configuration_plan.lead, to: c.id }] : []),
    ]);
    return { ordered, wired, outer, talks };
  }, [crews, drafts]);
}

/** Radial agents beside a grouped Connections box. Drag the graph to pan, scroll to zoom. */
export function AgentGraph({ crews, drafts, me, memory, selection, hits, inset = 0, onSelect, onNew, onRun, tasks = [], routes = [],execution }: {
  crews: CrewInstance[]; drafts: CrewInstance[]; me: string; memory: MemoryFact[]; selection: Selection;
  /** Agents and nodes a search found: everything else fades. */
  hits?: { agents: Set<string>; keys: Set<string> } | null;
  /** Room taken on the right (a panel over the graph): the drawing fits in what is left. */
  inset?: number;
  onSelect: (s: Selection) => void; onNew: () => void;
  /** Mavo's models; each agent links to the one it thinks with. */
  routes?: Brain[];
  /** One-time tasks working now (or just finished): temporary teams on the outer ring. */
  tasks?: TaskOnMap[];
  /** Run a scheduled agent now, from its clock. */
  onRun?: (id: string) => Promise<void>;
  execution?:ExecutionFeed|null;
}) {
  const { ordered, wired, outer, talks } = useGraphModel(crews, drafts);
  const signalNow=useNow(1000);
  const signals=useMemo(()=>executionSignals(execution,signalNow),[execution,signalNow]);
  const runCards=Object.values(signals.crews).filter(s=>ordered.some(c=>c.id===s.crewId))
    .sort((a,b)=>Number(['running','requesting'].includes(b.stage))-Number(['running','requesting'].includes(a.stage))||b.at.localeCompare(a.at)).slice(0,3);
  const identityKeys = [...ordered.map(c=>`crew:${c.id}`),...routes.map(r=>`route:${r.id}`),...outer.map(n=>`${n.kind}:${n.key}`)];
  const palette = useMemo(() => reserveIdentityColors(identityKeys), [identityKeys.join('\0')]);
  useEffect(() => persistIdentityColors(), [palette]);
  const colorStyle = (key:string) => identityStyle(identityColor(key));
  const liveRouteUsers=Object.values(signals.crews).map(s=>`${s.crewId}:${s.routeId}`).sort().join('|');
  // The models agents think with, each with the agents that use it.
  const brains = useMemo(() => routes
    .map(route => ({ key: "route:" + route.id, route, users: ordered.filter(c => c.status!=="draft"&&(c.route_id===route.id||Object.values(signals.crews).some(s=>s.crewId===c.id&&s.routeId===route.id))).map(c => c.id) }))
    .filter(b => b.users.length), [routes, ordered,liveRouteUsers]);
  const teamSizes = tasks.map(t => t.members.length).join(",");
  const radial = useMemo(() => radialLayout(ordered.map(c => c.id), [...outer, ...brains.map(b => ({ key: b.key, users: b.users }))], memory.length, teamSizes ? teamSizes.split(",").map(Number) : tasks.map(() => 0)), [ordered, outer, brains, memory.length, teamSizes, tasks.length]);
  // A big team: lines are drawn for what you point at, not all at once (hundreds of agents would
  // be a web of thousands of lines, and slow).
  const many = ordered.length > 40;
  const [hover, setHover] = useState<Selection>(null);
  // Hover with intent: light up after a beat, let go after a slightly longer one, so passing over
  // the map does not flash and moving from a node to its clock does not flicker. Leaving a node
  // always lets go (it used to stay lit until the pointer left the whole map).
  const hoverTimer = useRef(0);
  const point = useCallback((s: Selection) => {
    window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => setHover(s), s ? 60 : 140);
  }, []);
  useEffect(() => () => window.clearTimeout(hoverTimer.current), []);
  const hoverable = (s: Selection) => ({ onMouseEnter: () => point(s), onMouseLeave: () => point(null), onFocus: () => point(s), onBlur: () => point(null) });
  const focus = hover ?? selection;
  const working = useMemo(() => new Set(ordered.filter(c => agentActivity(c)==="working").map(c => c.id)), [ordered]);
  const stopped = useMemo(() => new Set(ordered.filter(c=>agentState(c)==="stopped").map(c=>c.id)), [ordered]);
  const readyMotion = useMemo(() => new Set(ordered.filter(c=>agentActivity(c)==="ready").slice(0,24).map(c=>c.id)), [ordered]);

  // A changed last-run timestamp sends a wake ripple; verified outcomes use the live feed.
  const seen = useRef(new Map<string, string | null>());
  const [ripples, setRipples] = useState<Record<string, number>>({});
  useEffect(() => {
    const fresh: string[] = [];
    ordered.forEach(c => {
      const before = seen.current.get(c.id);
      if (before !== undefined && c.last_run_at && before !== c.last_run_at) fresh.push(c.id);
      seen.current.set(c.id, c.last_run_at ?? null);
    });
    if (!fresh.length) return;
    const at = Date.now();
    setRipples(r => ({ ...r, ...Object.fromEntries(fresh.map(id => [id, at])) }));
    window.setTimeout(() => setRipples(r => Object.fromEntries(Object.entries(r).filter(([, t]) => t !== at))), 1900);
  }, [ordered]);

  // What the focus touches: an agent its lines and the agents it talks to; a node its agents.
  const lit = useMemo(() => {
    if (hits) return hits;
    if (!focus || focus.kind === "me") return null;
    const agents = new Set<string>(), keys = new Set<string>();
    if (focus.kind === "task") return { agents, keys }; // a task's team is its own: the rest fades
    if (focus.kind === "route") {
      keys.add(focus.key);
      brains.find(b => b.key === focus.key)?.users.forEach(u => agents.add(u));
      return { agents, keys };
    }
    if (focus.kind === "agent") {
      agents.add(focus.id);
      const w = wired.find(x => x.crew.id === focus.id);
      w?.reads.forEach(r => keys.add(r.key)); w?.does.forEach(d => keys.add(d.key));
      if (w?.crew.route_id) keys.add("route:" + w.crew.route_id);
      talks.forEach(t => { if (t.from === focus.id) agents.add(t.to); if (t.to === focus.id) agents.add(t.from); });
    } else {
      keys.add(focus.key);
      outer.find(n => n.key === focus.key)?.users.forEach(u => agents.add(u));
    }
    return { agents, keys };
  }, [focus, hits, wired, talks, outer, brains]);
  const soloAgent = !hits && focus?.kind === "agent" ? focus.id : null;
  const edgeOn = (agentId: string, key: string) => !lit || (lit.agents.has(agentId) && lit.keys.has(key) && (!soloAgent || agentId === soloAgent));

  // The view: fitted to the window until you move it yourself.
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [anchors, setAnchors] = useState<Record<string,Point>>({});
  const updateAnchors = useCallback((next: Record<string,Point>) => {
    setAnchors(previous => JSON.stringify(previous)===JSON.stringify(next)?previous:next);
  }, []);
  const [moved, setMoved] = useState(false);
  useLayoutEffect(() => {
    const el = box.current; if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const watch = new ResizeObserver(measure); watch.observe(el);
    return () => watch.disconnect();
  }, []);
  const free = Math.max(320, size.w - inset);
  const connectionWidth = Math.min(228,Math.max(176,Math.floor(free*.26)));
  const mapLeft = connectionWidth+40;
  // What should stay in sight: the picked thing and what it is connected to.
  const targetKey = JSON.stringify(selection);
  const target = useMemo(() => {
    if (selection?.kind === "agent") {
      const w = wired.find(x => x.crew.id === selection.id);
      return [radial.agents[w?.crew.id ?? selection.id]].filter(Boolean);
    }
    if (selection?.kind === "route") {
      const b = brains.find(x => x.key === selection.key);
      return (b?.users ?? []).map(u => radial.agents[u]).filter(Boolean);
    }
    if (selection?.kind === "task") {
      const i = tasks.findIndex(t => t.run.id === selection.id), place = radial.tasks[i];
      return place ? [place.hub, ...place.members] : [];
    }
    if (selection && selection.kind !== "me") {
      const n = outer.find(x => x.key === selection.key);
      return (n?.users ?? []).map(u => radial.agents[u]).filter(Boolean);
    }
    return [];
  }, [targetKey, radial, wired, outer, tasks, brains]);
  /** Moves the view just enough that `points` are in the free part, the first one above all. */
  const keepInView = useCallback((v: View, points: { x: number; y: number }[]): View => {
    if (!points.length) return v;
    const xs = points.map(p => v.x + p.x * v.k), ys = points.map(p => v.y + p.y * v.k);
    const fit = (lo: number, hi: number, min: number, max: number, first: number) => {
      if (hi - lo > max - min) return (min + max) / 2 - first; // too wide: centre the picked one
      return lo < min ? min - lo : hi > max ? max - hi : 0;
    };
    const dx = fit(Math.min(...xs), Math.max(...xs), mapLeft+70, free - 70, xs[0]);
    const dy = fit(Math.min(...ys), Math.max(...ys), 50, size.h - 50, ys[0]);
    return dx || dy ? { ...v, x: v.x + dx, y: v.y + dy } : v;
  }, [free, mapLeft, size.h]);
  // Fitted to the window until you move it. A panel takes the right side: the drawing shrinks a
  // little, not to a stamp, and slides so what you picked stays in sight.
  useLayoutEffect(() => {
    if (moved || !size.w || !size.h) return;
    setView(keepInView(frameAgents(radial,mapLeft,Math.max(200,free-mapLeft-16),size.h),target));
  }, [moved, size, free, mapLeft, radial, target, keepInView]);
  useEffect(() => { if (moved) setView(v => keepInView(v, target)); }, [moved, target, keepInView]);
  useEffect(() => {
    const el = box.current; if (!el) return;
    let settle = 0;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      // Zooming follows the wheel directly; the glide is for fits and jumps.
      el.dataset.zooming = "true";
      window.clearTimeout(settle);
      settle = window.setTimeout(() => { delete el.dataset.zooming; }, 180);
      const r = el.getBoundingClientRect();
      setMoved(true);
      setView(v => zoomAt(v, Math.exp(-e.deltaY * 0.0015), { x: e.clientX - r.left, y: e.clientY - r.top }));
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => { el.removeEventListener("wheel", wheel); window.clearTimeout(settle); };
  }, []);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; far: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);
  const down = (e: ReactPointerEvent) => {
    if ((e.target as HTMLElement).closest("button, .graph-fact")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, far: false };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent) => {
    const d = drag.current; if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!d.far && Math.hypot(dx, dy) < 4) return;
    if (!d.far) { setDragging(true); point(null); }
    d.far = true; setMoved(true);
    setView(v => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
  };
  const up = () => { const d = drag.current; drag.current = null; setDragging(false); if (d && !d.far) onSelect(null); };
  const zoomBy = (f: number) => { setMoved(true); setView(v => zoomAt(v, f, { x: size.w / 2, y: size.h / 2 })); };

  const layout = useMemo(() => attachConnections(radial,view,anchors), [radial,view,anchors]);
  const { c } = layout;
  const pos = (p: { x: number; y: number }): CSSProperties => ({ left: p.x, top: p.y });
  /** Curved links end at measured rows in the screen-space Connections box. */
  const edgePath = (agentId: string, key: string) => {
    const a = layout.agents[agentId], b = layout.nodes[key];
    if (!a || !b) return null;
    const bend = Math.max(40/view.k,Math.abs(a.x-b.x)*.4);
    return `M${a.x},${a.y} C${a.x-bend},${a.y} ${b.x+bend},${b.y} ${b.x},${b.y}`;
  };
  // The path an agent's work takes, shown in order when you pick it (or a model's agents, when
  // you pick the model): you hand it the job, it thinks with its model, reads, then acts.
  const traceKey = focus && (focus.kind === "agent" || focus.kind === "route") ? JSON.stringify(focus) : "";
  const trace = useMemo(() => {
    const out: { d: string; delay: number; kind: string; identity: string; local?: boolean }[] = [];
    const isLocal = (routeId?: string | null) => routes.find(r => r.id === routeId)?.provider === "ollama";
    if (!traceKey || !focus) return out;
    const follow = (crewId: string, start: number) => {
      const a = layout.agents[crewId], w = wired.find(x => x.crew.id === crewId);
      if (!a || !w || agentState(w.crew) === "stopped") return;
      out.push({ d: `M${c.x},${c.y} L${a.x},${a.y}`, delay: start, kind: "you", identity:`crew:${crewId}` });
      const thinks = w.crew.route_id && edgePath(crewId, "route:" + w.crew.route_id);
      if (thinks) out.push({ d: thinks, delay: start + 0.45, kind: "think", identity:`route:${w.crew.route_id}`, local: isLocal(w.crew.route_id) });
      w.reads.filter(x => x.allowed).forEach((x, i) => { const d = edgePath(crewId, x.key); if (d) out.push({ d, delay: start + 0.9 + i * 0.08, kind: "read", identity:`resource:${x.key}` }); });
      w.does.filter(x => x.allowed).forEach((x, i) => { const d = edgePath(crewId, x.key); if (d) out.push({ d, delay: start + 1.35 + i * 0.08, kind: "act", identity:`action:${x.key}` }); });
    };
    if (focus.kind === "agent") follow(focus.id, 0);
    else if (focus.kind === "route") brains.find(b => b.key === focus.key)?.users.slice(0, 24).forEach((u, i) => {
      if (agentState(ordered.find(c => c.id === u)!) === "stopped") return;
      const d = edgePath(u, focus.key);
      if (d) out.push({ d, delay: i * 0.05, kind: "think", identity:focus.key, local: focus.key.startsWith("route:") && isLocal(focus.key.slice(6)) });
    });
    return out;
  }, [traceKey, layout, wired, brains, routes]);
  const picked = (s: Selection) => JSON.stringify(s) === JSON.stringify(selection);
  return <div className="graph-canvas" ref={box} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null; setDragging(false); }} onMouseLeave={() => point(null)} data-dragging={dragging} data-focus={!!lit} data-many={many} data-zoom={detailAt(view.k)}
    style={{ ["--lift" as string]: Math.min(4, Math.max(1, 0.85 / view.k)), ["--lift-soft" as string]: Math.min(2.6, Math.max(1, 0.62 / view.k)), ["--far" as string]: Math.min(2.2, Math.max(1, 0.5 / view.k)) } as CSSProperties}>
    <div className="graph-world" style={{ width: layout.W, height: layout.H, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
      <svg className="graph-wires" width={layout.W} height={layout.H} aria-hidden="true">
        {ordered.map(crew => { const a = layout.agents[crew.id]; return <line key={"s" + crew.id} className="wire wire-spoke" data-identity={`crew:${crew.id}`} data-state={stopped.has(crew.id)?"stopped":undefined} style={colorStyle(`crew:${crew.id}`)} data-on={!lit || lit.agents.has(crew.id)} data-flow={working.has(crew.id) ? "out" : undefined} x1={c.x} y1={c.y} x2={a.x} y2={a.y} />; })}
        <line className="wire wire-spoke wire-new" x1={c.x} y1={c.y} x2={layout.newAgent.x} y2={layout.newAgent.y} data-on={!lit} />
        {wired.flatMap(({ crew, reads, does }) => [...reads, ...does].map(w => {
          const a = layout.agents[crew.id], b = layout.nodes[w.key]; if (!a || !b) return null;
          return <path key={crew.id + w.key} className={"wire " + (w.kind === "resource" ? "wire-read" : "wire-act")} data-target={w.key} data-identity={`${w.kind}:${w.key}`} data-state={stopped.has(crew.id)?"stopped":undefined} style={colorStyle(`${w.kind}:${w.key}`)} data-on={edgeOn(crew.id, w.key)} data-allowed={w.allowed}
            data-flow={working.has(crew.id) && w.allowed ? (w.kind === "resource" ? "in" : "out") : undefined} d={edgePath(crew.id,w.key) ?? undefined} />;
        }))}
        {brains.flatMap(b => b.users.map(u => {
          const d = edgePath(u, b.key); if (!d) return null;
          const requesting=signals.calls.some(call=>call.crew_id===u&&call.route_id===b.route.id);
          return <path key={"think" + u} className="wire wire-route" data-target={b.key} data-identity={b.key} data-state={stopped.has(u)&&!requesting?"stopped":undefined} data-provider-active={requesting} style={colorStyle(b.key)} data-local={b.route.provider === "ollama"} data-on={edgeOn(u, b.key)} data-flow={requesting ? "out" : undefined} d={d} />;
        }))}
        {talks.map(({ from, to }) => {
          const a = layout.agents[from], b = layout.agents[to]; if (!a || !b) return null;
          const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, k = { x: m.x + (c.x - m.x) * 0.45, y: m.y + (c.y - m.y) * 0.45 };
          const id = `talk-${from}-${to}`, on = !lit || (lit.agents.has(from) && lit.agents.has(to));
          return <g key={id} data-on={on} data-state={stopped.has(from)||stopped.has(to)?"stopped":undefined} data-identity={`crew:${to}`} style={colorStyle(`crew:${to}`)}><path id={id} className="wire wire-talk" data-identity={`crew:${to}`} data-state={stopped.has(from)||stopped.has(to)?"stopped":undefined} d={`M${a.x},${a.y} Q${k.x},${k.y} ${b.x},${b.y}`} />
            <circle className="wire-pulse" r={3.6}><animateMotion dur="2.4s" repeatCount="indefinite"><mpath href={"#" + id} /></animateMotion></circle></g>;
        })}
        {tasks.map((t, i) => {
          const place = layout.tasks[i]; if (!place) return null;
          const dim = !!focus && !(focus.kind === "task" && focus.id === t.run.id);
          return <g key={"task" + t.run.id} data-on={!dim} data-state={t.state}>
            <line className="wire wire-task" x1={c.x} y1={c.y} x2={place.hub.x} y2={place.hub.y} />
            {t.members.map((m, k) => place.members[k] && <line key={m.name} className="wire wire-task-member" data-flow={m.state === "working" ? "out" : undefined}
              x1={place.hub.x} y1={place.hub.y} x2={place.members[k].x} y2={place.members[k].y} />)}
          </g>;
        })}
      </svg>

      <button className="graph-me" style={pos(c)} aria-pressed={selection?.kind === "me"} {...hoverable({ kind: "me" })} onClick={() => onSelect({ kind: "me" })} title="You, and what Mavo remembers">
        <span className="graph-me-face">{myInitials(me)}</span><b>{me}</b>
        <span className="graph-me-sub"><Brain size={11} />{memory.length ? `${memory.length} thing${memory.length === 1 ? "" : "s"} remembered` : "Memory"}</span>
      </button>
      {layout.memory.map((p, i) => memory[i] && <span key={memory[i].id} className="graph-fact" style={pos(p)} title={`${memory[i].text} (from ${memory[i].source})`} onClick={() => onSelect({ kind: "me" })}>{memory[i].text}</span>)}

      {tasks.map((t, i) => {
        const place = layout.tasks[i]; if (!place) return null;
        const sel: Selection = { kind: "task", id: t.run.id };
        const mine = focus?.kind === "task" && focus.id === t.run.id;
        const dim = !mine && (!!lit || !!focus);
        const word = t.state === "working" ? `Working · ${t.done} of ${t.total} steps` : t.state === "needs" ? "Needs you" : t.state === "done" ? "Done" : "Stopped";
        return <span key={"task" + t.run.id} className="graph-task-team" data-state={t.state}>
          <button className="graph-task" style={pos(place.hub)} data-state={t.state} data-dim={dim} aria-pressed={picked(sel)} {...hoverable(sel)}
            onClick={() => onSelect(sel)} title={`${t.run.title}: ${word}`}>
            <span className="graph-task-ring" style={{ ["--p" as string]: t.total ? t.done / t.total : 0 } as CSSProperties}><ListChecks size={13} aria-hidden="true" /></span>
            <span className="graph-task-text"><b>{t.run.title.replace(/^build:\s*/i, "")}</b><span>{word}</span></span>
          </button>
          {t.members.map((m, k) => place.members[k] && <button key={m.name} className="graph-task-member" style={pos(place.members[k])} data-state={m.state} data-dim={dim}
            onClick={() => onSelect(sel)} {...hoverable(sel)} title={`${m.name}: ${m.state === "working" ? "working now" : m.state}`} aria-label={`${m.name}, ${m.state}, in ${t.run.title}`}>
            <span>{initials(m.name)}</span><i className="graph-task-member-name">{m.name}</i>
          </button>)}
        </span>;
      })}
      <RunClocks crews={ordered} layout={layout} lit={lit} focus={focus?.kind === "agent" ? focus.id : null} onRun={onRun} onOpen={id => onSelect({ kind: "agent", id })} hoverable={id => hoverable({ kind: "agent", id })} />
      {Object.entries(ripples).map(([id, at]) => layout.agents[id] && <span key={id + at} className="graph-ripple" style={pos(layout.agents[id])} aria-hidden="true" />)}
      {/* A step in a line that checks the work (a reviewer) or waits for you: a badge on its arc. */}
      {talks.map(({ from, to }) => {
        const target = ordered.find(x => x.id === to), plan = target?.configuration_plan;
        const a = layout.agents[from], b = layout.agents[to];
        if (!target || !a || !b || !(plan?.review || plan?.approve_first)) return null;
        const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, k = { x: m.x + (c.x - m.x) * 0.45, y: m.y + (c.y - m.y) * 0.45 };
        const mid = { x: 0.25 * a.x + 0.5 * k.x + 0.25 * b.x, y: 0.25 * a.y + 0.5 * k.y + 0.25 * b.y };
        const say = [plan.review && `${target.name} reviews this work and can send it back`, plan.approve_first && `${target.name} waits for your OK`].filter(Boolean).join(". ");
        return <span key={"gate" + from + to} className="talk-badge" style={pos(mid)} title={say} aria-label={say}
          data-dim={!!lit && !(lit.agents.has(from) && lit.agents.has(to))} data-held={!!target.held}>
          {plan.review && <ShieldCheck size={11} aria-hidden="true" />}{plan.approve_first && <UserCheck size={11} aria-hidden="true" />}
        </span>;
      })}

      {ordered.map((crew, i) => {
        const state = agentState(crew), live=signals.crews[crew.id], activity=live&&['running','requesting'].includes(live.stage)?'working':agentActivity(crew), sel: Selection = { kind: "agent", id: crew.id };
        const motion=activity==="working"||(activity==="ready"&&(readyMotion.has(crew.id)||(focus?.kind==="agent"&&focus.id===crew.id)));
        return <button key={crew.id} className="graph-agent" style={{ ...pos(layout.agents[crew.id]), ...agentStyle(crew), ["--i" as string]: Math.min(i + 1, 24) }} data-identity={`crew:${crew.id}`} data-state={state} data-activity={activity} data-execution={live?.stage} data-motion={motion} data-listen={listens(crew) && state === "running"} data-dim={!!lit && !lit.agents.has(crew.id)} aria-pressed={picked(sel)}
          {...hoverable(sel)} onClick={() => onSelect(sel)} title={`${crew.name}: ${activity==="waiting"?workerLabel(crew):STATE_WORD[state]}`}>
          <span className="map-face">{initials(crew.name)}{(activity==="ready"||activity==="working")&&
            <svg className="agent-state-ring" viewBox="0 0 40 40" aria-hidden="true">
              <circle className="agent-ring-track" cx="20" cy="20" r="17"/>
              <circle className="agent-ring-motion" cx="20" cy="20" r="17" pathLength="100"/>
            </svg>}</span>
          <span className="graph-agent-text"><b>{crew.name}</b>{live?<span className="agent-execution-line">{executionLabel(live.stage)}</span>:state === "working"
            ? <span className="working-line">Working<i /><i /><i /></span>
            : <span className={activity==="ready"?"agent-ready-line":activity==="waiting"?"agent-waiting-line":undefined}>{state === "draft" ? "Draft · finish setting it up" : activity==="waiting"?workerLabel(crew):activity==="ready"?`${listens(crew)?"Listening":"Running"} · ${triggerLine(crew)}`:triggerLine(crew)}</span>}</span>
          <span className="map-dot" aria-label={STATE_WORD[state]} />
        </button>;
      })}
      <button className="graph-agent graph-new" style={pos(layout.newAgent)} data-dim={!!lit} onClick={onNew}><span className="map-face"><Plus size={15} /></span><b>New agent</b></button>

      {trace.map((t, i) => <span key={traceKey + i} className="trace-dot" data-kind={t.kind} data-local={t.local || undefined} aria-hidden="true"
        style={{ ...colorStyle(t.identity), ["--dot" as string]:"var(--identity)", offsetPath: `path("${t.d}")`, animationDelay: `${t.delay}s` } as CSSProperties} />)}
      {signals.calls.map(call=>{
        const path=call.route_id&&edgePath(call.crew_id,`route:${call.route_id}`);
        return path?<span key={call.call_id} className="provider-request-pulse" aria-hidden="true"
          style={{...colorStyle(`route:${call.route_id}`),offsetPath:`path("${path}")`} as CSSProperties}/>:null;
      })}

    </div>

    <GraphConnections models={brains} nodes={outer} width={connectionWidth} selection={selection}
      lit={lit} onSelect={onSelect} hoverable={hoverable} onAnchors={updateAnchors} providerActivity={signals.providers}/>

    {!!runCards.length&&<div className="graph-run-notices" role="status" aria-live="polite" style={{right:inset+16,maxWidth:Math.max(180,Math.min(280,free-connectionWidth-48))}}>
      {runCards.map(signal=>{
        const crew=ordered.find(c=>c.id===signal.crewId)!;
        const route=routes.find(r=>r.id===signal.routeId);
        const active=['running','requesting'].includes(signal.stage);
        return <button key={signal.runId} className="graph-run-notice" data-stage={signal.stage} style={agentStyle(crew)} onClick={()=>onSelect({kind:'agent',id:crew.id})} onPointerDown={e=>e.stopPropagation()}>
          {active?<LoaderCircle size={14} className="provider-request-spinner" aria-hidden="true"/>:signal.stage==='completed'?<Check size={14} aria-hidden="true"/>:<Minus size={14} aria-hidden="true"/>}
          <span><b>{crew.name}</b><span>{executionLabel(signal.stage)}{signal.stage==='requesting'&&` · ${route?.name??signal.provider??'Model'}`}</span></span>
        </button>;
      })}
    </div>}

    <div className="graph-tools" onPointerDown={e => e.stopPropagation()}>
      <button className="btn-quiet !p-1.5" aria-label="Zoom in" title="Zoom in" onClick={() => zoomBy(1.2)}><Plus size={14} /></button>
      <button className="btn-quiet !p-1.5" aria-label="Zoom out" title="Zoom out" onClick={() => zoomBy(1 / 1.2)}><Minus size={14} /></button>
      <button className="btn-quiet !p-1.5" aria-label="Fit to screen" title="Fit to screen" disabled={!moved} onClick={() => setMoved(false)}><Maximize2 size={13} /></button>
    </div>
    <div className="graph-legend" aria-label="Graph help" style={{ maxWidth: Math.max(160, free - 140) }}>Select an agent to preview its connections.</div>
  </div>;
}

/** Ticks every `ms` while mounted, so only what shows time re-draws. */
function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t); }, [ms]);
  return now;
}

/** Each scheduled agent's clock: a dot travels from you to the agent, and when it arrives the
 *  agent runs. The line behind it fills as it goes. Point at it to see when; press it to run now. */
function RunClocks({ crews, layout, lit, focus, onRun, onOpen, hoverable }: {
  crews: CrewInstance[]; layout: GraphLayout; lit: { agents: Set<string> } | null; focus: string | null;
  onRun?: (id: string) => Promise<void>; onOpen: (id: string) => void;
  /** Pointing at a clock lights up its agent. */
  hoverable: (id: string) => Record<string, () => void>;
}) {
  const now = useNow(1000);
  const [asked, setAsked] = useState<string | null>(null);
  const { c } = layout;
  const items = crews.flatMap(crew => {
    const clock = runClock(crew, now), a = layout.agents[crew.id];
    if (!clock || !a) return [];
    const dx = a.x - c.x, dy = a.y - c.y, len = Math.hypot(dx, dy) || 1;
    // From just outside your circle to the edge of the agent's card.
    const t0 = Math.min(0.4, 40 / len);
    const t1 = Math.max(t0 + 0.1, 1 - Math.min(Math.abs(dx) > 1 ? 112 / Math.abs(dx) : 1, Math.abs(dy) > 1 ? 30 / Math.abs(dy) : 1));
    const t = t0 + (t1 - t0) * clock.progress;
    return [{ crew, clock, len, t0, t1, t, at: { x: c.x + dx * t, y: c.y + dy * t } }];
  });
  if (!items.length) return null;
  const run = async (id: string) => {
    if (!onRun || asked) return;
    setAsked(id);
    try { await onRun(id); } finally { setAsked(null); }
  };
  return <>
    <svg className="graph-wires graph-clocks" width={layout.W} height={layout.H} aria-hidden="true">
      {items.map(({ crew, len, t0, t, clock }) => {
        const on = !lit || lit.agents.has(crew.id);
        return <line key={crew.id} className="clock-trail" data-on={on} data-due={clock.left <= 0}
          x1={c.x} y1={c.y} x2={layout.agents[crew.id].x} y2={layout.agents[crew.id].y}
          style={{ strokeDasharray: `0 ${len * t0} ${Math.max(0, len * (t - t0))} ${len * 2}` }} />;
      })}
    </svg>
    {items.map(({ crew, clock, at }) => {
      const busy = asked === crew.id, can = clock.canRunNow && !!onRun;
      const say = `${crew.name} runs ${clock.label}, ${clock.every}.` + (can ? " Press to run it now." : "");
      return <button key={crew.id} className="run-clock" style={{ left: at.x, top: at.y }} data-due={clock.left <= 0} data-busy={busy}
        data-show={focus === crew.id} data-dim={!!lit && !lit.agents.has(crew.id)} aria-label={say} title={say} {...hoverable(crew.id)}
        onClick={() => can ? void run(crew.id) : onOpen(crew.id)}>
        <i />
        <span className="run-clock-text"><b>{busy ? "Starting…" : clock.label}</b>{!busy && <em>{can ? "Run now" : clock.every}</em>}</span>
      </button>;
    })}
  </>;
}
