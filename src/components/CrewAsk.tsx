import { ArrowUp, Check, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import type { CrewAsk as Ask } from "../types";
import { Report } from "./Report";

/** Ask a Crew anything about what it has found. It answers from its reports; when a question
 *  needs a fresh look it goes and finds out, and the answer appears here when it is back.
 *  `compact` is the one-question form for the small desktop card. */
export function CrewAsk({ crewId, name, compact, autoFocus, onPreference }: { crewId: string; name: string; compact?: boolean; autoFocus?: boolean; onPreference?: () => void }) {
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
  }, [crewId, compact]);
  // While it is finding something out, look for the answer now and then.
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void api.crewAsks(crewId).then(setItems).catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [crewId, waiting]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [items.length, asking]);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question || asking) return;
    setAsking(question); setText(""); setError(null);
    try { const item = await api.askCrew(crewId, question); setItems(v => [...v, item]); if (item.kind === "preference" || item.kind === "fact") onPreference?.(); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "Could not ask.";
      // An older Mavo running behind a newer screen has no Ask yet.
      setError(message === "Not Found" ? "Restart Mavo to use Ask." : message);
      setText(question);
    }
    finally { setAsking(null); }
  }
  async function clear() { try { await api.clearAsks(crewId); setItems([]); } catch { /* stays as it was */ } }

  const shown = compact ? items.slice(-1) : items;
  return <div className={"ask" + (compact ? " ask-compact" : "")}>
    {(shown.length > 0 || asking) && <div className="ask-thread" aria-live="polite">
      {shown.map(item => <div key={item.id} className="ask-pair">
        <p className="ask-q">{item.question}</p>
        {item.kind === "preference" || item.kind === "fact" ? <p className="ask-a ask-ack"><Check size={13} aria-hidden="true" />{item.answer}</p>
          : item.pending ? <>{item.answer && <div className="ask-a ask-note"><Report compact text={item.answer} /></div>}<p className="ask-a ask-wait"><LoaderCircle size={12} className="animate-spin" />Working on it…</p></>
          : <div className="ask-a"><Report compact={compact} text={item.answer ?? ""} /></div>}
      </div>)}
      {asking && <div className="ask-pair"><p className="ask-q">{asking}</p><p className="ask-a ask-wait"><span className="ask-dots" aria-label="Thinking"><i /><i /><i /></span></p></div>}
      <div ref={end} />
    </div>}
    {error && <p role="alert" className="ask-error">{error}</p>}
    <form className="ask-box" onSubmit={ask}>
      <input aria-label={`Ask ${name}`} placeholder={compact ? `Ask ${name}…` : `Ask anything, or tell it how to work`} value={text} maxLength={1000} autoFocus={autoFocus} onChange={e => setText(e.target.value)} />
      <button type="submit" aria-label="Ask" disabled={!text.trim() || !!asking}>{asking ? <LoaderCircle size={14} className="animate-spin" /> : <ArrowUp size={15} />}</button>
    </form>
    {!compact && items.length > 0 && <button type="button" className="ask-clear" onClick={() => void clear()}>Clear</button>}
  </div>;
}
