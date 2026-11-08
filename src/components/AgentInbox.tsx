import { ArrowUp, Check, ChevronDown, ListChecks, LoaderCircle, MessageCircle, Search } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api } from "../lib/api";
import { initials } from "../lib/radialGraph";
import { parseTime } from "../lib/time";
import { useAppStore } from "../store/app";
import type { CrewAsk, CrewInstance, Reminder } from "../types";
import { agentState } from "./AgentGraph";
import { agentStyle } from "./CrewFan";
import { needsReason } from "./CrewQuickList";
import { CrewReplyBox } from "./CrewReplyBox";
import { HandoverCard } from "./LinePanel";
import { LimitCard, limitOf } from "./LimitCard";
import { Report } from "./Report";

/** One message in the thread: an agent telling you something, you asking an agent, its answer. */
export interface ThreadItem { id: string; at: string; agentId: string; agent: string; from: "agent" | "you"; text: string; pending?: boolean }

const DAY = 86_400_000;

/** The thread: what agents told you (their updates) and what you asked them and they answered,
 *  oldest first, for the last week. */
export function threadOf(updates: Reminder[], asks: Record<string, { name: string; items: CrewAsk[] }>, now = Date.now()): ThreadItem[] {
  const recent = (at: string) => now - parseTime(at).getTime() < 7 * DAY;
  const items: ThreadItem[] = updates.filter(u => recent(u.scheduled_for)).map(u => ({
    id: "u" + u.id, at: u.scheduled_for, agentId: u.crew_id, agent: u.crew_name, from: "agent" as const, text: u.message,
  }));
  for (const [agentId, { name, items: list }] of Object.entries(asks)) {
    for (const ask of list) {
      if (!recent(ask.at)) continue;
      items.push({ id: "q" + ask.id, at: ask.at, agentId, agent: name, from: "you", text: ask.question });
      if (ask.answer || ask.pending) items.push({ id: "a" + ask.id, at: ask.at + "~", agentId, agent: name, from: "agent", text: ask.answer ?? "", pending: ask.pending });
    }
  }
  return items.sort((a, b) => a.at.localeCompare(b.at));
}

/** A one-time task's messages come from "Build: RSS Feed Reader" or "One-time ab12cd ...": the
 *  task's own name, and that it is a task (it cannot be messaged back). */
export function senderOf(name: string, known: boolean): { label: string; task: boolean; gone?: boolean } {
  if (known) return { label: name, task: false };
  // Only a one-time task's own names ("Build: ...", "One-time ab12cd ..."); anything else that is
  // not one of your agents now is an agent that was removed, not a task.
  if (!/^(one-time [0-9a-f]{4,8}\b|build\s*:|task\s*:)/i.test(name)) return { label: name, task: false, gone: true };
  const label = name.replace(/^One-time [0-9a-f]{4,8}\s*/i, "").replace(/^(build|task)\s*:\s*/i, "").trim() || name;
  return { label, task: true };
}

const at = (value: string) => parseTime(value.replace(/~$/, ""));
const clock = (value: string) => at(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
export function dayLabel(value: string, now = new Date()): string {
  const d = at(value);
  if (d.toDateString() === now.toDateString()) return "Today";
  if (d.toDateString() === new Date(now.getTime() - DAY).toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}

function Face({ crew, name, task }: { crew?: CrewInstance; name: string; task?: boolean }) {
  if (task) return <span className="map-face sm inbox-task-face" aria-hidden="true"><ListChecks size={12} /></span>;
  return <span className="map-face sm" style={crew?agentStyle(crew):undefined} aria-hidden="true">{initials(name)}</span>;
}

/** The two answers to work held for you, in the words its card uses. */
export function heldWords(crew: Pick<CrewInstance, "held">): { yes: string; no: string } {
  const kind = crew.held?.kind;
  return kind === "ask" ? { yes: "Yes", no: "No" } : kind === "review_stuck" ? { yes: "Send it on", no: "Send back" } : { yes: "Approve", no: "Send back" };
}

/** A long message shows its first lines; the rest one click away. */
function Body({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [long, setLong] = useState(false);
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => { const el = box.current; if (el) setLong(el.scrollHeight > el.clientHeight + 4); }, [text]);
  return <>
    <div ref={box} className="inbox-body" data-open={open}><Report text={text} compact /></div>
    {(long || open) && <button className="inbox-more" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button>}
  </>;
}

/** Who a message goes to: your agents, with their faces and how they are doing; type to find one,
 *  arrows and Enter to choose. */
function AgentPicker({ crews, value, onChange }: { crews: CrewInstance[]; value: CrewInstance | undefined; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const shown = crews.filter(c => c.name.toLowerCase().includes(query.trim().toLowerCase()));
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("pointerdown", away);
    return () => window.removeEventListener("pointerdown", away);
  }, [open]);
  useEffect(() => { setActive(0); }, [query, open]);
  const choose = (c: CrewInstance) => { onChange(c.id); setOpen(false); setQuery(""); };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(i => Math.min(i + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    else if (e.key === "Enter" && shown[active]) { e.preventDefault(); choose(shown[active]); }
  };
  return <div className="picker" ref={root}>
    <button type="button" className="picker-button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="picker-to">To</span>
      {value && <Face crew={value} name={value.name} />}
      <span className="picker-name">{value?.name ?? "Choose an agent"}</span>
      <ChevronDown size={13} aria-hidden="true" />
    </button>
    {open && <div className="picker-pop" role="dialog" aria-label="Send to">
      {crews.length > 6 && <label className="picker-search"><Search size={13} aria-hidden="true" />
        <input autoFocus value={query} placeholder="Find an agent" onChange={e => setQuery(e.target.value)} onKeyDown={keys} /></label>}
      <ul role="listbox" tabIndex={crews.length > 6 ? -1 : 0} onKeyDown={keys} autoFocus={crews.length <= 6}>
        {shown.map((c, i) => <li key={c.id} role="option" aria-selected={c.id === value?.id} data-active={i === active}
          onPointerEnter={() => setActive(i)} onClick={() => choose(c)}>
          <Face crew={c} name={c.name} />
          <span className="picker-option-name">{c.name}</span>
          <i className="picker-dot" data-state={agentState(c)} aria-hidden="true" />
          {c.id === value?.id && <Check size={13} className="picker-check" aria-hidden="true" />}
        </li>)}
        {!shown.length && <li className="picker-none">No agent called that</li>}
      </ul>
    </div>}
  </div>;
}

/** Your agents talking with you: what needs you first, then the conversation. You answer and ask
 *  them here; making or changing agents is Build's job. */
export function AgentInbox({ initial }: { initial?: { updates: Reminder[]; asks: Record<string, { name: string; items: CrewAsk[] }> } } = {}) {
  const crews = useAppStore(s => s.crew);
  const [updates, setUpdates] = useState<Reminder[]>(initial?.updates ?? []);
  const [asks, setAsks] = useState<Record<string, { name: string; items: CrewAsk[] }>>(initial?.asks ?? {});
  const [loaded, setLoaded] = useState(!!initial);
  const [offline, setOffline] = useState(false);
  const [to, setTo] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const crewsNow = useRef(crews);
  crewsNow.current = crews;

  // What agents said lately, and the questions you asked the agents in that talk.
  useEffect(() => {
    if (initial) return;
    let active = true;
    const load = async () => {
      try {
        const list = await api.reminderHistory();
        if (!active) return;
        setUpdates(list); setOffline(false);
        const talking = [...new Set([...list.slice(-30).map(u => u.crew_id), ...(to ? [to] : [])])].filter(id => crewsNow.current.some(c => c.id === id)).slice(-12);
        const found = await Promise.all(talking.map(async id => [id, await api.crewAsks(id).catch(() => null)] as const));
        if (active) setAsks(prev => ({ ...prev, ...Object.fromEntries(found.filter(([, items]) => items).map(([id, items]) => [id, { name: crewsNow.current.find(c => c.id === id)?.name ?? "Agent", items: items! }])) }));
      } catch { if (active) setOffline(true); }
      finally { if (active) setLoaded(true); }
    };
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [crews.length, initial, to]);

  const thread = useMemo(() => threadOf(updates, asks), [updates, asks]);
  const yours = useMemo(() => crews.filter(c => c.status !== "draft"), [crews]);
  const waiting = yours.filter(c => c.held || c.question);
  const lastAgent = [...thread].reverse().find(t => t.from === "agent" && yours.some(c => c.id === t.agentId));
  const target = yours.find(c => c.id === (to ?? lastAgent?.agentId)) ?? yours[0];
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [thread.length, waiting.length]);
  // Typing grows the box, up to a few lines.
  useLayoutEffect(() => { const el = field.current; if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 140) + "px"; } }, [text]);

  // Writing to an agent that holds work for your OK: your words go with your answer as its note,
  // so the work never stays held while you think you have replied.
  async function answer(approve: boolean) {
    if (!target || sending) return;
    setSending(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.answerHandover(target.id, approve, text.trim())); setText(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not send your answer."); }
    finally { setSending(false); }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (target?.held) return; // answered with the two buttons instead
    if (!text.trim() || !target || sending) return;
    setSending(true); setError(null);
    try {
      const ask = await api.askCrew(target.id, text.trim());
      setAsks(prev => ({ ...prev, [target.id]: { name: target.name, items: [...(prev[target.id]?.items ?? []), ask] } }));
      setText("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not send it."); }
    finally { setSending(false); }
  }
  const reply = (id: string) => { setTo(id); field.current?.focus(); };

  return <div className="inbox">
    <div className="inbox-scroll">
      {offline && <p className="inbox-offline" role="status">Mavo isn't reachable right now. Messages appear once it is back.</p>}
      {!loaded && !offline && <p className="inbox-loading" role="status"><LoaderCircle size={14} className="animate-spin" />Loading…</p>}
      {loaded && !thread.length && !waiting.length && <div className="inbox-empty"><MessageCircle size={20} aria-hidden="true" />
        <b>Your agents talk to you here</b><span>When an agent finishes something, needs a decision or has a question, it shows up here, and you can answer it. Make or change agents in Build.</span></div>}

      <ol className="inbox-thread">{thread.map((t, i) => {
        const crew = yours.find(c => c.id === t.agentId);
        const sender = senderOf(t.agent, !!crew);
        const before = thread[i - 1];
        const newDay = !before || dayLabel(before.at) !== dayLabel(t.at);
        // Back-to-back messages from the same sender, close in time, read as one group.
        const grouped = !newDay && before && before.from === t.from && before.agentId === t.agentId && at(t.at).getTime() - at(before.at).getTime() < 10 * 60_000;
        return <li key={t.id}>
          {newDay && <div className="inbox-day"><span>{dayLabel(t.at)}</span></div>}
          <div className="inbox-msg" data-from={t.from} data-grouped={!!grouped}>
            {t.from === "agent" && (grouped ? <span className="inbox-face-gap" /> : <Face crew={crew} name={sender.label} task={sender.task} />)}
            <div className="inbox-bubble">
              {!grouped && <div className="inbox-meta">
                {t.from === "agent"
                  ? <>{crew ? <button className="inbox-name" onClick={() => useAppStore.getState().openAgent(crew.id)}>{sender.label}</button> : <span className="inbox-name">{sender.label}</span>}
                      {sender.task && <span className="inbox-tag">One-time task</span>}{sender.gone && <span className="inbox-tag">Removed</span>}</>
                  : <span>To {sender.label}</span>}
                <time>{clock(t.at)}</time>
              </div>}
              <div className="inbox-text">
                {t.pending ? <p className="inbox-pending"><LoaderCircle size={12} className="animate-spin" />Finding out…</p>
                  : t.from === "agent" ? <Body text={t.text} /> : <p>{t.text}</p>}
              </div>
              {t.from === "agent" && crew && !t.pending && <button className="inbox-reply" onClick={() => reply(crew.id)}>Reply</button>}
            </div>
          </div>
        </li>;
      })}</ol>

      {waiting.length > 0 && <section className="inbox-waiting" aria-label="Waiting for you">
        <div className="inbox-day"><span>Waiting for you</span></div>
        {waiting.map(c => <div key={c.id} className="inbox-wait">
          {c.held ? <HandoverCard crew={c} /> : limitOf(c) ? <LimitCard crew={c} /> : <div className="inbox-question">
            <div className="inbox-who"><Face crew={c} name={c.name} /><b>{c.name}</b></div>
            <p>{needsReason(c)}</p>
            <CrewReplyBox crew={c} />
          </div>}
        </div>)}
      </section>}
      <div ref={end} />
    </div>

    {yours.length > 0 && <form className="inbox-compose" onSubmit={send}>
      <AgentPicker crews={yours} value={target} onChange={setTo} />
      {target?.held && <p className="inbox-held-hint">{target.name} is waiting for your OK. Anything you write goes with your answer as a note.</p>}
      <div className="inbox-field">
        <textarea ref={field} rows={1} value={text} maxLength={2000} placeholder={target?.held ? "Add a note (optional)" : target ? `Message ${target.name}` : "Message an agent"}
          aria-label={target ? `Message ${target.name}` : "Message an agent"} onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
        {target?.held
          ? <div className="inbox-answer">
              <button type="button" className="inbox-answer-no" disabled={sending} onClick={() => void answer(false)}>{heldWords(target).no}</button>
              <button type="button" className="inbox-answer-yes" disabled={sending} onClick={() => void answer(true)}>{sending ? <LoaderCircle size={13} className="animate-spin" /> : heldWords(target).yes}</button>
            </div>
          : <button type="submit" aria-label="Send" disabled={!text.trim() || sending}>{sending ? <LoaderCircle size={14} className="animate-spin" /> : <ArrowUp size={14} />}</button>}
      </div>
      {error && <p role="alert" className="inbox-error">{error}</p>}
    </form>}
  </div>;
}
