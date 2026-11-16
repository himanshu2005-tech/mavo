import { BookOpen, Boxes, Brain as BrainIcon, CalendarClock, Cloud, Cpu, History, ListChecks, Map as MapIcon, MessageCircle, MessageSquare, PanelRightClose, Search, Settings, X, Zap, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { AgentGraph, agentState, useGraphModel, type Brain, type Selection } from "../components/AgentGraph";
import { needsReason } from "../components/CrewQuickList";
import { AgentPanel, MePanel, NodePanel } from "../components/GraphPanels";
import { MemberPanel } from '../components/CrewMembers';
import { AgentTraffic, useAgentMessages } from '../components/AgentTraffic';
import {connectionInventory} from '../lib/connectionInventory';
import { Brand } from "../components/Brand";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { PageHeader } from "../components/PageHeader";
import { StatusPill } from "../components/StatusPill";
import { api } from "../lib/api";
import { identityColor, identityStyle } from "../lib/identityColors";
import { routeAvailability, usesModel } from "../lib/modelMap";
import { OPEN_CREW, takeOpenCrew } from "../lib/openCrew";
import { useAppStore } from "../store/app";
import type { CrewInstance, MemoryFact, NavId, TaskRun,PoolResource } from "../types";
import { taskOnMap, visibleTasks, type TaskOnMap } from "../lib/taskTeams";
import { watchRun } from "../lib/runOutcome";


import { TaskRunCard } from "../components/TaskRunCard";
import { Rooms } from "../components/Rooms";
import { ActionsPage } from "./ActionsPage";
import { LibraryPage } from "./LibraryPage";
import { KnowledgePage } from "./KnowledgePage";
import { ChatPage } from "./ChatPage";
import { AgentInbox } from "../components/AgentInbox";
import { ActivityView } from "./EmptyPages";
import { ResourcesPage } from "./ResourcesPage";
import { SettingsPage } from "./SettingsPage";
import { TimelineView } from "./TimelinePage";

/** Mavo is one screen: you and your agents as a graph, the chat beside it, and a panel for
 *  whatever you pick. Settings, accounts and history open over it. */
export function Home({ previewTasks }: { previewTasks?: TaskRun[] } = {}) {
  const { crew, setupDrafts, page, chatOpen, focusAgent, statusError,execution,executionError } = useAppStore();
  const setPage = useAppStore(s => s.setPage);
  const [selection, setSelection] = useState<Selection>(null);
  // Rooms (a list of your agents and a timeline for each) or the map, the overview. Remembered.
  const [view, setViewState] = useState<"rooms" | "map">(() => { try { return localStorage.getItem("mavo-view") === "map" ? "map" : "rooms"; } catch { return "rooms"; } });
  const setView = (next: "rooms" | "map") => { setViewState(next); try { localStorage.setItem("mavo-view", next); } catch { /* for now only */ } };
  const messages=useAgentMessages();
  const [poolResources,setPoolResources]=useState<PoolResource[]>([]);
  const [connectionsLoading,setConnectionsLoading]=useState(true);
  const [connectionsError,setConnectionsError]=useState<string|null>(null);
  useEffect(()=>{
    let live=true,inFlight=false;
    const epoch=useAppStore.getState().workspaceEpoch;
    const refresh=async()=>{
      if(inFlight||document.hidden)return;inFlight=true;
      const current=()=>live&&epoch===useAppStore.getState().workspaceEpoch;
      const results=await Promise.allSettled([api.routes(),api.pool()]);
      if(current()){
        if(results[0].status==='fulfilled')useAppStore.getState().setRoutes(results[0].value);
        if(results[1].status==='fulfilled')setPoolResources(results[1].value.resources??[]);
        setConnectionsError(results.some(r=>r.status==='rejected')?'Could not refresh connections. Saved entries may be out of date.':null);
        setConnectionsLoading(false);
      }
      inFlight=false;
    };
    void refresh();const timer=window.setInterval(()=>void refresh(),10000);
    const focus=()=>void refresh();window.addEventListener('focus',focus);
    return()=>{live=false;window.clearInterval(timer);window.removeEventListener('focus',focus);};
  },[page]);
  const inventory=useMemo(()=>connectionInventory(poolResources),[poolResources]);
  const [query, setQuery] = useState("");
  const [me, setMe] = useState("You");
  const [memory, setMemory] = useState<MemoryFact[]>([]);
  const [runError, setRunError] = useState<string | null>(null);
  const [runNote, setRunNote] = useState<string | null>(null);
  // One-time tasks (a build, a research job) as temporary teams on the map: looked at often while
  // one works, now and then otherwise.
  const [taskRuns, setTaskRuns] = useState<TaskRun[]>([]);
  useEffect(() => {
    if (previewTasks) { setTaskRuns(previewTasks); return; }
    let active = true, timer = 0;
    const load = async () => {
      let busy = false;
      try { const runs = await api.tasks(); if (active) setTaskRuns(runs); busy = runs.some(r => r.status === "running"); } catch { /* not up yet */ }
      if (active) timer = window.setTimeout(() => void load(), busy ? 4000 : 15000);
    };
    void load();
    return () => { active = false; window.clearTimeout(timer); };
  }, [previewTasks]);
  const tasks = useMemo(() => visibleTasks(taskRuns).map(taskOnMap), [taskRuns]);
  // "Run now" on an agent's clock: the same as Check now in its panel.
  // It then says how the run ended ("checked: nothing new", or what it did), so it never looks
  // like nothing happened.
  const runNow = async (id: string) => {
    const agent = crew.find(c => c.id === id);
    try { useAppStore.getState().upsertCrew(await api.checkCrew(id)); setRunError(null); }
    catch (reason) { setRunError(reason instanceof Error ? reason.message : "Could not run it."); window.setTimeout(() => setRunError(null), 6000); return; }
    void watchRun(id, agent?.name ?? "The agent", agent?.trigger_type, setRunNote);
  };
  const model = useGraphModel(crew, setupDrafts,inventory);
  const routes = useAppStore(s => s.routes);
  const providers = useAppStore(s => s.status?.providers);
  const workspaceEpoch = useAppStore(s => s.workspaceEpoch);
  const [routeChecks, setRouteChecks] = useState<Record<string, { signature: string; connected: boolean; epoch: number }>>({});
  const brains = useMemo(() => routes.map(r => {
    const signature = `${r.provider}:${r.model}:${r.updated_at}`;
    const checked = routeChecks[r.id];
    return { id: r.id, name: r.name, provider: r.provider, model: r.model, signature,
      connected: checked?.epoch === workspaceEpoch && checked.signature === signature
        ? checked.connected : routeAvailability(r, providers) };
  }), [routes, providers, routeChecks, workspaceEpoch]);
  const routeChecked = (route: Brain, connected: boolean) => setRouteChecks(previous => ({
    ...previous, [route.id]: { signature: route.signature ?? "", connected, epoch: workspaceEpoch },
  }));

  useEffect(() => { api.googleStatus().then(s => { if (s.name?.trim()) setMe(s.name.trim()); }).catch(() => undefined); }, []);
  // A fact can be learned anywhere (any agent's Ask box, the chat): look again when a chat
  // changes, when the window comes back, and now and then.
  const conversations = useAppStore(s => s.conversations);
  useEffect(() => { api.memory().then(setMemory).catch(() => undefined); }, [conversations]);
  useEffect(() => {
    const load = () => { api.memory().then(setMemory).catch(() => undefined); };
    const timer = window.setInterval(load, 15000);
    window.addEventListener("focus", load);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", load); };
  }, []);
  // Something asked to show an agent (a card in chat, a desktop alert): pick it.
  useEffect(() => { if (focusAgent) { setSelection({ kind: "agent", id: focusAgent }); useAppStore.getState().openAgent(null); } }, [focusAgent]);
  // "Open" on a desktop card lands here, on that agent.
  useEffect(() => {
    const take = () => { const id = takeOpenCrew(); if (id) setSelection({ kind: "agent", id }); };
    const onStorage = (e: StorageEvent) => { if (e.key === OPEN_CREW && e.newValue) take(); };
    take();
    window.addEventListener("storage", onStorage); window.addEventListener("focus", take);
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("focus", take); };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || page !== "home" || document.querySelector(".sheet-backdrop, [role=dialog][aria-modal=true]")) return;
      if (query) setQuery(""); else setSelection(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [page, query]);

  const hits = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const agents = new Set(model.ordered.filter(c => (c.name + " " + c.responsibilities).toLowerCase().includes(q)).map(c => c.id));
    const keys = new Set(model.outer.filter(n => n.label.toLowerCase().includes(q)).map(n => n.key));
    brains.filter(b => `${b.name} ${b.provider} ${b.model}`.toLowerCase().includes(q)).forEach(b => {
      keys.add("route:" + b.id);
      model.ordered.filter(c => c.route_id === b.id || c.fallback_route_id === b.id).forEach(c => agents.add(c.id));
    });
    return { agents, keys };
  }, [query, model, brains]);
  const hitCount = hits ? hits.agents.size + hits.keys.size : 0;
  // Enter in search goes to the first match, and again to the next: with hundreds of agents this
  // is the quick way across the map. The view slides to it and its panel opens.
  const jump = useRef(-1);
  const goToNextHit = () => {
    if (!hits) return;
    const all: Selection[] = [
      ...model.ordered.filter(c => hits.agents.has(c.id)).map(c => ({ kind: "agent" as const, id: c.id })),
      ...model.outer.filter(n => hits.keys.has(n.key)).map(n => ({ kind: n.kind, key: n.key })),
    ];
    if (!all.length) return;
    jump.current = (jump.current + 1) % all.length;
    setSelection(all[jump.current]);
  };
  // Agents waiting for you (a question, a problem, setup): one press shows the first; its panel
  // says what it needs and lets you answer.
  const waiting = model.ordered.filter(c => c.status !== "draft" && agentState(c) === "needs");

  const agent = selection?.kind === "agent" ? model.ordered.find(c => c.id === selection.id) : undefined;
  const memberCrew=selection?.kind==='member'?model.ordered.find(c=>c.id===selection.crewId):undefined;
  const member=selection?.kind==='member'?memberCrew?.agents?.find(a=>a.id===selection.id):undefined;
  const node = selection?.kind === "resource" || selection?.kind === "action" ? model.outer.find(n => n.key === selection.key&&n.kind===selection.kind) : undefined;
  const close = () => setSelection(null);
  const taskShown = selection?.kind === "task" ? tasks.find(t => t.run.id === selection.id) : undefined;
  // Mavo's models, with whether each can be reached now (Ollama not running, a key missing).
  const brainShown = selection?.kind === "route" ? brains.find(b => "route:" + b.id === selection.key) : undefined;
  const panel = selection?.kind === "me" ? <MePanel me={me} memory={memory} agents={crew.length} onClose={close}
      onRemember={async text => setMemory(await api.addMemory(text))} onForget={async id => setMemory(await api.forgetMemory(id))} />
    : agent ? <AgentPanel crew={agent} onClose={close} />
    : member&&memberCrew?<MemberPanel crew={memberCrew} agent={member} onClose={close}/>
    : selection?.kind === "task" && taskShown ? <TaskPanel task={taskShown} onClose={close} onDeleted={id => { setTaskRuns(runs => runs.filter(r => r.id !== id)); close(); }} />
    : brainShown ? <RoutePanel key={workspaceEpoch + brainShown.id + brainShown.signature} route={brainShown} all={brains} crews={model.ordered} onTest={routeChecked} onPick={id => setSelection({ kind: "agent", id })} onClose={close} />
    : selection?.kind==='handoff'?<section className="p-5"><button className="btn-quiet" onClick={close}>Close</button><h3 className="mb-3">Dependency & delivery</h3><p className="text-sm text-zinc-400 mb-3">Work passes between these agents under each agent’s existing access.</p><AgentTraffic messages={messages.filter(m=>[`${m.sender.replace(/^crew:/,'')}|${m.recipient.replace(/^crew:/,'')}`,`${m.recipient.replace(/^crew:/,'')}|${m.sender.replace(/^crew:/,'')}`].includes(selection.key))}/></section>
    : node ? <NodePanel node={node} crews={model.ordered} onPick={id => setSelection({ kind: "agent", id })} onClose={close} />
    : null;

  return <div className="home">
    <header className="home-bar">
      <Brand />
      {/* Chat or map, in words, always in the same place: the way back was one small icon. */}
      <div className="view-switch" role="tablist" aria-label="Chat or map">
        <button role="tab" aria-selected={view === "rooms"} onClick={() => setView("rooms")}>{view === "rooms" && <motion.span layoutId="view-pill" className="view-pill" transition={{ type: "spring", stiffness: 520, damping: 40 }} />}<MessageSquare size={14} /><span>Chat</span></button>
        <button role="tab" aria-selected={view === "map"} onClick={() => setView("map")}>{view === "map" && <motion.span layoutId="view-pill" className="view-pill" transition={{ type: "spring", stiffness: 520, damping: 40 }} />}<MapIcon size={14} /><span>Map</span></button>
      </div>
      <div className="home-search"><Search size={13} aria-hidden="true" /><input aria-label="Find an agent, account or action" placeholder="Find an agent, account or action" value={query} onChange={e => { setQuery(e.target.value); jump.current = -1; }}
          onKeyDown={e => { if (e.key === "Enter" && view === "map") { e.preventDefault(); goToNextHit(); } }} />
        {query && <><span className="home-search-count">{hitCount ? `${hitCount} found · Enter` : "None"}</span><button className="btn-quiet !p-0.5" aria-label="Clear search" onClick={() => setQuery("")}><X size={12} /></button></>}</div>
      <div className="flex-1" />
      {waiting.length > 0 && <button className="bar-needs" onClick={() => view === "rooms" ? useAppStore.getState().openAgent(waiting[0].id) : setSelection({ kind: "agent", id: waiting[0].id })}
        title={waiting.map(c => `${c.name}: ${needsReason(c)}`).join("\n")}>
        {waiting.length === 1 ? `${waiting[0].name} needs you` : `${waiting.length} agents need you`}</button>}
      <StatusPill />
      <BarButton icon={BookOpen} label="Library" onClick={() => setPage("library")} />
      <BarButton icon={BrainIcon} label="Knowledge" onClick={() => setPage("knowledge")} />
      <BarButton icon={CalendarClock} label="What's next" onClick={() => setPage("timeline")} />
      <BarButton icon={History} label="What happened" onClick={() => setPage("activity")} />
      <BarButton icon={Settings} label="Settings" onClick={() => setPage("settings")} />
      {view === "map" && <BarButton icon={MessageCircle} label={chatOpen ? "Hide chat" : "Chat"} pressed={chatOpen} onClick={() => useAppStore.getState().setChatOpen(!chatOpen)} />}
    </header>
    {(statusError || runError || executionError) && <p role="alert" className="home-alert">{statusError || runError || executionError}</p>}
    {runNote && !(statusError || runError || executionError) && <p role="status" className="home-alert home-note">{runNote}</p>}
    {view === "rooms" ? <ErrorBoundary><Rooms query={query} onOverview={() => setView("map")} /></ErrorBoundary> : <div className="home-body">
      <main className="home-stage" aria-label="Your agents">
        <ErrorBoundary><AgentGraph crews={crew} drafts={setupDrafts} me={me} memory={memory} selection={selection} hits={hits} inset={panel ? 424 : 0} onSelect={setSelection} onRun={runNow} tasks={tasks} routes={brains} execution={execution} messages={messages} inventory={inventory} connectionsLoading={connectionsLoading} connectionsError={connectionsError}
          onNew={() => useAppStore.getState().startInChat("Make an agent that ")} /></ErrorBoundary>
        {!crew.length && !setupDrafts.length && <div className="home-hint"><b>Make your first agent</b><span>Tell Mavo in the chat what to handle: "summarise my important mail every morning", "remind me to drink water every hour". It appears here, connected to what it uses.</span></div>}
        {panel && <aside className="graph-side" aria-label="Details"><ErrorBoundary key={JSON.stringify(selection)}>{panel}</ErrorBoundary></aside>}
      </main>
      {chatOpen && <aside className="chat-dock" aria-label="Chat"><ChatDock /></aside>}
    </div>}
    {page !== "home" && <WorkspaceSheet tab={page} onTab={setPage} onClose={() => setPage("home")} />}
  </div>;
}

/** General chat is the primary surface. Keep the older inbox available for existing workflows. */
function ChatDock() {
  const tab = useAppStore(s => s.chatTab);
  const waiting = useAppStore(s => s.crew.filter(c => c.held || (c.question && c.status !== "draft")).length);
  const set = useAppStore.getState().setChatTab;
  const hide = () => useAppStore.getState().setChatOpen(false);
  const tabs = <div className="dock-tabs" role="tablist" aria-label="Chat or updates">
    <button role="tab" aria-selected={tab === "chat"} onClick={() => set("chat")}>Chat</button>
    <button role="tab" aria-selected={tab === "inbox"} onClick={() => set("inbox")}>Updates{waiting > 0 && <i className="dock-count">{waiting}</i>}</button>
  </div>;
  if (tab !== "inbox") return <ErrorBoundary key="chat"><ChatPage docked tabs={tabs} onClose={hide} /></ErrorBoundary>;
  return <div className="chat-dock-inner">
    <header className="chat-dock-bar">{tabs}<div className="flex-1" />
      <button className="btn-quiet !p-1.5" aria-label="Hide chat" title="Hide chat" onClick={hide}><PanelRightClose size={15} /></button></header>
    <ErrorBoundary key="inbox"><AgentInbox /></ErrorBoundary>
  </div>;
}

/** One of Mavo's models: what it is, a test, and the agents that use it (each can be moved to
 *  another model). */
export function RoutePanel({ route, all, crews, onPick, onClose, onTest }: { route: Brain; all: Brain[]; crews: CrewInstance[]; onPick: (id: string) => void; onClose: () => void; onTest?: (route: Brain, connected: boolean) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<{ model: string; ok: boolean } | null>(null);
  const local = route.provider === "ollama";
  const users = crews.filter(c => usesModel(c) && (c.route_id === route.id || c.fallback_route_id === route.id) && c.status !== "draft");
  async function save(crew: CrewInstance, change: { route_id?: string; fallback_route_id?: string | null }) {
    const epoch = useAppStore.getState().workspaceEpoch;
    setBusy(crew.id); setError(null);
    try {
      const updated = await api.updateCrew(crew.id, change);
      if (useAppStore.getState().workspaceEpoch === epoch) useAppStore.getState().upsertCrew(updated);
    }
    catch (e) { setError(e instanceof Error ? e.message : "Could not move it."); }
    finally { setBusy(null); }
  }
  async function check() {
    setBusy("test"); setError(null); setTest(null);
    try { await api.testRoute(route.id); setTest({ model: route.model, ok: true }); onTest?.(route, true); }
    catch { setTest({ model: route.model, ok: false }); onTest?.(route, false); }
    finally { setBusy(null); }
  }
  const tested = test?.model === route.model ? test : null;
  const line = tested ? (tested.ok ? "It replied. Working." : "It didn't reply. Check it in Settings.")
    : route.connected === false ? (local ? "Ollama isn't running, or this model isn't installed." : "Can't be reached. Check its key in Settings.")
    : null;
  return <>
    <div className="panel-head"><span className="map-icon lg graph-route-icon" data-local={local} style={{...identityStyle(identityColor(`route:${route.id}`)),color:'var(--identity)',background:'color-mix(in srgb,var(--identity) 12%,transparent)'}}>{local ? <Cpu size={18} /> : <Cloud size={18} />}</span>
      <div className="min-w-0 flex-1"><h3>{route.name}</h3>
        <div className="graph-sub">{route.model} · {local ? "on this PC" : "cloud"}</div></div>
      <button className="btn-quiet !p-1.5" aria-label="Close" onClick={onClose}><X size={15} /></button></div>
    <div className="route-simple">
      {line && <p className="route-line" role="status" data-bad={!tested?.ok}>{line}</p>}
      <button className="btn-secondary justify-self-start" disabled={!!busy} onClick={() => void check()}>{busy === "test" ? "Testing…" : "Test"}</button>
      <section>
        <h4 className="graph-h">Used by {users.length}</h4>
        {!users.length && <p className="route-line">No agents yet.</p>}
        <ul>{users.map(c => <li key={c.id}>
          <button onClick={() => onPick(c.id)}>{c.name}{c.route_id !== route.id && <span className="route-fell">backup</span>}</button>
          {all.length > 1 && <select className="field !w-auto" aria-label={`Model for ${c.name}`} value={c.route_id ?? ""} disabled={!!busy} onChange={e => void save(c, { route_id: e.target.value })}>
            {all.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select>}
          {all.find(b => b.id === c.route_id)?.provider === "ollama" && all.some(b => b.provider !== "ollama") && <select className="field !w-auto" aria-label={`Cloud fallback for ${c.name}`} title="If this PC's model fails, use…" value={c.fallback_route_id ?? ""} disabled={!!busy}
            onChange={e => void save(c, { fallback_route_id: e.target.value || null })}>
            <option value="">No backup</option>
            {all.filter(b => b.provider !== "ollama").map(b => <option key={b.id} value={b.id}>Backup: {b.name}</option>)}
          </select>}
        </li>)}</ul>
      </section>
      {error && <p role="alert" className="access-hint">{error}</p>}
    </div>
  </>;
}

/** A one-time task's panel: the same live card as in chat (steps, how it thought, Stop), and the
 *  way back to the chat it came from. */
function TaskPanel({ task, onClose, onDeleted }: { task: TaskOnMap; onClose: () => void; onDeleted: (id: string) => void }) {
  const open = () => {
    const store = useAppStore.getState();
    if (task.run.conversation_id) store.setActiveConversation(task.run.conversation_id);
    store.setChatTab("build"); store.setChatOpen(true);
  };
  return <>
    <div className="panel-head"><span className="map-icon lg graph-task-icon"><ListChecks size={18} /></span>
      <div className="min-w-0 flex-1"><h3>{task.run.title.replace(/^build:\s*/i, "")}</h3>
        <div className="graph-sub">One-time task · {task.members.length ? `a team of ${task.members.length}` : "steps"} · {task.done} of {task.total} done</div></div>
      <button className="btn-quiet !p-1.5" aria-label="Close" onClick={onClose}><X size={15} /></button></div>
    <TaskRunCard run={task.run} onDeleted={onDeleted} />
    {task.run.conversation_id && <button className="btn-quiet self-start" onClick={open}><MessageCircle size={14} />Open its chat</button>}
  </>;
}

function BarButton({ icon: Icon, label, onClick, pressed }: { icon: LucideIcon; label: string; onClick: () => void; pressed?: boolean }) {
  return <button className="bar-button" aria-label={label} title={label} aria-pressed={pressed} onClick={onClick}><Icon size={16} strokeWidth={1.8} /></button>;
}

const TABS: { id: Exclude<NavId, "home">; label: string; icon: LucideIcon }[] = [
  { id: "library", label: "Library", icon: BookOpen },
  { id: "knowledge", label: "Knowledge", icon: BrainIcon },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "resources", label: "Accounts & resources", icon: Boxes },
  { id: "actions", label: "Action rules", icon: Zap },
  { id: "timeline", label: "What's next", icon: CalendarClock },
  { id: "activity", label: "What happened", icon: History },
];

/** Everything that is not the graph, in one sheet over it. */
function WorkspaceSheet({ tab, onTab, onClose }: { tab: NavId; onTab: (t: NavId) => void; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".sheet-backdrop, .drawer-backdrop")) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const body = tab === "library" ? <LibraryPage /> : tab === "knowledge" ? <KnowledgePage /> : tab === "settings" ? <SettingsPage /> : tab === "resources" ? <ResourcesPage /> : tab === "actions" ? <ActionsPage />
    : tab === "timeline" ? <><PageHeader eyebrow="Agents" title="What's next" description="What your agents will do, in order." /><div className="page-content"><div className="narrow"><TimelineView /></div></div></>
    : <><PageHeader eyebrow="Agents" title="What happened" description="What your agents have done." /><div className="page-content"><div className="narrow"><ActivityView /></div></div></>;
  return <div className="workspace-backdrop" onClick={onClose}>
    <div className="workspace-sheet" role="dialog" aria-modal="true" aria-label={TABS.find(t => t.id === tab)?.label} onClick={e => e.stopPropagation()}>
      <nav className="workspace-nav" aria-label="Sections">
        {TABS.map(t => <button key={t.id} className="side-item" aria-current={tab === t.id ? "page" : undefined} onClick={() => onTab(t.id)}><span className="nav-tile"><t.icon size={15} strokeWidth={1.8} /></span>{t.label}</button>)}
        <button className="btn-quiet mt-auto justify-start" onClick={onClose}><X size={14} />Back to the map</button>
      </nav>
      <div className="workspace-body"><ErrorBoundary key={tab}>{body}</ErrorBoundary></div>
    </div>
  </div>;
}
