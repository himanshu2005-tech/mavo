import { ArrowRight, ArrowUp, Check, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewAsk as Ask, CrewCycle } from "../types";
import { LiveActions } from "./LiveActions";
import { Report } from "./Report";

/** Hand a request the agent cannot do to Mavo, written out: Mavo's chat opens with it typed,
 *  ready to send. */
function buildWithMavo(prompt: string) {
  const store = useAppStore.getState();
  store.startInChat(prompt);
  store.openAgent("mavo");
}

/** Ask a Crew anything about what it has found. It answers from its reports; when a question
 *  needs a fresh look it goes and finds out, and the answer appears here when it is back.
 *  `compact` is the one-question form for the small desktop card. In a room it is the whole
 *  conversation: the messages scroll, the box stays put at the bottom. `steps`: what the run
 *  answering the last question says it has done so far, shown under that question. `children`:
 *  what waits for the person, shown as the newest thing in the conversation. */
export function CrewAsk({ crewId, name, compact, autoFocus, onPreference, steps, live, children, reload = 0 }: { crewId: string; name: string; compact?: boolean; autoFocus?: boolean; onPreference?: () => void; steps?: string[]; /** The run answering the last request, step by step and action by action. */ live?: CrewCycle | null; children?: ReactNode; /** Bumped when something was added from outside (Check now). */ reload?: number }) {
  const [items, setItems] = useState<Ask[]>([]);
  const [text, setText] = useState("");
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const waiting = items.some(i => i.pending);

  useEffect(() => {
    let active = true;
    const load = () => api.crewAsks(crewId).then(v => { if (active) setItems(v); }).catch(() => undefined);
    if (!compact) void load();
    return () => { active = false; };
  }, [crewId, compact, reload]);
  // While it is finding something out, look for the answer now and then.
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void api.crewAsks(crewId).then(setItems).catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [crewId, waiting]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [items.length, asking, steps?.length, waiting]);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question || asking) return;
    setAsking(question); setText(""); setError(null);
    try { const item = await api.askCrew(crewId, question); setItems(v => [...v, item]); if (item.kind === "preference" || item.kind === "fact" || item.kind === "note") onPreference?.(); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "Could not ask.";
      // An older Mavo running behind a newer screen has no Ask yet.
      setError(message === "Not Found" ? "Restart Mavo to use Ask." : message);
      setText(question);
    }
    finally { setAsking(null); }
  }

  const shown = compact ? items.slice(-1) : items;
  const lastPending = [...shown].reverse().find(i => i.pending)?.id;
  return <div className={"ask" + (compact ? " ask-compact" : "")}>
    <div className="ask-thread" aria-live="polite">
      {shown.map(item => <div key={item.id} className="ask-pair">
        <p className="ask-q">{item.question}</p>
        {item.kind === "preference" || item.kind === "fact" || item.kind === "note" || item.kind === "schedule" || (item.kind === "run" && !item.pending && !item.delivered) ? <p className="ask-a ask-ack"><Check size={13} aria-hidden="true" />{item.answer}</p>
          : item.kind === "beyond" ? <div className="ask-a ask-beyond"><p>{item.answer}</p>
            {item.prompt && <button type="button" className="ask-link" onClick={() => buildWithMavo(item.prompt!)}>{/^make an agent/i.test(item.prompt) ? "Make an agent for this" : "Ask Mavo"}<ArrowRight size={13} /></button>}</div>
          : item.kind === "chat" ? <p className="ask-a ask-chat">{item.answer}</p>
          : item.kind === "error" ? <p className="ask-a ask-failed" role="alert">{item.answer}</p>
          : item.pending ? <div className="ask-a ask-working">
            {item.answer && <p className="ask-note">{item.answer}</p>}
            {item.id === lastPending && live ? <LiveActions cycle={live} /> : <ol className="ask-steps">
              {(item.id === lastPending ? steps ?? [] : []).map((line, n, all) => <li key={n} data-now={n === all.length - 1 || undefined}>
                {n < all.length - 1 ? <Check size={11} aria-hidden="true" /> : <LoaderCircle size={11} className="animate-spin" aria-hidden="true" />}{line}</li>)}
              {!(item.id === lastPending && steps?.length) && <li data-now><LoaderCircle size={11} className="animate-spin" aria-hidden="true" />Working on it…</li>}
            </ol>}</div>
          : <div className="ask-a"><Report compact={compact} text={item.answer ?? ""} truncate={!!compact} />
            {!!item.delivered?.length && <p className="ask-sent"><Check size={12} aria-hidden="true" />Sent to {item.delivered.map(d => d === "telegram" ? "Telegram" : "your desktop").join(" and ")}</p>}</div>}
      </div>)}
      {asking && <div className="ask-pair"><p className="ask-q">{asking}</p><p className="ask-a ask-wait"><span className="ask-dots" aria-label="Thinking"><i /><i /><i /></span></p></div>}
      {children}
      <div ref={end} />
    </div>
    {error && <p role="alert" className="ask-error">{error}</p>}
    <form className="ask-box" onSubmit={ask}>
      <input aria-label={`Ask ${name}`} placeholder={compact ? `Ask ${name}…` : `Ask, tell it what's happening, or how to work`} value={text} maxLength={1000} autoFocus={autoFocus} onChange={e => setText(e.target.value)} />
      <button type="submit" aria-label="Ask" disabled={!text.trim() || !!asking}>{asking ? <LoaderCircle size={14} className="animate-spin" /> : <ArrowUp size={15} />}</button>
    </form>
  </div>;
}
