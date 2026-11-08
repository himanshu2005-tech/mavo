import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { cursorPosition, getCurrentWindow } from "@tauri-apps/api/window";
import { ArrowUpRight, BellDot, BellOff, Bot, ChevronLeft, ChevronRight, CircleAlert, Power } from "lucide-react";
import { CrewQuickList } from "../components/CrewQuickList";
import { OPEN_CREW } from "../lib/openCrew";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { MUTE_CHOICES, countdown, useMute } from "../lib/mute";
import { ReminderWatcher } from "../components/ReminderWatcher";
import { DesktopUpdateCard, ago } from "../components/DesktopUpdateCard";
import type { Reminder } from "../types";
import { speak } from "./ReminderOverlay";

function MavoMark() {
  return <svg className="orb-mark" width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}

type Status = { paused: boolean; running: number; pending: number; needs?: { id: string; name: string; reason: string }[]; working?: string[] };

const PEEK_MS = 9000;

/** The first thing an update says, as one plain line. */
export function firstLine(message: string): string {
  const line = message.split(/\r?\n/).map(l => l.trim()).find(l => l && !/^(#|note:)/i.test(l)) ?? "";
  return line.replace(/^([-*•]|\d+[.)])\s+/, "").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`/g, "").slice(0, 160);
}

/** The updates that came while the laptop was closed (or Mavo was muted): newest first, each one
 *  opens as its card, and Clear all puts them away. */
export function MissedList({ items, busy, error, onBack, onClear, onOpen }: {
  items: Reminder[]; busy: boolean; error: string | null; onBack: () => void; onClear: () => void; onOpen: (item: Reminder) => void;
}) {
  return <section className="surface orb-menu dmenu dlist missed" aria-label="Missed updates">
    <header className="dmenu-head">
      <button className="dmenu-back" aria-label="Back" onClick={onBack}><ChevronLeft size={15} /></button>
      <span className="dmenu-title">Missed</span>
      {items.length > 0 && <button className="dmenu-pill" disabled={busy} onClick={onClear}>Clear all</button>}
    </header>
    {items.length === 0
      ? <p className="missed-empty">Nothing missed.</p>
      : <ul className="missed-list">{[...items].reverse().map(item => <li key={item.id}>
          <button onClick={() => onOpen(item)}>
            <span className="missed-row"><b>{item.crew_name}</b><time dateTime={item.scheduled_for}>{ago(item.scheduled_for)}</time></span>
            <span className="missed-text">{firstLine(item.message)}</span>
          </button>
        </li>)}</ul>}
    {error && <p role="alert" className="toast-error">{error}</p>}
  </section>;
}

/** The desktop icon. Rest the pointer on it and a small menu opens: Open Mavo, Mute for a while,
 * or Quit (which stops everything). Click it to keep the menu open; drag it to move it. */
export function StatusOverlay() {
  const [expanded, setExpanded] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [signedIn, setSignedIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmQuit, setConfirmQuit] = useState(false);
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [voice, setVoice] = useState<"ready" | "speaking" | "unavailable">("ready");
  const [peek, setPeek] = useState(false);
  // Updates that came while the laptop was closed: kept here, not popped up one after another.
  const [missed, setMissed] = useState<Reminder[]>([]);
  const [showMissed, setShowMissed] = useState(false);
  const [showCrew, setShowCrew] = useState(false); // the agents, as a list in the menu
  const opening = useRef(false); // a missed update opened from the list: straight to the card
  const peekTimer = useRef<number | undefined>(undefined);
  const overPill = useRef(false);
  const { seconds, muted, mute, unmute } = useMute();
  const pointer = useRef<{ x: number; y: number; dragged: boolean } | null>(null);
  const hoverTimer = useRef<number | undefined>(undefined);
  const leaveTimer = useRef<number | undefined>(undefined);

  async function refresh() {
    try {
      const session = await api.firebaseStatus();
      setSignedIn(session.signed_in);
      setStatus(session.signed_in ? await api.desktopStatus() : null);
      setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Mavo is reconnecting."); }
  }
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer); }, []);

  useEffect(() => {
    let active = true;
    function show(value: Reminder | null) {
      if (!active) return;
      setReminder(value);
      if (value && opening.current) {
        opening.current = false;
        setExpanded(true); setPeek(false); setShowMissed(false); setError(null);
        void invoke("status_expanded", { expanded: true, peek: false }).catch(e => setError(String(e)));
        return;
      }
      if (value) {
        // It slides out of the icon as a one-line pill, then tucks back in, leaving the dot.
        setExpanded(true); setPeek(true); setError(null);
        void invoke("status_expanded", { expanded: true, peek: true }).catch(e => setError(String(e)));
        window.clearTimeout(peekTimer.current);
        peekTimer.current = window.setTimeout(() => hide.current(), PEEK_MS);
        if (value.kind !== "crew_update") speak(value.message, setVoice);
      }
    }
    const registered = listen<Reminder>("reminder-updated", e => show(e.payload));
    const cleared = listen("reminder-cleared", () => { if (active) { setReminder(null); window.speechSynthesis?.cancel(); void expand(false); } });
    void registered.then(() => invoke<Reminder | null>("pending_reminder")).then(show).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; void registered.then(f => f()); void cleared.then(f => f()); window.speechSynthesis?.cancel(); };
  }, []);

  /** Opens the menu (or, with `list`, the Missed list or the agents) or folds it into the icon.
   *  The lists take a taller window; nothing ever covers the screen. */
  async function expand(value: boolean, list: boolean | "list" = false) {
    window.clearTimeout(peekTimer.current);
    try {
      await invoke("status_expanded", { expanded: value, peek: false, missed: value && !!list, fan: false });
      setPeek(false); setExpanded(value); setShowMissed(value && list === true); setShowCrew(value && list === "list");
      if (!value) { setPinned(false); setConfirmQuit(false); }
    }
    catch (e) { setError(String(e)); }
  }
  async function openMissed(item: Reminder) {
    opening.current = true;
    try { await invoke("show_reminder_overlay", { reminder: item }); setMissed(m => m.filter(r => r.id !== item.id)); }
    catch (e) { opening.current = false; setError(String(e)); }
  }
  async function clearMissed() {
    setBusy(true);
    try { await Promise.all(missed.map(r => api.dismissReminder(r.id))); setMissed([]); void expand(true); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not clear them."); }
    finally { setBusy(false); }
  }
  // Folded into the icon, the page must be the icon's size; if it was left larger (the icon would
  // be drawn outside its window and vanish), ask for the size again.
  useEffect(() => {
    if (expanded) return;
    const timer = window.setTimeout(() => { if (window.innerWidth > 90 || window.innerHeight > 90) void invoke("status_expanded", { expanded: false, peek: false }).catch(() => undefined); }, 600);
    return () => window.clearTimeout(timer);
  }, [expanded]);
  // The pill goes back into the icon unless the pointer is on it.
  const hide = useRef(() => {});
  hide.current = () => { if (peek && !overPill.current) void expand(false); };
  async function dismiss() {
    if (!reminder) return;
    setBusy(true);
    try { if (!reminder.preview) await api.dismissReminder(reminder.id); await invoke("close_reminder_overlay"); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  // Opens Mavo on this Crew: the main window picks the Crew up from shared storage.
  async function openCrewById(id: string) {
    try { localStorage.setItem(OPEN_CREW, JSON.stringify({ id, at: Date.now() })); } catch { /* Mavo still opens */ }
    try { await invoke("show_main", { activity: true }); } catch (e) { setError(String(e)); }
  }
  async function openCrew() {
    if (!reminder?.crew_id) return;
    try { localStorage.setItem(OPEN_CREW, JSON.stringify({ id: reminder.crew_id, at: Date.now() })); } catch { /* Mavo still opens */ }
    try { await invoke("show_main", { activity: true }); } catch (e) { setError(String(e)); }
  }

  // Hover opens the menu after a breath, and leaving closes it after a longer one, so passing over
  // the icon does not flicker it and a slip off the edge does not lose it.
  function enter() {
    window.clearTimeout(leaveTimer.current);
    window.clearTimeout(hoverTimer.current);
    if (!expanded && !reminder && !pointer.current?.dragged) hoverTimer.current = window.setTimeout(() => void expand(true), 200);
  }
  function leave() {
    window.clearTimeout(hoverTimer.current);
    window.clearTimeout(leaveTimer.current);
    if (expanded && !pinned && !reminder) leaveTimer.current = window.setTimeout(() => void expand(false), 500);
  }
  // The icon swaps its own elements when the menu opens, so also listen to the window itself: the
  // pointer leaving the window always closes an unpinned menu.
  const hover = useRef({ enter, leave });
  hover.current = { enter, leave };
  useEffect(() => {
    const root = document.documentElement;
    const inside = () => hover.current.enter();
    const outside = () => hover.current.leave();
    root.addEventListener("mouseenter", inside);
    root.addEventListener("mouseleave", outside);
    return () => { root.removeEventListener("mouseenter", inside); root.removeEventListener("mouseleave", outside); window.clearTimeout(hoverTimer.current); window.clearTimeout(leaveTimer.current); };
  }, []);
  // The browser does not always say the pointer left (the window resized under it, or it left
  // fast over the see-through edge), and an open menu was left hanging on the desktop. While
  // a menu is open and not pinned, look where the pointer really is; outside the window twice
  // in a row (about half a second) and it folds away.
  useEffect(() => {
    if (!expanded || pinned || reminder) return;
    let away = 0, active = true;
    const look = async () => {
      try {
        const win = getCurrentWindow();
        const [at, pos, size] = await Promise.all([cursorPosition(), win.outerPosition(), win.outerSize()]);
        const outside = at.x < pos.x || at.y < pos.y || at.x > pos.x + size.width || at.y > pos.y + size.height;
        away = outside ? away + 1 : 0;
        if (active && away >= 2) void expand(false);
      } catch { /* not in the desktop app (a preview): the pointer events still close it */ }
    };
    const timer = window.setInterval(() => void look(), 250);
    return () => { active = false; window.clearInterval(timer); };
  }, [expanded, pinned, reminder]);
  // A pinned menu folds away like any menu: a click somewhere else, or Esc.
  useEffect(() => {
    if (!expanded || reminder) return;
    const blur = () => void expand(false);
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") void expand(false); };
    window.addEventListener("blur", blur);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("blur", blur); window.removeEventListener("keydown", key); };
  }, [expanded, reminder]);

  const working = status?.working ?? [];
  const orbState = reminder ? "attention" : error || !signedIn ? "offline" : status?.paused ? "paused" : working.length ? "working" : (status?.running ?? 0) > 0 ? "running" : "idle";
  const minutes = Math.ceil(seconds / 60);
  const orb = <button className="status-orb" data-state={orbState} data-muted={muted}
    aria-label={muted ? `Mavo · muted, ${minutes} min left` : reminder ? "Mavo status · unread update" : "Mavo status"}
    title={muted ? `Muted · ${countdown(seconds)} left` : reminder ? "New Crew update" : missed.length ? `${missed.length} missed` : error ? "Mavo is reconnecting" : status?.paused ? "Mavo · paused" : working.length ? `${working.join(", ")} working` : "Mavo is running"}
    onPointerDown={e => { pointer.current = { x: e.screenX, y: e.screenY, dragged: false }; }}
    onPointerMove={e => { const start = pointer.current; if (start && e.buttons === 1 && !start.dragged && Math.hypot(e.screenX - start.x, e.screenY - start.y) > 5) { start.dragged = true; window.clearTimeout(hoverTimer.current); void getCurrentWindow().startDragging().catch(err => setError(String(err))); } }}
    onClick={() => { if (!pointer.current?.dragged) { if (expanded && pinned) void expand(false); else { setPinned(true); if (!expanded) void expand(true); } } pointer.current = null; }}>
    <MavoMark /><span className="orb-dot" />{muted ? <span className="orb-badge" aria-hidden="true">{minutes}m</span>
      : missed.length > 0 && !reminder ? <span className="orb-badge orb-missed" aria-hidden="true">{missed.length > 9 ? "9+" : missed.length}</span> : null}
  </button>;

  if (expanded && showCrew && !reminder) return <div className="orb-menu-stage" onPointerEnter={enter} onPointerLeave={leave}><ReminderWatcher onMissed={setMissed} />
    <CrewQuickList onBack={() => void expand(true)} onOpen={crew => void openCrewById(crew.id)} />
    <div className="orb-foot"><span /><div className="orb-slot">{orb}</div></div>
  </div>;

  if (expanded && showMissed && !reminder) return <div className="orb-menu-stage" onPointerEnter={enter} onPointerLeave={leave}><ReminderWatcher onMissed={setMissed} />
    <MissedList items={missed} busy={busy} error={error} onBack={() => void expand(true)} onClear={() => void clearMissed()} onOpen={item => void openMissed(item)} />
    <div className="orb-foot"><span /><div className="orb-slot">{orb}</div></div>
  </div>;

  if (!expanded) return <div className="orb-stage" onPointerEnter={enter} onPointerLeave={leave}><ReminderWatcher onMissed={setMissed} />{orb}</div>;

  if (reminder && peek) return <div className="pill-stage"><ReminderWatcher onMissed={setMissed} />
    <button className="pill" aria-label={`${reminder.crew_name}: ${firstLine(reminder.message)}. Open`}
      onPointerEnter={() => { overPill.current = true; window.clearTimeout(peekTimer.current); }}
      onPointerLeave={() => { overPill.current = false; window.clearTimeout(peekTimer.current); peekTimer.current = window.setTimeout(() => hide.current(), 2500); }}
      onClick={() => void expand(true)}>
      <span className="pill-mark"><MavoMark /></span>
      <span className="pill-text"><b>{reminder.crew_name}</b><span>{firstLine(reminder.message)}</span></span>
      <span className="pill-time">{ago(reminder.scheduled_for)}</span>
    </button>
  </div>;

  if (reminder) return <div className="toast-stage"><ReminderWatcher onMissed={setMissed} /><section className="toast-shell">
    <DesktopUpdateCard reminder={reminder} busy={busy} onDismiss={() => void dismiss()} onClose={() => void expand(false)} onOpen={() => void openCrew()} onSpeak={() => speak(reminder.message, setVoice)} />
    {(voice === "unavailable" || error) && <p role="alert" className="toast-error">{error ?? "Voice unavailable. Check Windows voices."}</p>}
  </section></div>;

  const tone = error || !signedIn ? "off" : muted || status?.paused ? "warn" : working.length ? "busy" : (status?.running ?? 0) > 0 ? "good" : "off";
  const headline = error ? "Reconnecting…" : !signedIn ? "Signed out" : muted ? `Muted · ${countdown(seconds)}` : status?.paused ? "Paused"
    : working.length === 1 ? `${working[0]} is working` : working.length ? `${working.length} working` : `${status?.running ?? 0} running`;
  return <div className="orb-menu-stage" onPointerEnter={enter} onPointerLeave={leave}><ReminderWatcher onMissed={setMissed} />
    <OrbMenu headline={headline} tone={tone} running={status?.running ?? 0} missed={missed.length} pending={muted ? 0 : status?.pending ?? 0}
      needs={status?.needs} muted={muted} confirmQuit={confirmQuit}
      onOpen={() => { void invoke("show_main", { activity: false }); void expand(false); }}
      onAgents={() => void expand(true, "list")}
      onMissed={() => void expand(true, true)}
      onAttention={() => void expand(true, "list")}
      onNeed={id => { void openCrewById(id); void expand(false); }}
      onMute={mute} onUnmute={unmute} onAskQuit={setConfirmQuit} onQuit={() => void invoke("quit_mavo")} />
    <div className="orb-foot">
      <span />
      <div className="orb-slot">{orb}</div>
    </div>
  </div>;
}

/** The desktop menu: where things stand, then one row per thing to do. Quiet rows, not buttons;
 *  only quitting asks twice. */
export function OrbMenu({ headline, tone, running, missed, pending, needs = [], muted, confirmQuit, onOpen, onAgents, onMissed, onAttention, onNeed, onMute, onUnmute, onAskQuit, onQuit }: {
  headline: string; tone: "good" | "busy" | "warn" | "off"; running: number; missed: number; pending: number; muted: boolean; confirmQuit: boolean;
  /** The agents waiting for the person, with why. */
  needs?: { id: string; name: string; reason: string }[];
  onOpen: () => void; onAgents: () => void; onMissed: () => void;
  /** Several agents need you: show them (the agents list puts them first, with why). */
  onAttention: () => void;
  /** Open this agent in Mavo. */
  onNeed: (id: string) => void;
  onMute: (minutes: number) => void; onUnmute: () => void; onAskQuit: (ask: boolean) => void; onQuit: () => void;
}) {
  return <section className="surface orb-menu dmenu" aria-label="Mavo menu">
    <header className="dmenu-head">
      <span className="dmenu-title">Mavo</span>
      <span role="status" className="dmenu-status" data-tone={tone} title={headline}><i /><span className="dmenu-status-text">{headline}</span></span>
    </header>
    <div className="dmenu-rows">
      <button className="dmenu-row" onClick={onOpen}><ArrowUpRight size={15} aria-hidden="true" /><span>Open Mavo</span></button>
      <button className="dmenu-row" onClick={onAgents}><Bot size={15} aria-hidden="true" /><span>Agents</span>{running > 0 && <em>{running}</em>}<ChevronRight size={14} className="dmenu-chev" aria-hidden="true" /></button>
      {missed > 0 && <button className="dmenu-row" onClick={onMissed}><BellDot size={15} aria-hidden="true" /><span>Missed</span><em>{missed}</em><ChevronRight size={14} className="dmenu-chev" aria-hidden="true" /></button>}
      {pending === 1 && needs[0]
        // One agent waiting: say which and why; the row opens it in Mavo, where it can be answered.
        ? <button className="dmenu-row dmenu-need" data-tone="warn" onClick={() => onNeed(needs[0].id)} title={`${needs[0].name}: ${needs[0].reason}`}>
            <CircleAlert size={15} aria-hidden="true" /><span><b>{needs[0].name} needs you</b><small>{needs[0].reason}</small></span><ChevronRight size={14} className="dmenu-chev" aria-hidden="true" /></button>
        : pending > 0 && <button className="dmenu-row" data-tone="warn" onClick={onAttention}><CircleAlert size={15} aria-hidden="true" /><span>{pending} agents need you</span><ChevronRight size={14} className="dmenu-chev" aria-hidden="true" /></button>}
    </div>
    <div className="dmenu-sep" />
    {muted
      ? <div className="dmenu-mute"><BellOff size={15} aria-hidden="true" /><span>Alerts muted</span><button className="dmenu-pill" onClick={onUnmute}>Unmute</button></div>
      : <div className="dmenu-mute" role="group" aria-label="Mute for"><BellOff size={15} aria-hidden="true" /><span>Mute</span>
          <div className="dmenu-seg">{MUTE_CHOICES.map(c => <button key={c.minutes} type="button" onClick={() => onMute(c.minutes)}>{c.label.replace(" hour", " h")}</button>)}</div></div>}
    <div className="dmenu-sep" />
    {confirmQuit
      ? <div className="dmenu-confirm"><span>Stop every agent?</span><button className="dmenu-pill" onClick={() => onAskQuit(false)}>Cancel</button><button className="dmenu-pill" data-tone="bad" onClick={onQuit}>Quit</button></div>
      : <button className="dmenu-row dmenu-quit" onClick={() => onAskQuit(true)}><Power size={15} aria-hidden="true" /><span>Quit Mavo</span></button>}
  </section>;
}
