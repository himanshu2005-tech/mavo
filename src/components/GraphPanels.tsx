import { Brain, ChevronDown, Cloud, Cpu, LoaderCircle, Plus, Settings2, ShieldAlert, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ActionAccessEditor, type ActionRule } from "./ActionAccessEditor";
import { CrewDetail } from "./CrewDetail";
import { LineSettings } from "./LinePanel";
import { agentStyle } from "./CrewFan";
import { agentState, type GraphNode } from "./AgentGraph";
import { workerLabel } from "../lib/crewStatus";
import { WireIcon } from "./WireIcon";
import { wiring } from "./crewWiring";
import { api } from "../lib/api";
import { identityColor, identityStyle } from "../lib/identityColors";
import { accessOptions, changeAccess, isRisky, loadAccessContext, poolIdFor, saveSetup, type AccessContext, type AccessOption } from "../lib/agentAccess";
import { initials, myInitials } from "../lib/radialGraph";
import { useAppStore } from "../store/app";
import { fallbackSummary, usesModel } from "../lib/modelMap";
import type { AccessLevel, CrewInstance, MemoryFact, PoolResource } from "../types";

/** What Mavo has connected, loaded once and again when the window comes back. */
export function useAccessContext() {
  const [ctx, setCtx] = useState<AccessContext | null>(null);
  const load = useCallback(() => { void loadAccessContext().then(setCtx).catch(() => setCtx({ google: [], connectors: [], weather: false })); }, []);
  useEffect(() => { load(); window.addEventListener("focus", load); return () => window.removeEventListener("focus", load); }, [load]);
  return ctx;
}

async function refreshCrew() {
  try { useAppStore.getState().setCrew(await api.crew()); } catch { /* the next poll brings it */ }
}

const ASK: Record<string, string> = {
  "gmail.send": "send email from your Gmail", "gmail.tidy": "archive, mark read or trash your email", "calendar.edit": "create, change and delete calendar events",
  "tasks.edit": "create, change and delete your tasks", "windows.sleep": "put this computer to sleep", "windows.shutdown": "shut down this computer (with a 60-second warning)",
  "windows.apps": "type and press keys in other apps", "terminal.run": "run commands on this computer", "project.run": "run project scripts", project: "write files in your projects", "web.act": "click and type on websites",
};
const askFor = (key: string, label: string) => ASK[key] ?? (key.startsWith("act:") ? `click and type on ${key.slice(4)}` : label.toLowerCase());

type Pending = { crew: CrewInstance; option: AccessOption } & ({ step: "confirm" } | { step: "setup"; op: string; rule: ActionRule });

/** One switch in a list: an agent's access to one thing, with its confirm and set-up steps. */
function useSwitch(ctx: AccessContext | null) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  async function run(crew: CrewInstance, option: AccessOption, on: boolean) {
    if (!ctx) return;
    setBusy(crew.id + option.key); setError(null);
    try {
      const result = await changeAccess(crew, option.key, on, ctx);
      if ("setup" in result) setPending({ crew, option, step: "setup", op: result.setup, rule: result.rule });
      else { setPending(null); await refreshCrew(); }
    } catch (e) { setError(e instanceof Error ? e.message : "Could not change that."); }
    finally { setBusy(null); }
  }
  function flip(crew: CrewInstance, option: AccessOption) {
    const on = !option.on;
    if (on && option.risky) { setPending({ crew, option, step: "confirm" }); return; }
    void run(crew, option, on);
  }
  async function saveRule(rule: ActionRule) {
    if (!pending || pending.step !== "setup") return;
    setBusy(pending.crew.id + pending.option.key); setError(null);
    try { await saveSetup(pending.crew, pending.option.key, pending.op, rule); setPending(null); await refreshCrew(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save that."); }
    finally { setBusy(null); }
  }
  return { busy, error, pending, setPending, run, flip, saveRule };
}

function Step({ sw, crew, option }: { sw: ReturnType<typeof useSwitch>; crew: CrewInstance; option: AccessOption }) {
  const [rule, setRule] = useState<ActionRule | null>(null);
  const p = sw.pending;
  useEffect(() => { setRule(p && p.step === "setup" ? p.rule : null); }, [p]);
  if (!p || p.crew.id !== crew.id || p.option.key !== option.key) return null;
  const working = sw.busy === crew.id + option.key;
  if (p.step === "confirm") return <div className="access-step" role="alertdialog" aria-label="Confirm">
    <p><ShieldAlert size={14} />Let <b>{crew.name}</b> {askFor(option.key, option.label)}?</p>
    <div className="flex gap-2"><button className="btn-primary !py-1" disabled={working} onClick={() => void sw.run(crew, option, true)}>{working && <LoaderCircle size={12} className="animate-spin" />}Allow</button><button className="btn-quiet !py-1" onClick={() => sw.setPending(null)}>Cancel</button></div>
  </div>;
  return <div className="access-step" aria-label={"Set up " + option.label}>
    <p><Settings2 size={14} />Choose its limits first.</p>
    {rule && <ActionAccessEditor operation={p.op} value={rule} onChange={setRule} />}
    <div className="mt-2 flex gap-2"><button className="btn-primary !py-1" disabled={working || !rule} onClick={() => rule && void sw.saveRule(rule)}>{working && <LoaderCircle size={12} className="animate-spin" />}Allow with these limits</button><button className="btn-quiet !py-1" onClick={() => sw.setPending(null)}>Cancel</button></div>
  </div>;
}

function SwitchRow({ label, icon, hint, on, disabled, busy, onFlip, lead }: { label: string; icon: ReactNode; hint?: string; on: boolean; disabled?: boolean; busy?: boolean; onFlip: () => void; lead?: ReactNode }) {
  return <div className="access-row">
    {lead ?? <span className="map-icon">{icon}</span>}
    <span className="min-w-0 flex-1"><span className="access-label">{label}</span>{hint && <span className="access-hint">{hint}</span>}</span>
    {busy ? <LoaderCircle size={14} className="animate-spin text-zinc-500" /> : <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className="resource-switch" onClick={onFlip}><span /></button>}
  </div>;
}

/** An agent's switches: what it reads and what it can do. What it does not use is one tap away. */
export function AgentAccess({ crew }: { crew: CrewInstance }) {
  const ctx = useAccessContext();
  const sw = useSwitch(ctx);
  const [more, setMore] = useState(false);
  if (!ctx) return <p className="access-loading"><LoaderCircle size={13} className="animate-spin" />Loading access…</p>;
  const options = accessOptions(crew, ctx);
  const shown = (kind: AccessOption["kind"]) => options.filter(o => o.kind === kind && (more || o.on || (sw.pending?.option.key === o.key)));
  const hidden = options.filter(o => !o.on).length;
  const group = (kind: AccessOption["kind"], title: string, empty: string) => <div>
    <h4 className="graph-h">{title}</h4>
    {shown(kind).length ? <div className="access-list">{shown(kind).map(o => <div key={o.key}>
      <SwitchRow label={o.label} icon={<WireIcon name={o.icon} />} hint={o.blocked ?? (wiringAsked(crew, o.key) ? "Asked for, not allowed yet" : undefined)} on={o.on} disabled={!!o.blocked && !o.on} busy={sw.busy === crew.id + o.key} onFlip={() => sw.flip(crew, o)} />
      <Step sw={sw} crew={crew} option={o} />
    </div>)}</div> : <p className="graph-empty">{empty}</p>}
  </div>;
  return <section className="access-box" aria-label="Access" data-kind="agent">
    {group("resource", "Reads", "Nothing of yours.")}
    {group("action", "Can do", "Only report back to you.")}
    {hidden > 0 && <button className="btn-quiet access-more" aria-expanded={more} onClick={() => setMore(m => !m)}><ChevronDown size={13} style={{ transform: more ? "rotate(180deg)" : undefined }} />{more ? "Show only what it uses" : `Give it more (${hidden})`}</button>}
    {sw.error && <p role="alert" className="notice error-notice">{sw.error}</p>}
  </section>;
}

/** It asked for this (its setup requested it) and was not given it yet. */
function wiringAsked(crew: CrewInstance, key: string): boolean {
  const { reads, does } = wiring(crew);
  return [...reads, ...does].some(w => w.key === key && !w.allowed);
}

/** The panel's head: a face or icon, a name, a line under it, and close. */
function Head({ lead, title, sub, onClose }: { lead: ReactNode; title: string; sub?: ReactNode; onClose: () => void }) {
  return <div className="panel-head">{lead}<div className="min-w-0 flex-1"><h3>{title}</h3>{sub && <div className="graph-sub">{sub}</div>}</div>
    <button className="btn-quiet !p-1.5" aria-label="Close" onClick={onClose}><X size={15} /></button></div>;
}

/** Which model the agent thinks with, and the last time that model could not do a run. */
function ThinksWith({ crew }: { crew: CrewInstance }) {
  const route = useAppStore(s => s.routes.find(r => r.id === crew.route_id));
  if (!usesModel(crew)) return <div className="thinks-with">Local routine · No model calls</div>;
  if (!route) return null;
  const local = route.provider === "ollama";
  return <div className="thinks-with">
    <span className="thinks-line">{local ? <Cpu size={12} aria-hidden="true" /> : <Cloud size={12} aria-hidden="true" />}Thinks with <b>{route.name}</b> · {route.model}{local ? " · on this PC" : ""}</span>
    {crew.fallback && <span className="thinks-fell">{fallbackSummary(crew.fallback)}</span>}
  </div>;
}

export function AgentPanel({ crew, onClose }: { crew: CrewInstance; onClose: () => void }) {
  const state = agentState(crew);
  return <>
    <Head lead={<span className="map-face lg" style={agentStyle(crew)}>{initials(crew.name)}</span>} title={crew.name}
      sub={<span className="map-state" data-state={state}><i />{state === "draft" ? "Draft" : state === "done" ? "Done" : workerLabel(crew)}</span>} onClose={onClose} />
    <ThinksWith crew={crew} />
    <CrewDetail key={crew.id} crew={crew} access={crew.status === "draft" ? undefined : <>{!crew.configuration_plan?.leads_team && <AgentAccess crew={crew} />}<LineSettings crew={crew} /></>} />
  </>;
}

const LEVELS: { id: AccessLevel; label: string }[] = [{ id: "off", label: "Off" }, { id: "read", label: "Read" }, { id: "write", label: "Read & act" }];

/** A resource or action: Mavo's overall level for it, and every agent with a switch. */
export function NodePanel({ node, crews, onPick, onClose }: { node: GraphNode; crews: CrewInstance[]; onPick: (id: string) => void; onClose: () => void }) {
  const ctx = useAccessContext();
  const sw = useSwitch(ctx);
  const [pool, setPool] = useState<PoolResource | null>(null);
  const [poolBusy, setPoolBusy] = useState(false);
  const [poolError, setPoolError] = useState<string | null>(null);
  const poolId = poolIdFor(node.key);
  useEffect(() => { let on = true; setPool(null); if (poolId) api.pool().then(p => { if (on) setPool(p.resources.find(r => r.id === poolId) ?? null); }).catch(() => undefined); return () => { on = false; }; }, [poolId]);
  async function level(next: AccessLevel) {
    if (!pool || pool.level === next) return;
    setPoolBusy(true); setPoolError(null);
    try { setPool(await api.setPoolLevel(pool.id, next)); } catch (e) { setPoolError(e instanceof Error ? e.message : "Could not change that."); } finally { setPoolBusy(false); }
  }
  const agents = crews.filter(c => c.status !== "draft");
  const rows = ctx ? agents.map(crew => ({ crew, option: accessOptions(crew, ctx).find(o => o.key === node.key) ?? { key: node.key, label: node.label, icon: node.icon, kind: node.kind, on: false, risky: node.kind === "action" && isRisky(node.key), blocked: "Not available for this agent" } })) : [];
  const using = rows.filter(r => r.option.on).length;
  return <>
    <Head lead={<span className="map-icon lg" data-kind={node.kind} style={{...identityStyle(identityColor(`${node.kind}:${node.key}`)),color:'var(--identity)',background:'color-mix(in srgb,var(--identity) 12%,transparent)'}}><WireIcon name={node.icon} size={18} /></span>} title={node.label}
      sub={node.kind === "resource" ? `Resource · Read by ${using} agent${using === 1 ? "" : "s"}` : `Action · ${using} agent${using === 1 ? "" : "s"} can do this`} onClose={onClose} />
    {pool && <div>
      <h4 className="graph-h">For all of Mavo</h4>
      <p className="graph-sub">{pool.connected ? pool.description : (pool.kind === "google" || pool.kind === "github" || pool.kind === "feeds") ? "Not connected yet." : pool.description}</p>
      {pool.connected ? <div className="segmented mt-2" role="radiogroup" aria-label={pool.name + " access"}>
        {LEVELS.filter(l => pool.reads.length || l.id !== "read").map(l => <button key={l.id} type="button" role="radio" aria-checked={pool.level === l.id} aria-pressed={pool.level === l.id} disabled={poolBusy || (l.id === "write" && !pool.can_act)} onClick={() => void level(l.id)}>{!pool.reads.length && l.id === "write" ? "On" : l.label}</button>)}
      </div> : <button className="btn-secondary mt-2 !py-1.5" onClick={() => useAppStore.getState().setPage("resources")}>Connect in Accounts</button>}
      {poolError && <p role="alert" className="notice error-notice mt-2">{poolError}</p>}
    </div>}
    <div>
      <h4 className="graph-h">{node.kind === "resource" ? "Which agents read it" : "Which agents can do it"}</h4>
      {!ctx ? <p className="access-loading"><LoaderCircle size={13} className="animate-spin" />Loading…</p> : !rows.length ? <p className="graph-empty">No agents yet.</p>
        : <div className="access-list">{rows.map(({ crew, option }) => <div key={crew.id}>
          <SwitchRow label={crew.name} icon={null} hint={option.blocked && !option.on ? option.blocked : undefined} on={option.on} disabled={!!option.blocked && !option.on} busy={sw.busy === crew.id + option.key} onFlip={() => sw.flip(crew, option)}
            lead={<button className="map-face sm" style={agentStyle(crew)} title={"Open " + crew.name} onClick={() => onPick(crew.id)}>{initials(crew.name)}</button>} />
          <Step sw={sw} crew={crew} option={option} />
        </div>)}</div>}
      {sw.error && <p role="alert" className="notice error-notice mt-2">{sw.error}</p>}
    </div>
  </>;
}

/** You: what Mavo remembers about you (every agent knows it), and how much is set up. */
export function MePanel({ me, memory, agents, onRemember, onForget, onClose }: {
  me: string; memory: MemoryFact[]; agents: number;
  onRemember: (text: string) => Promise<void>; onForget: (id: string) => Promise<void>; onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function add(e: FormEvent) {
    e.preventDefault(); if (text.trim().length < 2) return;
    setBusy("add"); setError(null);
    try { await onRemember(text.trim()); setText(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not keep that."); } finally { setBusy(null); }
  }
  async function forget(id: string) {
    setBusy(id);
    try { await onForget(id); } catch { /* stays */ } finally { setBusy(null); }
  }
  return <>
    <Head lead={<span className="graph-me-face sm">{myInitials(me)}</span>} title={me} sub={`${agents} agent${agents === 1 ? "" : "s"} work for you`} onClose={onClose} />
    <div>
      <h4 className="graph-h"><Brain size={13} />Shared memory</h4>
      <p className="graph-sub">What you tell any agent about yourself, every agent knows. Tell one "my mom is Sita", or add it here.</p>
      {memory.length ? <ul className="graph-facts">{memory.map(f => <li key={f.id}><span className="min-w-0 flex-1"><span>{f.text}</span><small>from {f.source}</small></span>
        <button className="btn-quiet !p-1" aria-label={`Forget: ${f.text}`} disabled={busy === f.id} onClick={() => void forget(f.id)}><X size={12} /></button></li>)}</ul>
        : <p className="graph-empty">Nothing yet.</p>}
      <form className="graph-add" onSubmit={e => void add(e)}>
        <input className="form-control !mt-0 !py-1.5" placeholder="e.g. My mom is Sita" value={text} maxLength={240} onChange={e => setText(e.target.value)} aria-label="Tell Mavo something about you" />
        <button className="btn-secondary !py-1.5" disabled={busy === "add" || text.trim().length < 2}>{busy === "add" ? <LoaderCircle size={13} className="animate-spin" /> : <Plus size={13} />}Add</button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12px] text-rose-300">{error}</p>}
    </div>
    <div className="flex flex-wrap gap-2">
      <button className="btn-secondary !py-1.5" onClick={() => useAppStore.getState().setPage("resources")}>Accounts &amp; resources</button>
      <button className="btn-quiet !py-1.5" onClick={() => useAppStore.getState().setPage("actions")}>Default action rules</button>
    </div>
  </>;
}
