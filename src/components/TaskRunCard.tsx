import type { Thought } from "../types";
import { ThoughtCard, isCard } from "./ThoughtCard";
import { Bot, Check, CircleAlert, Copy, ExternalLink, Folder, LoaderCircle, RotateCcw, Sparkles, Trash2, Users, X } from "lucide-react";
import { Report, withoutThinking } from "./Report";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { plainError } from "../lib/shownText";
import { ReplayButton } from "./ReplayButton";
import type { TaskRun, TaskStepView } from "../types";

const KIND: Record<TaskStepView["kind"], string> = { helper: "Helper", agent: "Agent", crew: "Your Crew", automation: "Automation", skill: "Built-in", read: "Read", act: "Action" };
const WORD: Record<TaskRun["status"], string> = { running: "Working", done: "Done", needs_you: "Needs you", failed: "Could not finish", stopped: "Stopped" };

function StepIcon({ step }: { step: TaskStepView }) {
  if (step.status === "running") return <LoaderCircle size={13} className="animate-spin" aria-hidden="true" />;
  if (step.status === "done") return <Check size={13} aria-hidden="true" />;
  if (step.status === "blocked") return <CircleAlert size={13} aria-hidden="true" />;
  if (step.status === "failed" || step.status === "stopped") return <X size={13} aria-hidden="true" />;
  return <i className="task-dot" aria-hidden="true" />;
}

const KindIcon = ({ kind }: { kind: TaskStepView["kind"] }) => kind === "crew" ? <Users size={12} aria-hidden="true" /> : kind === "agent" ? <Bot size={12} aria-hidden="true" /> : <Sparkles size={12} aria-hidden="true" />;

export interface Build { folder: string; project: string; passed: boolean; checks: { text: string; ok: boolean }[]; web: boolean }

/** A build team's report ("Built in <folder>. ... TEST: passed. - ..."), read into its parts. */
export function parseBuild(text: string): Build | null {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const head = /^Built in (.+?)\. (Open|Run) /.exec(lines[0] ?? "");
  if (!head) return null;
  const folder = head[1];
  const checks = lines.filter(l => l.startsWith("- ")).map(l => {
    const line = l.slice(2);
    // As the tester writes them: "Script error", "SYNTAX ERROR", "did NOT", "shows nothing", "is gone".
    return { text: line, ok: !(/error/i.test(line) || /\bNOT\b|shows nothing|is gone|^Still open/.test(line)) };
  });
  return { folder, project: folder.split(/[\\/]/).pop() ?? folder, passed: lines.some(l => l === "TEST: passed."), checks, web: head[2] === "Open" };
}

function BuildResult({ build }: { build: Build }) {
  const [error, setError] = useState<string | null>(null);
  async function open(what: "app" | "folder") { setError(null); try { await api.openProject(build.project, what); } catch (e) { setError(e instanceof Error ? e.message : "Could not open it."); } }
  return <div className="build-result" data-passed={build.passed}>
    <div className="build-head">
      <span className="build-badge">{build.passed ? <Check size={13} /> : <CircleAlert size={13} />}{build.passed ? "Built and tested" : "Built, with problems"}</span>
    </div>
    <p className="build-name">{build.project}</p>
    <p className="build-path" title={build.folder}>{build.folder}</p>
    <div className="build-actions">
      {build.web && <button className="btn-primary !py-1.5" onClick={() => void open("app")}><ExternalLink size={13} />Open app</button>}
      <button className="btn-secondary !py-1.5" onClick={() => void open("folder")}><Folder size={13} />Show folder</button>
    </div>
    {build.checks.length > 0 && <ul className="build-checks" aria-label="What was tested">{build.checks.map(c =>
      <li key={c.text} data-ok={c.ok}>{c.ok ? <Check size={13} /> : <X size={13} />}<span>{c.text}</span></li>)}</ul>}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </div>;
}

/** A one-time task in the chat: who is doing what, how far it is, and the result. Only what was
 *  asked for is shown as the result; the steps stay small underneath. */
/** `details`: shown under a result already on screen: only how it was done (steps, model calls,
 *  replay), never the title, status or the result a second time. */
/** What a step did that a person cares about: a page it read, a line like "Read 14 emails".
 *  Never its working notes ("Goal", "Given", "Approach", quoting JSON). */
export function shownThoughts(thinking?: Thought[]): Thought[] {
  return (thinking ?? []).filter(t => t.tool !== "action" && (isCard(t) || (!t.label && !!t.text?.trim())));
}

export function TaskRunCard({ run, onDeleted, details = false }: { run: TaskRun; onDeleted?: (id: string) => void; details?: boolean }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [retried, setRetried] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [answer, setAnswer] = useState("");
  const [answeredAt, setAnsweredAt] = useState<string | null>(null);
  const waiting = run.steps.find(s => s.status === "blocked");
  const asking = run.status === "running" ? run.steps.find(s => s.ask) : undefined;
  const sent = !!asking && answeredAt === (asking.asked_at ?? asking.ask);
  const canRetry = (run.status === "needs_you" || run.status === "failed" || run.status === "stopped") && !retried;
  useEffect(()=>{if(run.status==='running'){setRetried(false);setNote(null);}},[run.status]);
  async function copy() { try { await navigator.clipboard.writeText(withoutThinking(run.result ?? "").trim()); setCopied(true); window.setTimeout(() => setCopied(false), 1800); } catch { setError("Could not copy."); } }
  // Stopped, failed or finished: it can go. A running task is stopped first.
  const canDelete = run.status === "done" || run.status === "failed" || run.status === "stopped";
  async function remove() {
    if (!window.confirm(`Delete "${run.title.replace(/^build:\s*/i, "")}"?`)) return;
    setError(null);
    try { await api.deleteTask(run.id); setDeleted(true); onDeleted?.(run.id); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not delete it."); }
  }
  async function stop() { try { await api.stopTask(run.id); } catch (e) { setError(e instanceof Error ? e.message : "Could not stop it."); } }
  async function signIn() { setError(null); try { await api.browserSignIn(run.sign_in_url ?? ""); setNote("Sign in in the window that opened, then press Try again."); } catch (e) { setError(e instanceof Error ? e.message : "Could not open Mavo's browser."); } }
  async function reply(choice?: string) {
    const text = (choice ?? answer).trim();
    if (!asking || !text || sent) return;
    setError(null);
    try { await api.answerTask(run.id, text, asking.id); setAnswer(""); setAnsweredAt(asking.asked_at ?? asking.ask ?? ""); } catch (e) { setError(e instanceof Error ? e.message : "Could not send your answer."); }
  }
  async function again() { setError(null); try { await api.retryTask(run.id); setRetried(true); setNote("Resuming this task. Completed steps are retained."); } catch (e) { setError(e instanceof Error ? e.message : "Could not resume this task."); } }
  if (deleted) return null;
  return <section className={"task-card" + (details ? " task-details" : " surface")} aria-label={"Task: " + run.title} data-status={run.status}>
    {details ? <p className="text-xs text-zinc-500">{run.used_calls} of {run.budget_calls} model calls</p> : <div className="flex items-start justify-between gap-3">
      <div className="min-w-0"><h3 className="text-[15px] font-semibold tracking-[-0.01em]">{run.title}</h3><p className="mt-0.5 text-xs text-zinc-500">One-time task · {run.used_calls} of {run.budget_calls} model calls</p></div>
      <span className="chip" data-tone={run.status === "done" ? "on" : asking ? "limit" : run.status === "running" ? "info" : run.status === "needs_you" ? "limit" : "warn"}><i />{asking ? "Asks you" : WORD[run.status]}</span>
    </div>}
    {asking && asking.choices?.length ? <form className="grid gap-2" aria-label="Your call" onSubmit={e => { e.preventDefault(); void reply(); }}>
      <p role="status" className="notice">{asking.ask}</p>
      <div className="flex flex-wrap items-center gap-2">{asking.choices.map((choice, i) =>
        <button type="button" key={choice} className={i === 0 ? "btn-primary !py-1.5" : "btn-secondary !py-1.5"} disabled={sent} onClick={() => void reply(choice)}>{choice}</button>)}
        {sent && <span className="text-xs text-zinc-500">Sent.</span>}
      </div>
      <div className="flex items-center gap-2">
        <input className="form-control min-w-0 flex-1" aria-label="Or say it in your words" placeholder={sent ? "Sent." : "Or in your words, e.g. fix only the timer bug"} value={answer} maxLength={2000} disabled={sent} onChange={e => setAnswer(e.target.value)} />
        <button type="submit" className="btn-secondary !py-1" disabled={sent || !answer.trim()}>Send</button>
      </div>
    </form> : asking && <form className="grid gap-2" aria-label="Answer the agent" onSubmit={e => { e.preventDefault(); void reply(); }}>
      <p role="status" className="notice">{asking.ask}</p>
      <div className="flex items-center gap-2">
        <input className="form-control min-w-0 flex-1" aria-label="Your answer" placeholder={sent ? "Sent. The agent is carrying on." : "Your answer"} value={answer} maxLength={2000} disabled={sent} autoFocus onChange={e => setAnswer(e.target.value)} />
        <button type="submit" className="btn-secondary !py-1" disabled={sent || !answer.trim()}>Send</button>
      </div>
      <p className="text-xs text-zinc-500">Your answer goes to the agent's model. Type passwords and codes in Mavo's browser instead.</p>
    </form>}
    {!details && run.status === "done" && run.result && (parseBuild(run.result)
      ? <BuildResult build={parseBuild(run.result)!} />
      : <div className="task-result"><div className="min-w-0 flex-1 break-words" aria-label="Result"><Report text={run.result} /></div><button className="btn-quiet !py-1" onClick={() => void copy()}><Copy size={12} />{copied ? "Copied" : "Copy"}</button></div>)}
    {run.status === "needs_you" && <p role="status" className="notice">{waiting?.question ?? run.error}</p>}
    {(run.status === "failed" || run.status === "stopped") && run.error && <p role="status" className="notice error-notice">{plainError(run.error)}</p>}
    {!!run.traces?.length && run.status !== "running" && <ReplayButton runId={run.id} steps={run.traces} />}
    <ol className="task-steps" aria-label="Steps">{run.steps.map(step => <li key={step.id} data-status={step.status} className="!block">
      {/* What it did, in plain words; how (its kind, its model) only on hover. Its working notes
          and the JSON between steps are never shown: only what it did that a person cares about
          (a page it read, "Read 14 emails"). */}
      <div className="flex min-w-0 items-center gap-2.5"><span className="task-mark"><StepIcon step={step} /></span><span className="min-w-0 flex-1 truncate" title={`${KIND[step.kind]}${step.model ? ` · ${step.model}` : ""}`}>{step.run_text || step.title}</span></div>
      {step.team?.length ? <div className="task-team" aria-label="The team">{step.team.map(name => <span key={name}>{name}</span>)}</div> : null}
      {shownThoughts(step.thinking).length ? <ol className="task-did" aria-label={"What " + (step.run_text || step.title) + " did"}>
        {shownThoughts(step.thinking).map((t, i) => isCard(t) ? <li key={i}><ThoughtCard thought={t} /></li> : <li key={i} className="task-did-line">{t.text}</li>)}
      </ol> : null}
    </li>)}</ol>
    {run.steps.filter(s=>s.interrupted).map(step=><InterruptedCheckpoint key={step.id} runId={run.id} stepId={step.id} title={step.title}/>)}
    {(run.status === "running"||run.status==='needs_you') && <div><button className="btn-quiet !py-1" onClick={() => void stop()}>Stop</button></div>}
    {canRetry && <div className="flex flex-wrap items-center gap-2">
      {run.status === "needs_you" && run.sign_in_url && <button className="btn-secondary !py-1" onClick={() => void signIn()}><ExternalLink size={12} />Sign in</button>}
      <button className="btn-quiet !py-1" title="Resume unfinished work; completed steps are retained" disabled={run.steps.some(s=>s.interrupted)} onClick={() => void again()}><RotateCcw size={12} />Try again</button>
      {canDelete && <button className="btn-quiet !py-1" onClick={() => void remove()}><Trash2 size={12} />Delete</button>}
      {run.status === "needs_you" && run.sign_in_url && <span className="text-xs text-zinc-500">{run.sign_in_url}</span>}
    </div>}
    {canDelete && !canRetry && <div><button className="btn-quiet !py-1" onClick={() => void remove()}><Trash2 size={12} />Delete</button></div>}
    {note && <p role="status" className="text-xs leading-6 text-zinc-400">{note}</p>}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </section>;
}

type Said = { role: string; created_at: string };
function InterruptedCheckpoint({runId,stepId,title}:{runId:string;stepId:string;title:string}){
  const [value,setValue]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false),[error,setError]=useState<string|null>(null);
  async function verify(){setBusy(true);setError(null);try{await api.verifyTaskCheckpoint(runId,stepId,value);setSaved(true);}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <form className="conversation-activity mt-3" onSubmit={e=>{e.preventDefault();void verify();}}><strong>Review interrupted work · {title}</strong><p>Actions may have happened before Mavo stopped. This step will not be replayed. Check its outcome in Actions, then record the result you verified, or stop the task.</p>
    <textarea aria-label={`Verified result for ${title}`} className="form-control" value={value} maxLength={6000} disabled={busy||saved} onChange={e=>setValue(e.target.value)}/><button className="btn-secondary mt-2" disabled={busy||saved||!value.trim()}>{saved?'Checkpoint saved':'Save verified result'}</button>{error&&<p role="alert" className="notice error-notice">{error}</p>}
  </form>;
}
type Setup = { status: string; created_at: string; updated_at: string };
export type TimelineItem<M extends Said, C extends Setup = Setup> =
  | { kind: "message"; message: M; at: number }
  | { kind: "task"; run: TaskRun; at: number }
  | { kind: "setup"; crews: C[]; at: number };

/** Messages, task cards and Crew cards in the order they happened. At the same moment: the
 *  person's message, then the reply, then what it started. (The old tie-break said "message
 *  first" both ways round for two messages, so a reply could be shown above the message it
 *  answered.) An added Crew's card sits where it was proposed (it used to stay at the very
 *  bottom, under every later message and task, long after it had been added); a draft still
 *  waiting for the person stays at the bottom, where they will act on it. */
export function chatTimeline<M extends Said, C extends Setup = Setup>(messages: M[], runs: TaskRun[], crews: C[] = []): TimelineItem<M, C>[] {
  const rank = (item: TimelineItem<M, C>) => item.kind === "message" ? (item.message.role === "user" ? 0 : 1) : 2;
  const when = (value: string) => { const t = moment(value); return Number.isFinite(t) ? t : 0; };
  // A draft waits for the person (Add Crew, Refine, a question): it stays at the bottom, by the
  // message box, not up where it was last changed ("I have to scroll up to add it").
  const placed = (crew: C) => crew.status === "draft" ? Number.POSITIVE_INFINITY : when(crew.created_at);
  return [
    ...messages.map(message => ({ kind: "message" as const, message, at: when(message.created_at) })),
    ...runs.map(run => ({ kind: "task" as const, run, at: when(run.created_at) })),
    ...(crews.length ? [{ kind: "setup" as const, crews, at: Math.max(...crews.map(placed)) }] : []),
  ].sort((a, b) => a.at - b.at || rank(a) - rank(b));
}

/** A server time as a moment: times without a zone are UTC. */
export function moment(value: string): number {
  return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : value + "Z");
}

/** The tasks asked for in one conversation, oldest first, kept up to date while the chat is open. */
export function useConversationTasks(conversationId: string | undefined): TaskRun[] {
  const [runs, setRuns] = useState<TaskRun[]>([]);
  useEffect(() => {
    let active = true;
    setRuns([]);
    if (!conversationId) return;
    async function load() { try { const value = await api.tasks(conversationId); if (active) setRuns([...value].reverse()); } catch { /* the cards simply stay as they were */ } }
    void load();
    const timer = window.setInterval(() => void load(), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [conversationId]);
  return runs;
}

/** The tasks asked for in one conversation, kept up to date while the chat is open. */
export function ConversationTasks({ conversationId }: { conversationId: string }) {
  const [runs, setRuns] = useState<TaskRun[]>([]);
  useEffect(() => {
    let active = true;
    setRuns([]);
    async function load() { try { const value = await api.tasks(conversationId); if (active) setRuns([...value].reverse()); } catch { /* the cards simply stay as they were */ } }
    void load();
    const timer = window.setInterval(() => void load(), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [conversationId]);
  return <>{runs.map(run => <TaskRunCard key={run.id} run={run} />)}</>;
}
