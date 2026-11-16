import { Map as MapIcon, Plus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChatPage } from "../pages/ChatPage";
import { OPEN_CREW, takeOpenCrew } from "../lib/openCrew";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";
import { agentState, botState } from "./AgentGraph";
import { AgentBot } from "./AgentBot";
import { AgentRoom } from "./AgentRoom";
import { agentStyle } from "./CrewFan";
import { CrewDetail } from "./CrewDetail";
import { needsReason } from "./CrewQuickList";
import { ErrorBoundary } from "./ErrorBoundary";
import { AgentPanel } from "./GraphPanels";
import { tidyReport } from "./Report";

const ROOM = "mavo-room";
// One feel for every move: quick, soft, no bounce to wait on.
const MOVE = { type: "spring", stiffness: 520, damping: 42, mass: 0.8 } as const;
const POP = { type: "spring", stiffness: 600, damping: 30 } as const;
const FADE = { duration: 0.22, ease: [0.2, 0.7, 0.2, 1] } as const;
const SLIDE = { type: "spring", stiffness: 420, damping: 40 } as const;
const MAVO = "mavo";

/** How many things wait for the person in this agent's room. */
export function waitingCount(crew: CrewInstance): number {
  return (crew.asks?.length ?? 0) + (crew.question ? 1 : 0) + (crew.held ? 1 : 0) + (crew.limit ? 1 : 0);
}

/** The one line under an agent's name: what needs you, what it is doing, or what it last found. */
export function roomLine(crew: CrewInstance): string {
  if (crew.status === "draft") return "Setting up";
  if (crew.asks?.length) return crew.asks[0].question;
  if (agentState(crew) === "needs") return needsReason(crew);
  if (agentState(crew) === "working") return "Working…";
  const first = tidyReport(crew.checkpoint ?? "").split("\n").map(l => l.replace(/^[#>\-*\d.)\s]+/, "").replace(/\*\*/g, "").trim()).find(Boolean);
  if (first) return first;
  return crew.is_running ? "Waiting for its first result" : "Stopped";
}

/** The rooms, in the order they matter: Mavo, then agents that need you, then the rest, then
 *  drafts being set up. */
export function roomOrder(crews: CrewInstance[], drafts: CrewInstance[]) {
  const live = crews.filter(c => c.status !== "draft");
  const needs = live.filter(c => waitingCount(c) > 0 || agentState(c) === "needs");
  const rest = live.filter(c => !needs.includes(c));
  return { needs, rest, drafts: [...drafts, ...crews.filter(c => c.status === "draft" && !drafts.some(d => d.id === c.id))] };
}

function RoomRow({ crew, active, onPick }: { crew: CrewInstance; active: boolean; onPick: () => void }) {
  const count = waitingCount(crew);
  return <motion.button layout="position" transition={MOVE} className="room-row" aria-current={active || undefined} data-state={agentState(crew)} onClick={onPick}>
    {active && <motion.span layoutId="room-active" className="room-active" transition={MOVE} />}
    <span className="map-face sm has-bot" style={agentStyle(crew)}><AgentBot seed={crew.id} state={botState(crew)} /></span>
    <span className="room-row-text"><b>{crew.name}</b><span>{roomLine(crew)}</span></span>
    <AnimatePresence>{count > 0 && <motion.i key="count" className="room-count" aria-label={`${count} waiting for you`}
      initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.4, opacity: 0 }} transition={POP}>{count}</motion.i>}</AnimatePresence>
  </motion.button>;
}

/** Mavo as rooms: a list on the left (Mavo itself, then each agent), and the room you pick on
 *  the right. The map is one press away, as the overview. */
export function Rooms({ onOverview, query = "", crews: given, drafts: givenDrafts }: { onOverview: () => void; query?: string; crews?: CrewInstance[]; drafts?: CrewInstance[] }) {
  const stored = useAppStore(s => s.crew);
  const storedDrafts = useAppStore(s => s.setupDrafts);
  const crews = given ?? stored, drafts = givenDrafts ?? storedDrafts;
  const focusAgent = useAppStore(s => s.focusAgent);
  const [room, setRoom] = useState<string>(() => { try { return localStorage.getItem(ROOM) || MAVO; } catch { return MAVO; } });
  const [settings, setSettings] = useState(false);
  const pick = (id: string) => { setRoom(id); setSettings(false); try { localStorage.setItem(ROOM, id); } catch { /* remembered for now only */ } };
  // Opened from elsewhere (a card in chat, a desktop alert's Open): that agent's room.
  useEffect(() => { if (focusAgent) { pick(focusAgent); useAppStore.getState().openAgent(null); } }, [focusAgent]);
  useEffect(() => {
    const take = () => { const id = takeOpenCrew(); if (id) pick(id); };
    const onStorage = (e: StorageEvent) => { if (e.key === OPEN_CREW && e.newValue) take(); };
    take();
    window.addEventListener("storage", onStorage); window.addEventListener("focus", take);
    return () => { window.removeEventListener("storage", onStorage); window.removeEventListener("focus", take); };
  }, []);

  // The bar's search narrows the list (by name or job).
  const q = query.trim().toLowerCase();
  const found = (c: CrewInstance) => !q || `${c.name} ${c.responsibilities}`.toLowerCase().includes(q);
  const ordered = roomOrder(crews, drafts);
  const needs = ordered.needs.filter(found), rest = ordered.rest.filter(found), setting = ordered.drafts.filter(found);
  const all = [...ordered.needs, ...ordered.rest, ...ordered.drafts];
  const crew = all.find(c => c.id === room);
  const shown = room === MAVO || !crew ? MAVO : room;
  return <div className="rooms">
    <nav className="rooms-rail" aria-label="Rooms">
      <button className="room-row room-mavo" aria-current={shown === MAVO || undefined} onClick={() => pick(MAVO)}>
        {shown === MAVO && <motion.span layoutId="room-active" className="room-active" transition={MOVE} />}
        <span className="map-face sm has-bot room-mavo-face"><AgentBot seed="mavo" state="ready" /></span>
        <span className="room-row-text"><b>Mavo</b><span>Make and change agents, ask anything</span></span>
      </button>
      {needs.length > 0 && <><p className="rooms-label">Needs you</p>{needs.map(c => <RoomRow key={c.id} crew={c} active={shown === c.id} onPick={() => pick(c.id)} />)}</>}
      {rest.length > 0 && <><p className="rooms-label">Agents</p>{rest.map(c => <RoomRow key={c.id} crew={c} active={shown === c.id} onPick={() => pick(c.id)} />)}</>}
      {setting.length > 0 && <><p className="rooms-label">Setting up</p>{setting.map(c => <RoomRow key={c.id} crew={c} active={shown === c.id} onPick={() => pick(c.id)} />)}</>}
      {q && !needs.length && !rest.length && !setting.length && <p className="rooms-empty">No agent matches.</p>}
      <button className="room-new" onClick={() => { pick(MAVO); useAppStore.getState().startInChat("Make an agent that "); }}><Plus size={14} />New agent</button>
      <button className="room-overview" onClick={onOverview}><MapIcon size={14} />Map</button>
    </nav>
    <main className="rooms-main">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={shown} className="room-swap" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={FADE}>
          <ErrorBoundary key={shown}>
            {shown === MAVO ? <div className="room-mavo-chat"><ChatPage docked /></div>
              : crew!.status === "draft" ? <section className="room room-draft"><header className="room-head"><Sparkles size={16} /><h2>{crew!.name}</h2></header><div className="room-feed"><CrewDetail crew={crew!} /></div></section>
              : <AgentRoom crew={crew!} onSettings={() => setSettings(open => !open)} />}
          </ErrorBoundary>
        </motion.div>
      </AnimatePresence>
      <AnimatePresence>
        {settings && crew && crew.status !== "draft" && <motion.aside key="drawer" className="room-drawer" aria-label={`${crew.name} settings`}
          initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }} transition={SLIDE}>
          <ErrorBoundary key={crew.id}><AgentPanel crew={crew} settingsOnly onClose={() => setSettings(false)} /></ErrorBoundary>
        </motion.aside>}
      </AnimatePresence>
    </main>
  </div>;
}
