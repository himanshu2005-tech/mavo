import { ArrowDown, ArrowRight, Check, Crown, Users, FileText, LoaderCircle, Minus, Plus, Send, ShieldCheck, UserCheck, X } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { initials } from "../lib/radialGraph";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";
import { agentStyle } from "./CrewFan";

/** The agents this one takes work from, and the ones it hands its work to. */
export function lineOf(crew: CrewInstance, all: CrewInstance[]) {
  const ups = (crew.configuration_plan?.upstream ?? []).map(id => all.find(c => c.id === id)).filter((c): c is CrewInstance => !!c);
  const downs = all.filter(c => (c.configuration_plan?.upstream ?? []).includes(crew.id));
  return { ups, downs };
}

function Face({ crew, onClick, onRemove }: { crew: CrewInstance; onClick: () => void; onRemove?: () => void }) {
  return <span className="line-chip">
    <button className="line-chip-main" onClick={onClick} title={`Open ${crew.name}`}>
      <span className="map-face sm" style={agentStyle(crew)}>{initials(crew.name)}</span>{crew.name}
    </button>
    {onRemove && <button className="line-chip-x" aria-label={`Stop taking work from ${crew.name}`} onClick={onRemove}><X size={12} /></button>}
  </span>;
}

/** Its place in a line of agents: where its work comes from and goes, whether it reviews that
 *  work (and how often it may send it back), and whether it asks you before it starts. */
export function LineSettings({ crew, crews }: { crew: CrewInstance; crews?: CrewInstance[] }) {
  const stored = useAppStore(s => s.crew);
  const all = crews ?? stored;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const plan = crew.configuration_plan ?? ({} as NonNullable<CrewInstance["configuration_plan"]>);
  const { ups, downs } = lineOf(crew, all);
  const open = (id: string) => useAppStore.getState().openAgent(id);
  const lead = plan.lead ? all.find(c => c.id === plan.lead) : undefined;
  const members = plan.leads_team ? (plan.members ?? []).map(id => all.find(c => c.id === id)).filter((c): c is CrewInstance => !!c) : [];
  if (plan.leads_team || lead) return <section className="line" aria-label="Team">
    <h4 className="graph-h">Team</h4>
    <div className="line-flow">
      {plan.leads_team && <div className="line-row"><Users size={13} className="line-dir" aria-hidden="true" /><span className="line-label">Leads</span>
        <div className="line-chips">{members.length ? members.map(m => <Face key={m.id} crew={m} onClick={() => open(m.id)} />) : <span className="line-none">No members yet</span>}</div></div>}
      {lead && <div className="line-row"><Crown size={13} className="line-dir" aria-hidden="true" /><span className="line-label">Works for</span>
        <div className="line-chips"><Face crew={lead} onClick={() => open(lead.id)} /></div></div>}
    </div>
    <p className="line-hint">{plan.leads_team
      ? "On its schedule it decides, step by step, who works next and on what; sends work back to be fixed; and asks you before anything that is yours to decide."
      : `Works only when ${lead!.name} gives it a task, and reports back to it.`}</p>
  </section>;
  const rounds = plan.max_rounds ?? 3;
  async function save(change: Parameters<typeof api.updateCrew>[1]) {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.updateCrew(crew.id, change)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change it."); }
    finally { setBusy(false); }
  }
  const candidates = all.filter(c => c.id !== crew.id && !ups.some(u => u.id === c.id) && c.status !== "draft");

  return <section className="line" aria-label="In a line">
    <h4 className="graph-h">In a line {busy && <LoaderCircle size={12} className="animate-spin" />}</h4>
    <div className="line-flow">
      <div className="line-row"><ArrowDown size={13} className="line-dir" aria-hidden="true" /><span className="line-label">Gets work from</span>
        <div className="line-chips">
          {ups.map(u => <Face key={u.id} crew={u} onClick={() => open(u.id)} onRemove={() => void save({ upstream: ups.filter(x => x.id !== u.id).map(x => x.id) })} />)}
          {!ups.length && !adding && <span className="line-none">Nobody: it starts on its own schedule</span>}
          {adding
            ? <select className="line-pick" autoFocus defaultValue="" onBlur={() => setAdding(false)} onChange={e => { const id = e.target.value; setAdding(false); if (id) void save({ upstream: [...ups.map(u => u.id), id] }); }}>
                <option value="" disabled>Choose an agent…</option>
                {candidates.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            : ups.length < 4 && candidates.length > 0 && <button className="line-add" onClick={() => setAdding(true)}><Plus size={12} />Add</button>}
        </div>
      </div>
      <div className="line-row"><ArrowRight size={13} className="line-dir" aria-hidden="true" /><span className="line-label">Hands work to</span>
        <div className="line-chips">{downs.length ? downs.map(d => <Face key={d.id} crew={d} onClick={() => open(d.id)} />) : <span className="line-none">Nobody yet</span>}</div>
      </div>
    </div>
    <div className="line-switches">
      <div className="access-row">
        <span className="map-icon"><ShieldCheck size={14} /></span>
        <span className="min-w-0 flex-1"><span className="access-label">Reviews the work it gets</span>
          <span className="line-hint">{plan.review ? "Passes it on when it is right, sends it back with what to fix when not" : "Off: it works on what it gets"}</span></span>
        <button type="button" role="switch" aria-checked={!!plan.review} aria-label="Reviews the work it gets" className="resource-switch" disabled={busy || !ups.length} onClick={() => void save({ review: !plan.review })}><span /></button>
      </div>
      {plan.review && <div className="access-row line-rounds">
        <span className="map-icon" />
        <span className="min-w-0 flex-1"><span className="access-label">Sends it back up to</span><span className="line-hint">Then you decide</span></span>
        <div className="line-stepper" role="group" aria-label="Times it may send work back">
          <button aria-label="Fewer" disabled={busy || rounds <= 1} onClick={() => void save({ max_rounds: rounds - 1 })}><Minus size={12} /></button>
          <b>{rounds}×</b>
          <button aria-label="More" disabled={busy || rounds >= 5} onClick={() => void save({ max_rounds: rounds + 1 })}><Plus size={12} /></button>
        </div>
      </div>}
      <div className="access-row">
        <span className="map-icon"><UserCheck size={14} /></span>
        <span className="min-w-0 flex-1"><span className="access-label">Asks you first</span>
          <span className="line-hint">{plan.approve_first ? "Waits for your OK on each piece of work it is handed" : "Starts as soon as it is handed work"}</span></span>
        <button type="button" role="switch" aria-checked={!!plan.approve_first} aria-label="Asks you first" className="resource-switch" disabled={busy || !ups.length} onClick={() => void save({ approve_first: !plan.approve_first })}><span /></button>
      </div>
      {!ups.length && <p className="line-hint line-pad">Reviewing and asking first apply to work handed over: add where its work comes from first.</p>}
    </div>
    {error && <p role="alert" className="access-hint">{error}</p>}
  </section>;
}

/** Work that waits for you: what was handed over (and its files, and a reviewer's notes), with
 *  Approve and Send back. */
export function HandoverCard({ crew }: { crew: CrewInstance }) {
  const held = crew.held!;
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "back" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stuck = held.kind === "review_stuck";
  const asks = held.kind === "ask";
  async function answer(approve: boolean) {
    setBusy(approve ? "approve" : "back"); setError(null);
    try { useAppStore.getState().upsertCrew(await api.answerHandover(crew.id, approve, note)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not send your answer."); }
    finally { setBusy(null); }
  }
  return <section className="handover" aria-label="Waiting for your OK">
    <div className="handover-head"><UserCheck size={15} aria-hidden="true" />
      <b>{asks ? `${crew.name} asks you` : stuck ? `${crew.name} and ${held.from} could not agree` : `${held.from} handed over work`}</b></div>
    {asks
      ? <p className="handover-question">{crew.question}</p>
      : <p className="handover-sub">{stuck ? "It was sent back the most times allowed. Send it on as it is, or back once more with your note." : `${crew.name} starts on it once you approve. Or send it back with what to change.`}</p>}
    {asks
      ? held.result && <details className="handover-context"><summary>Why it asks</summary><div className="handover-work">{held.result}</div></details>
      : <div className="handover-work">{held.result || "No text came with it."}</div>}
    {!!held.files?.length && <ul className="handover-files">{held.files.map(f => <li key={f}><FileText size={13} aria-hidden="true" /><span title={f}>{f}</span></li>)}</ul>}
    {stuck && held.review && <div className="handover-review"><b>Last review</b><p>{held.review}</p></div>}
    <textarea className="handover-note" rows={2} maxLength={1000} placeholder={asks ? "Add a note for it (optional)" : stuck ? "Your note (optional)" : "What to change, if you send it back"} value={note} onChange={e => setNote(e.target.value)} />
    <div className="handover-actions">
      <button className="btn-quiet" disabled={!!busy} onClick={() => void answer(false)}>{busy === "back" ? <LoaderCircle size={13} className="animate-spin" /> : <Send size={13} style={{ transform: "scaleX(-1)" }} />}{asks ? "No" : "Send back"}</button>
      <button className="btn-primary" disabled={!!busy} onClick={() => void answer(true)}>{busy === "approve" ? <LoaderCircle size={13} className="animate-spin" /> : <Check size={13} />}{asks ? "Yes, go on" : stuck ? "Send it on" : "Approve"}</button>
    </div>
    {error && <p role="alert" className="access-hint">{error}</p>}
  </section>;
}
