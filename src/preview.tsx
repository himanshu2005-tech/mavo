/* Design preview: every look in one page, no backend. Open /preview.html while `vite` runs.
   ?theme=light|dark picks the theme, ?accent=graphite|coral|emerald|violet|sky the accent; ?view=cards|drawer|desktop|controls|setup narrows the page. */
import React from "react";
import ReactDOM from "react-dom/client";
import { CrewCard, DraftCard, NewCrewCard } from "./components/CrewCard";
import { CrewSetupCard } from "./components/CrewSetupCard";
import { ActionsPage } from "./pages/ActionsPage";
import { TaskRunCard } from "./components/TaskRunCard";
import { useAppStore } from "./store/app";
import { CrewArt, HUES, artStyle } from "./components/CrewArt";
import { DesktopUpdateCard } from "./components/DesktopUpdateCard";
import { Report } from "./components/Report";
import { MissedList, OrbMenu, firstLine } from "./pages/StatusOverlay";
import { ActivityView, happenings } from "./pages/EmptyPages";
import { AgentInbox } from "./components/AgentInbox";
import { PageHeader } from "./components/PageHeader";
import { CrewQuickList } from "./components/CrewQuickList";
import { CrewGrants } from "./components/CrewGrants";
import { CrewFan } from "./components/CrewFan";
import { MentionText } from "./components/CrewMentions";
import { Home } from "./pages/Home";
import { AgentGraph } from "./components/AgentGraph";
import { applyAccent, applyTheme, type AccentChoice, type ThemeChoice } from "./lib/theme";
import type { CrewInstance, Reminder, TaskRun } from "./types";
import "./styles.css";

const query = new URLSearchParams(window.location.search);
applyTheme((query.get("theme") as ThemeChoice) ?? "dark");
applyAccent((query.get("accent") as AccentChoice) ?? "graphite");
const view = query.get("view") ?? "all";

const SAMPLE = `Checked **placements.haveloc.com**. The portal currently displays **108 Jobs Listed**.

Please note: The **Location** field is not visible in the main table view on this page.

Based on the "Job Posted Date" column, the most recent newly posted listings visible are:

1. **Job Title:** CEI INDIA for BTech 2027 batch
   **Company:** CEI INDIA PRIVATE LIMITED
   **Posted Date:** 2 Nov 2026
   **Status:** Open For Applications

2. **Job Title:** Hut Labs : B.Tech 2027 POB
   **Company:** HuT Labs
   **Posted Date:** 31 Oct 2026

Other active listings on this page span from 28 Oct 2026 back to 29 Sept 2026.`;

const crew = (template: string, name: string, extra: Partial<CrewInstance> = {}) => ({
  id: template, name, template_id: template, status: "active", is_running: true, runtime_state: "waiting", trigger_type: "interval",
  review_interval_minutes: 30, responsibilities: "x", configuration_plan: null, permissions: [], resources: [], daily_triggers: [], ...extra,
}) as unknown as CrewInstance;

const NAMES: Record<string, string> = { schedule: "Meal reminders", communication: "Important mail", computer: "PC health", coding: "Repo watcher", health: "Health check-in", fitness: "Workout plan", learning: "Study buddy", research: "Paper tracker", project: "Launch board", files: "Downloads tidy", operator: "Placements watch", travel: "Trip planner", home: "Home routine", wellbeing: "Evening wind-down", "personal-admin": "Bills & forms" };
const NEWS = `- UK data centres' emissions could double by 2030 — The Guardian
- New benchmark tests how well AI agents follow governance rules — arXiv
- Survey: most graduate courses still skip hands-on AI training — Nature`;
const reminder: Reminder = { id: "1", crew_id: "research", crew_name: "AI News", message: NEWS, scheduled_for: new Date(Date.now() - 4 * 60000).toISOString(), kind: "crew_update" } as Reminder;

function Cards() {
  return <div className="crew-grid">
    <NewCrewCard onClick={() => undefined} />
    <DraftCard crew={crew("research", "Paper tracker", { status: "draft" })} onContinue={() => undefined} onDiscard={() => undefined} />
    {Object.keys(HUES).map((t, i) => <CrewCard key={t} crew={crew(t, NAMES[t], { is_running: i % 4 !== 3, runtime_state: i % 5 === 2 ? "needs_input" : "waiting" })} busy={false} onOpen={() => undefined} onToggle={() => undefined} />)}
  </div>;
}

function Drawer() {
  return <div style={{ width: 680, background: "var(--background)", borderRadius: 24, overflow: "hidden", boxShadow: "var(--shadow-card), inset 0 0 0 1px var(--border)" }}>
    <div className="drawer-art" style={artStyle("operator")}><CrewArt template="operator" /></div>
    <div className="crew-detail">
      <h2 className="text-[24px] font-semibold tracking-[-0.024em] text-zinc-50">Placements watch</h2>
      <p className="mt-1.5 flex items-center gap-3 text-[13px] text-zinc-500">Every 30 min <span className="badge badge-green">Running</span></p>
      <div className="mt-7"><h3 className="mb-3 text-[13px] font-semibold text-zinc-300">Latest report</h3><Report text={SAMPLE} /></div>
    </div>
  </div>;
}

function Desktop() {
  return <div className="space-y-4"><Orbs /><Pill />{CardOnly()}</div>;
}

function Orbs() {
  const mark = <svg className="orb-mark" viewBox="0 0 24 24" fill="none"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg>;
  return <div className="orbs" style={{ display: "flex", gap: 24, padding: 16 }}>{["idle", "running", "attention", "paused", "offline"].map(state =>
    <button key={state} className="status-orb" data-state={state} data-muted="false" title={state}>{mark}<span className="orb-dot" /></button>)}
    <button className="status-orb" data-state="idle" data-muted="true">{mark}<span className="orb-dot" /><span className="orb-badge">15m</span></button></div>;
}

function Pill() {
  return <div style={{ width: 380, height: 72 }} className="pill-stage"><button className="pill"><span className="pill-mark"><svg className="orb-mark" viewBox="0 0 24 24" fill="none"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg></span><span className="pill-text"><b>AI News</b><span>{firstLine(NEWS)}</span></span><span className="pill-time">4m</span></button></div>;
}

function CardOnly() {
  return <div style={{ width: 400, height: 360 }} className="toast-stage"><section className="toast-shell">
    <DesktopUpdateCard reminder={reminder} busy={false} onDismiss={() => undefined} onOpen={() => undefined} onClose={() => undefined} onSpeak={() => undefined} />
  </section></div>;
}

function Controls() {
  return <div className="space-y-5" style={{ width: 460 }}>
    <div className="flex flex-wrap items-center gap-3"><button className="btn-primary">Add Crew</button><button className="btn-secondary">Try it first</button><button className="btn-quiet">Details</button><button className="btn-secondary btn-stop">Stop</button></div>
    <div className="segmented"><button aria-pressed>Crew</button><button>Timeline</button><button>Activity</button></div>
    <input className="form-control" placeholder="Describe what you need" /><select className="form-control"><option>AIRouter default</option></select>
    <div className="list-card"><div className="list-row"><div className="flex-1"><p className="text-[14px] text-zinc-50">Tell me on my desktop</p><p className="text-[12px] text-zinc-500">New results show on the Mavo icon.</p></div><button className="resource-switch" role="switch" aria-checked="true"><span /></button></div>
      <div className="list-row"><div className="flex-1"><p className="text-[14px] text-zinc-50">Open and read placements.haveloc.com</p></div><button className="resource-switch" role="switch" aria-checked="false"><span /></button></div></div>
    <div className="notice">Looks signed out. Sign in once and the Crew keeps working.</div>
    <div className="signin-panel"><h4 className="text-[15px] font-semibold text-zinc-50">Please sign in</h4><p className="mt-1 text-[13px] text-zinc-400">placements.haveloc.com is open in Mavo's browser.</p></div>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" defaultChecked /> A checkbox</label>
  </div>;
}

/* The card shown in chat while a Crew is set up. Needs the API mocked (see the Playwright check). */
function Setup() {
  useAppStore.setState({ routes: [{ id: "r1", name: "Agent", provider: "ollama", model: "m" }] as never });
  const action = (operation: string) => ({ operation, reason: "Read", folders: [], domains: ["airouter.in"], apps: [], recipients: [] });
  const draft = {
    id: "air", name: "AIRouter Credits Watch", template_id: "operator", status: "draft", route_id: null, is_running: false, permissions: ["model:invoke"],
    responsibilities: "Check the AIRouter credits balance on airouter.in.", trigger_type: "interval", review_interval_minutes: 30,
    configuration_plan: { phase: "needs_access", questions: [], draft: {
      name: "AIRouter Credits Watch", template_id: "operator", responsibilities: "Check the AIRouter credits balance on airouter.in.", trigger_type: "interval", review_interval_minutes: 30,
      requested_google_capabilities: [], requested_google_resources: [], requested_actions: ["browser.open", "browser.read", "browser.navigate"].map(action),
      requested_notifications: true, requested_device_metrics: false, requested_weather: false, daily_call_limit: 48, max_model_calls: 5,
    } },
  } as unknown as CrewInstance;
  return <div style={{ width: 680 }}><CrewSetupCard crew={draft} onRefine={() => undefined} /></div>;
}

/* A Crew that builds things: its tools are one switch. Needs the API mocked. */
function Blender() {
  useAppStore.setState({ routes: [{ id: "r1", name: "Agent", provider: "ollama", model: "m" }] as never });
  const action = (operation: string) => ({ operation, reason: "Build", folders: [], domains: [], apps: operation === "project.run" ? ["blender.exe"] : [], recipients: [] });
  const draft = {
    id: "drone", name: "Blender Drone Builder", template_id: "operator", status: "draft", route_id: null, is_running: false, permissions: ["model:invoke"],
    responsibilities: "Build a drone model in Blender and open it.", trigger_type: "manual", review_interval_minutes: 30,
    configuration_plan: { phase: "needs_access", questions: [], draft: {
      name: "Blender Drone Builder", template_id: "operator", responsibilities: "Build a drone model in Blender and open it.", trigger_type: "manual", review_interval_minutes: 30,
      requested_google_capabilities: [], requested_google_resources: [], requested_actions: ["project.create", "project.write", "project.read", "project.run"].map(action),
      requested_notifications: true, requested_device_metrics: false, requested_weather: false, daily_call_limit: 24, max_model_calls: 8,
    } },
  } as unknown as CrewInstance;
  return <div style={{ width: 680 }}><CrewSetupCard crew={draft} onRefine={() => undefined} /></div>;
}

/* The Actions page itself. Needs the API mocked. */
function ActionsView() {
  useAppStore.setState({ crew: [crew("operator", "Blender Drone Builder")] as never, setupDrafts: [] as never });
  return <div style={{ width: 980 }}><ActionsPage /></div>;
}

/* One-time tasks as they look in the chat: working, done, needing the person. */
function TaskCards() {
  const base = { id: "t", title: "Microsoft Form", result: null, error: null, used_calls: 3, budget_calls: 15, conversation_id: "c", created_at: "2026-11-04T05:00:00Z", finished_at: null } as const;
  return <div style={{ width: 680, display: "grid", gap: 16 }}>
    <TaskRunCard run={{ ...base, status: "running", steps: [{ id: "a", title: "Create the form", kind: "agent", status: "running" }, { id: "b", title: "Check the fields", kind: "helper", status: "waiting" }] }} />
    <TaskRunCard run={{ ...base, id: "u", title: "Latest Python version", status: "done", used_calls: 5, budget_calls: 10, result: "3.15.2", steps: [{ id: "a", title: "Read python.org", kind: "agent", status: "done" }, { id: "b", title: "Double-check", kind: "helper", status: "done" }] }} />
    <TaskRunCard run={{ ...base, id: "n", title: "Microsoft Form", status: "needs_you", used_calls: 0, steps: [{ id: "a", title: "Create the form", kind: "agent", status: "blocked", question: "Turn on in Actions (Defaults): Open page, Read page. Tip: Set all, then without limits, lets one-time tasks run without asking." }] }} />
  </div>;
}

const MATH = String.raw`Compound interest on ₹10,000 at 8% for 5 years:

$$A = P\left(1 + \frac{r}{n}\right)^{nt} = 10000\,(1.08)^5 \approx 14{,}693$$

\begin{tabular}{lrr}
\hline
Year & Balance & Interest \\
\hline
1 & ₹10,800 & ₹800 \\
3 & ₹12,597 & ₹933 \\
5 & ₹14,693 & ₹1,088 \\
\end{tabular}`;

function BuildCards() {
  const folder = "C:\\Users\\himan\\Documents\\Mavo Projects\\expense-todo";
  const steps = ["Architect designs it", "Coder writes it", "Mavo saves the files", "Tester tries it in a browser", "Reviewer checks it", "Debugger fixes what was found", "Mavo saves the fixes", "Tester tries it again"]
    .map((title, i) => ({ id: "s" + i, title, kind: (i % 4 < 2 ? "helper" : "skill") as "helper" | "skill", status: "done" as const }));
  const result = `Built in ${folder}. Open ${folder}\\index.html in your browser to use it.\nTEST: passed.\n- The page opened and shows content.\n- Typing an item and pressing Enter (or Add) put it on the page.\n- After a reload the item is still there.\n- Pressed each of its 5 buttons once.`;
  const run = { id: "b", title: "Build: Expense Todo", status: "done", result, error: null, used_calls: 4, budget_calls: 8, conversation_id: "c", created_at: "", finished_at: null, steps } as TaskRun;
  const team = ["React/UI developer", "Feed normalizer specialist", "Accessibility & semantics reviewer", "Production/edge-case reviewer", "Integration fixer", "Tester"];
  const working = { ...run, id: "w", title: "Build: MultiFeed News", status: "running", result: null, used_calls: 2, budget_calls: 23, steps: [
    { id: "lead", title: "Lead picked the team", kind: "helper", status: "done", team, thinking: [
      { label: "Goal", text: "A React page that merges several RSS feeds into one list, newest first, with search and per-source filters." },
      { label: "Given", text: "React was asked for, so the pinned CDN builds in one index.html; feeds must be added and removed by the person." },
      { label: "Approach", text: "One developer writes the app; a feed specialist normalises dates and titles; two reviewers cover accessibility and failure cases." },
      { label: "Risks", text: "A feed can be slow, malformed or blocked, so every source needs its own error state and a retry." },
      { label: "Check", text: "Adding a feed, removing it, reload keeps it, and a failed feed never breaks the rest." },
    ] },
    { id: "build1", title: "React/UI developer writes it, with every feed normalised into one clean, searchable list", kind: "helper", status: "running" },
  ] } as TaskRun;
  return <div className="build-preview grid gap-4" style={{ width: 640 }}><TaskRunCard run={working} /><TaskRunCard run={run} /></div>;
}

function MathCard() {
  const r = { ...reminder, crew_name: "Finance Tutor", message: MATH } as Reminder;
  return <div style={{ width: 400, height: 380 }} className="toast-stage math-card"><section className="toast-shell">
    <DesktopUpdateCard reminder={r} busy={false} onDismiss={() => undefined} onOpen={() => undefined} onClose={() => undefined} onSpeak={() => undefined} />
  </section></div>;
}

function Missed() {
  const at = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString().replace("Z", "");
  const items = [
    { ...reminder, id: "m1", crew_name: "News Watch", message: "Three new stories: RBI holds rates; ISRO's next launch window; monsoon withdraws early.", scheduled_for: at(190) },
    { ...reminder, id: "m2", crew_name: "Inbox Summary", message: "2 new job emails: a Frontend Engineer role at Zepto, and an interview slot from Razorpay.", scheduled_for: at(95) },
    { ...reminder, id: "m3", crew_name: "AI Study Sync", message: "Study block 18:00–19:30 is on your calendar: read the CogAgent paper, section 3.", scheduled_for: at(32) },
  ] as Reminder[];
  return <div className="missed-preview orb-menu-stage" style={{ width: 320, height: 400, padding: 8 }}>
    <MissedList items={items} busy={false} error={null} onBack={() => undefined} onClear={() => undefined} onOpen={() => undefined} />
  </div>;
}

function QuickCrew() {
  const soon = (minutes: number) => new Date(Date.now() + minutes * 60000).toISOString().replace("Z", "");
  const base = { template_id: "research", responsibilities: "", autonomy_mode: "reactive", status: "active", provider: "airouter", model: "m", route_id: "r", max_model_calls: 4, schedule_time: null, reminder_text: null, resources: [], permissions: [], triggers: [], created_at: "", updated_at: "", is_running: true, runtime_state: "waiting", review_interval_minutes: 30, daily_call_limit: 96, daily_calls_used: 0, trigger_type: "interval", last_runtime_error: null };
  const crews = [
    { ...base, id: "a", name: "World News Watch", next_run_at: soon(12) },
    { ...base, id: "b", name: "Placements Job Watch", next_run_at: soon(41), configuration_plan: { upstream: ["c"] } },
    { ...base, id: "c", name: "Inbox Summary", trigger_type: "gmail_message", next_run_at: null },
    { ...base, id: "d", name: "Battery Watch", template_id: "computer", runtime_state: "needs_input", next_run_at: null, last_runtime_error: "Allow device metrics in Edit to monitor this computer." },
    { ...base, id: "e", name: "AI Study Sync", is_running: false, next_run_at: null },
  ] as unknown as CrewInstance[];
  const fan = (list: CrewInstance[], name: string) => <div className={"fan-preview " + name} style={{ position: "relative", width: 300, height: 300, borderRadius: 16, background: "linear-gradient(135deg,#3a6fd8,#1b3b8f)" }}>
    <CrewFan initial={list} width={300} height={300} onList={() => undefined} onOpen={() => undefined} />
    <div style={{ position: "absolute", right: 10, bottom: 10, width: 40, height: 40, borderRadius: "50%", background: "#161615", display: "grid", placeItems: "center", color: "#f4f2ed", fontWeight: 700, zIndex: 3 }}>M</div>
  </div>;
  const more = [...crews, { ...crews[0], id: "f", name: "Battery Watch 2", template_id: "computer", runtime_state: "working" } as CrewInstance];
  return <div style={{ display: "flex", gap: 32, alignItems: "flex-start", flexWrap: "wrap" }}>
    {fan(crews.slice(0, 3), "fan-three")}
    {fan(more, "fan-six")}
    <div className="quick-preview orb-menu-stage" style={{ width: 320, height: 400, padding: 8 }}><CrewQuickList initial={crews} onBack={() => undefined} onOpen={() => undefined} /></div>
    <div className="grants-preview" style={{ width: 520 }}><div className="notice" role="status">{crews[3].last_runtime_error}<CrewGrants crew={crews[3]} /></div></div>
  </div>;
}

/** The desktop widget at its real window sizes, over a wallpaper: the menu, the agents, missed. */
function Widget() {
  const soon = (minutes: number) => new Date(Date.now() + minutes * 60000).toISOString();
  const base = { template_id: "research", responsibilities: "", autonomy_mode: "reactive", status: "active", provider: "airouter", model: "m", route_id: "r", max_model_calls: 4, schedule_time: null, reminder_text: null, resources: [], permissions: [], triggers: [], created_at: "", updated_at: "", is_running: true, runtime_state: "waiting", review_interval_minutes: 30, daily_call_limit: 96, daily_calls_used: 0, trigger_type: "interval", last_runtime_error: null };
  const crews = [
    { ...base, id: "a", name: "Inbox Summary", next_run_at: soon(12), last_run_at: soon(-18) },
    { ...base, id: "b", name: "Placements Job Watch", review_interval_minutes: 60, next_run_at: soon(41), last_run_at: soon(-19) },
    { ...base, id: "c", name: "Instant Mail Alert", trigger_type: "gmail_message", next_run_at: null },
    { ...base, id: "d", name: "Battery Watch", template_id: "computer", runtime_state: "needs_input", next_run_at: null },
    { ...base, id: "f", name: "Temp & Drive Cleanup", runtime_state: "working", next_run_at: null },
    { ...base, id: "e", name: "Sleep Before Lunch", is_running: false, next_run_at: null },
  ] as unknown as CrewInstance[];
  const missed = [
    { id: "1", crew_id: "a", crew_name: "Inbox Summary", message: "3 important mails: placement drive on Friday, a bill due, and a reply from Prof. Rao.", scheduled_for: soon(-95), kind: "crew_update" },
    { id: "2", crew_id: "d", crew_name: "Battery Watch", message: "Battery fell 18% in an hour while idle.", scheduled_for: soon(-240), kind: "crew_update" },
  ] as unknown as Reminder[];
  const orb = (state = "running") => <div className="orb-foot"><span /><div className="orb-slot"><button className="status-orb" data-state={state}><svg className="orb-mark" width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg><span className="orb-dot" /></button></div></div>;
  const frame = (w: number, h: number, body: React.ReactNode, name: string, state = "running") => <div className={"widget-preview " + name} style={{ width: w, height: h, borderRadius: 4, background: "linear-gradient(160deg,#2b4f86,#122444 60%,#0b1427)" }}>
    <div className="orb-menu-stage" style={{ height: h }}>{body}{orb(state)}</div></div>;
  const noop = () => undefined;
  return <div style={{ display: "flex", gap: 32, alignItems: "flex-end" }}>
    {frame(248, 336, <OrbMenu headline="Placements Job Watch is working" tone="busy" running={5} missed={2} pending={1} needs={[{ id: "d", name: "Battery Watch", reason: "Allow device metrics in Edit to monitor this computer." }]} onNeed={noop} muted={false} confirmQuit={false} onOpen={noop} onAgents={noop} onMissed={noop} onAttention={noop} onMute={noop} onUnmute={noop} onAskQuit={noop} onQuit={noop} />, "w-menu", "working")}
    <div className="w-idle" style={{ width: 60, height: 60, display: "grid", placeItems: "center", background: "#1b3b6f" }}><button className="status-orb" data-state="working"><b style={{ color: "#f4f2ed" }}>M</b><span className="orb-dot" /></button></div>
    {frame(320, 400, <CrewQuickList initial={crews} onBack={noop} onOpen={noop} />, "w-agents")}
    {frame(320, 400, <MissedList items={missed} busy={false} error={null} onBack={noop} onClear={noop} onOpen={noop} />, "w-missed")}
    {frame(248, 336, <OrbMenu headline="Muted · 14:02" tone="warn" running={5} missed={0} pending={0} onNeed={noop} muted confirmQuit onOpen={noop} onAgents={noop} onMissed={noop} onAttention={noop} onMute={noop} onUnmute={noop} onAskQuit={noop} onQuit={noop} />, "w-muted")}
  </div>;
}

/** What happened, inside the settings sheet, with a few real-looking events. */
function Happened() {
  const at = (min: number) => new Date(Date.now() - min * 60000).toISOString();
  const act = (o: Record<string, unknown>) => ({ id: Math.random().toString(36).slice(2), crew_id: "jobs", crew_name: "Placements Job Watch", source: "crew", operation: "browser.read", arguments: {}, status: "completed", result: null, error: null, finished_at: null, ...o });
  const list = happenings([
    act({ operation: "browser.open", arguments: { url: "https://jobs.example.com/listings" }, created_at: at(40) }),
    act({ operation: "browser.read", status: "failed", error: "Open a page in Mavo's browser first", created_at: at(41) }),
    act({ operation: "browser.click", status: "failed", error: "Open a page in Mavo's browser first", created_at: at(42) }),
    act({ crew_id: "inbox", crew_name: "Inbox Summary", operation: "gmail.archive", created_at: at(95) }),
    act({ crew_id: "clean", crew_name: "Temp & Drive Cleanup", operation: "files.backup", arguments: { folder: "~/Projects/site" }, created_at: at(160) }),
  ] as never, [{ id: "r1", crew_id: "news", crew_name: "News Watch", status: "failed", error: "The model took too long to answer.", result: null, created_at: at(1500) } as never],
  [{ id: "m1", crew_id: "inbox", crew_name: "Inbox Summary", message: "3 important mails: placement drive Friday, a bill due, a reply from your professor.", scheduled_for: at(60) } as never]);
  React.useLayoutEffect(() => { useAppStore.setState({ crew: [{ id: "jobs" }, { id: "inbox" }, { id: "clean" }, { id: "news" }] as never }); }, []);
  return <div className="happened-preview workspace-sheet" style={{ width: 1000, height: 820 }}><div className="workspace-body">
    <PageHeader eyebrow="Agents" title="What happened" description="What your agents have done." />
    <div className="page-content"><div className="narrow"><ActivityView initial={list} /></div></div></div></div>;
}

/** The Chat tab: your agents talking with you. */
function InboxView() {
  const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
  React.useLayoutEffect(() => {
    useAppStore.setState({ crew: [
      { id: "inbox", name: "Inbox Summary", template_id: "communication", status: "active", configuration_plan: {}, permissions: [] },
      { id: "quotes", name: "Motivational Quotes", template_id: "wellbeing", status: "active", configuration_plan: {}, permissions: [], max_model_calls: 1, daily_call_limit: 48,
        question: "This follow-up needs a remaining session model-call budget. Adjust this Crew's access settings or tell it another way to proceed." },
      { id: "lead", name: "Channel Lead", template_id: "operator", status: "active", configuration_plan: { leads_team: true, members: [] }, permissions: [],
        question: "Today's video is ready: publish it as public, or keep it unlisted for a day?",
        held: { kind: "ask", from: "Channel Lead", at: ago(5), result: "Script tightened once; thumbnail done." } },
    ] as never });
  }, []);
  const updates = [
    { id: "1", crew_id: "inbox", crew_name: "Inbox Summary", kind: "crew_update", scheduled_for: ago(240), message: "3 important mails today: the placement drive moved to Friday, your electricity bill is due on the 12th, and Prof. Rao replied about the project." },
    { id: "2", crew_id: "lead", crew_name: "Channel Lead", kind: "crew_update", scheduled_for: ago(30), message: "Today's topic: laptop battery myths. Script is written and checked." },
    { id: "3", crew_id: "build-1", crew_name: "Build: RSS Feed Reader", kind: "crew_update", scheduled_for: ago(26), message: "Built in ~/Documents/Mavo Projects/rss-feed-reader. Open index.html in your browser to use it. TEST: problems found. The page shows content. Typing a feed address and pressing Enter or Add did nothing on the first try. Buttons respond once. The search box filters titles but not descriptions. Everything else works: feeds load, the list scrolls, and settings are saved after a reload." },
    { id: "4", crew_id: "lead", crew_name: "Channel Lead", kind: "crew_update", scheduled_for: ago(24), message: "Thumbnail is done too." },
  ];
  const asks = { inbox: { name: "Inbox Summary", items: [
    { id: "q1", question: "What did Prof. Rao say?", answer: "He approved the topic and wants a draft by next Wednesday.", pending: false, at: ago(200) },
  ] } };
  return <div className="inbox-preview chat-dock" style={{ width: 420, height: 860, display: "flex" }}><div className="chat-dock-inner"><AgentInbox initial={{ updates, asks } as never} /></div></div>;
}

function Swarm({ n }: { n: number }) {
  const names = ["News Watch", "Inbox Summary", "Placements Job Watch", "Battery Watch", "Temp Cleaner", "Study Sync", "Price Tracker", "Weather Brief", "Repo Watch", "Paper Digest"];
  const kinds = ["research", "communication", "operator", "computer", "files", "learning", "travel", "coding"];
  const states = ["waiting", "waiting", "waiting", "working", "needs_input"];
  const crews = Array.from({ length: n }, (_, i) => ({
    id: `agent-${i}-${(i * 2654435761 >>> 0).toString(36)}`, name: `${names[i % names.length]} ${i + 1}`, template_id: kinds[i % kinds.length],
    status: "active", is_running: i % 13 !== 5, runtime_state: states[i % states.length], trigger_type: "interval",
    next_run_at: null, configuration_plan: i === 2 ? { upstream: ["agent-1-" + (2654435761 >>> 0).toString(36)] } : {},
  })) as unknown as CrewInstance[];
  return <div className={"swarm-preview swarm-" + n} style={{ position: "relative", width: 1920, height: 1032, overflow: "hidden", background: "linear-gradient(135deg,#2c5bd0,#0f2a78)" }}>
    <CrewFan initial={crews} width={1920} height={1032} core={{ x: 1862, y: 974 }} onList={() => undefined} onOpen={() => undefined} onClose={() => undefined} />
    <div style={{ position: "absolute", left: 1842, top: 954, width: 40, height: 40, borderRadius: "50%", background: "#161615", display: "grid", placeItems: "center", color: "#f4f2ed", fontWeight: 700, zIndex: 3 }}>M</div>
  </div>;
}

function Mentions() {
  const names = ["Lunch Reminder", "Wind Down", "Inbox Summary"];
  const typed = "Create a new agent called Wind Down: whenever my @Lunch Reminder works, put my laptop to sleep and tell @Inbox Summary";
  return <div className="mentions-preview" style={{ width: 760, display: "grid", gap: 24 }}>
    <div className="flex justify-end"><div className="message-content message-user max-w-[80%] whitespace-pre-wrap text-[14px] leading-6"><MentionText text={typed} names={names} /></div></div>
    <div className="composer flex items-end gap-3 p-2.5">
      <div className="mention-field relative min-w-0 flex-1"><div aria-hidden="true" className="mention-mirror px-2 py-2 text-sm"><MentionText text={typed + "\n"} names={names} /></div>
        <textarea readOnly rows={2} value={typed} className="mention-input relative block min-h-10 w-full resize-none bg-transparent px-2 py-2 text-sm outline-none" /></div>
    </div>
  </div>;
}

function HomeView() {
  const soon = new Date(Date.now() + 14 * 60000).toISOString();
  const at = (min: number) => new Date(Date.now() + min * 60000).toISOString();
  const crews = [
    crew("communication", "Inbox Summary", { id: "inbox", responsibilities: "Summarise new important mail every morning and flag anything urgent.", trigger_type: "daily", schedule_time: "08:00", next_run_at: at(300), last_run_at: at(-1140), permissions: ["google:gmail:read", "google:gmail:body", "notifications:show"] }),
    crew("communication", "Urgent Mail Alert", { id: "urgent", responsibilities: "Alert me the moment an urgent mail lands.", trigger_type: "gmail_message", permissions: ["google:gmail:read", "notifications:show"] }),
    crew("schedule", "Lunch Reminder", { id: "lunch", responsibilities: "Remind me to take lunch at 1 pm.", trigger_type: "daily", schedule_time: "13:00", next_run_at: soon, permissions: ["google:calendar:read"] }),
    crew("wellbeing", "Wind Down", { id: "wind", responsibilities: "When lunch is called, put the laptop to sleep.", trigger_type: "manual", permissions: ["action:windows.sleep"], configuration_plan: { upstream: ["lunch"], draft: { requested_actions: [{ operation: "windows.sleep" }] } } as never }),
    crew("research", "News Watch", { id: "news", responsibilities: "Tell me when something big happens in tech.", trigger_type: "interval", review_interval_minutes: 30, next_run_at: at(11), last_run_at: at(-19), permissions: ["feeds:read", "notifications:show"] }),
    crew("research", "Podcast Research", { id: "pod", responsibilities: "Find guests and topics for this week's episode from news feeds.", runtime_state: "working", permissions: ["feeds:read", "google:calendar:read"], configuration_plan: { goal: "Five guest ideas", run_until: "2026-11-30" } as never }),
    crew("files", "Episode Notes", { id: "notes", responsibilities: "Turn the research into show notes and draft the guest invite.", trigger_type: "manual", permissions: ["google:drive:read", "action:gmail.draft"], configuration_plan: { upstream: ["pod"], review: true, max_rounds: 3, draft: { requested_actions: [{ operation: "gmail.draft" }, { operation: "gmail.send" }] } } as never }),
    crew("operator", "Channel Lead", { id: "lead", responsibilities: "Run my YouTube channel: one good short video every day.", trigger_type: "daily", schedule_time: "10:00",
      runtime_state: "needs_input", question: "Today's video is ready: publish it as public, or keep it unlisted for a day?",
      configuration_plan: { leads_team: true, members: ["trend", "script"] } as never,
      held: { kind: "ask", from: "Channel Lead", at: at(-5), result: "Trend Researcher found 'laptop battery myths'. Script Writer made a 58-second script; I sent it back once to tighten the hook. It is good now." } } as never),
    crew("research", "Trend Researcher", { id: "trend", responsibilities: "Find what my audience is searching for this week.", trigger_type: "manual", configuration_plan: { lead: "lead" } as never, permissions: ["google:youtube:read"] }),
    crew("files", "Script Writer", { id: "script", responsibilities: "Write short, punchy video scripts.", trigger_type: "manual", configuration_plan: { lead: "lead" } as never, permissions: ["action:notes.write"] }),
    crew("operator", "YouTube Uploader", { id: "upl", responsibilities: "Upload the reviewed episode to YouTube.", trigger_type: "manual", runtime_state: "needs_input",
      question: "Episode Notes handed over work. Approve it for YouTube Uploader to start on, or send it back with a note.",
      configuration_plan: { upstream: ["notes"], approve_first: true } as never,
      held: { kind: "approve", from: "Episode Notes", at: at(-12), result: "Episode 12: three guests on local-first software. Title, description and chapters are ready.\n\nReviewed by Episode Notes: tight, on-topic, chapters match.", files: ["~/Videos/episode-12/final.mp4", "~/Videos/episode-12/thumbnail.png"] } } as never),
    crew("operator", "AIRouter Credits", { id: "credits", responsibilities: "Check my AIRouter balance daily and tell me when it is low.", trigger_type: "daily", schedule_time: "09:00", next_run_at: at(560), last_run_at: at(-880), permissions: ["notifications:show"], configuration_plan: { draft: { requested_actions: [{ operation: "browser.open", domains: ["airouter.io"] }] } } as never }),
    crew("computer", "PC Health", { id: "pc", responsibilities: "Watch battery drain and free up space when the disk is nearly full.", runtime_state: "needs_input", permissions: ["device:read", "device:clean_temp", "notifications:show"] }),
  ];
  const params = new URLSearchParams(location.search);
  // ?many=300: a big team, most of them using desktop alerts, some Gmail, a few a site of their own.
  const many = Number(params.get("many") || 0);
  if (many) {
    const kinds = ["communication", "research", "operator", "computer", "files", "schedule", "wellbeing"];
    crews.splice(0, crews.length, ...Array.from({ length: many }, (_, i) => crew(kinds[i % kinds.length], `Agent ${String(i + 1).padStart(3, "0")}`, {
      id: "m" + i, trigger_type: i % 5 ? "interval" : "gmail_message", next_run_at: at(5 + (i * 7) % 55), last_run_at: at(-25),
      permissions: [...(i % 4 ? ["notifications:show"] : []), ...(i % 6 === 0 ? ["google:gmail:read"] : []), ...(i % 9 === 0 ? ["action:windows.sleep"] : [])],
      configuration_plan: (i % 23 === 0 ? { draft: { requested_actions: [{ operation: "browser.open", domains: [`site${i}.example.com`] }] } } : {}) as never,
    })));
  }
  React.useLayoutEffect(() => {
    // Two of Mavo's models: the cloud one, and Ollama on this PC for the light agents.
    for (const c of crews) Object.assign(c, ["inbox", "lunch", "news", "urgent"].includes(c.id) ? { route_id: "local", provider: "ollama", model: "qwen2.5:3b" } : { route_id: c.route_id ?? "r" });
    Object.assign(crews.find(c => c.id === "inbox") ?? {}, { fallback: { from: "Ollama (qwen2.5:3b)", to: "AIRouter default (deepseek-v4-flash)", why: "The model's reply was not in the expected shape", at: at(-60) } });
    useAppStore.setState({ routes: [{ id: "r", name: "AIRouter default", provider: "airouter", model: "deepseek-v4-flash" }, { id: "local", name: "Ollama", provider: "ollama", model: "qwen2.5:3b" }] as never });
    useAppStore.setState({ crew: crews, setupDrafts: many ? [] : [crew("travel", "Trip Planner", { id: "trip", status: "draft", responsibilities: "Plan my Goa trip.", configuration_plan: { draft: { requested_actions: [{ operation: "browser.open", domains: ["irctc.co.in"] }] } } as never })],
      chatOpen: params.get("chat") !== "0", page: "home" });
  }, []);
  if (params.get("bare") === "1") return <div className="map-preview" style={{ width: 1200, height: 760, position: "relative" }}><AgentGraph crews={crews} drafts={[]} me="Himanshu" memory={[{ id: "1", text: "My mom is Sita", source: "Lunch Reminder", at: "" }]} selection={null} onSelect={() => undefined} onNew={() => undefined} /></div>;
  const step = (title: string, status: string, team?: string[]) => ({ id: title, title, kind: "helper", status, team });
  const tasks = [{
    id: "t1", title: "Build: Combined RSS Reader", status: "running", result: null, error: null, used_calls: 5, budget_calls: 17, conversation_id: null,
    created_at: at(-6), finished_at: null, steps: [
      step("Lead picked the team", "done", ["React developer", "Documentation writer", "Accessibility & UX reviewer", "Integration finisher", "Tester"]),
      step("React developer writes it", "done"), step("Documentation writer works on it", "done"), step("Tester tries it in a browser", "done"),
      step("Accessibility & UX reviewer checks it", "done"), step("Integration finisher fixes what was found", "running"),
    ],
  }] as never;
  return <div className="map-preview" style={{ width: 1600, height: 940, position: "relative", overflow: "hidden" }}><Home previewTasks={params.get("tasks") === "0" ? undefined : tasks} /></div>;
}

function Preview() {
  const show = (name: string) => (view === "all" && !["setup", "blender", "actions", "tasks"].includes(name)) || view === name;
  return <div className="app-shell" style={{ padding: 40, minHeight: "100vh", display: "grid", gap: 40, alignContent: "start" }}>
    {show("cards") && <Cards />}
    <div style={{ display: "flex", gap: 40, flexWrap: "wrap", alignItems: "flex-start" }}>
      {show("drawer") && <Drawer />}
      {show("desktop") && <Desktop />}
      {show("controls") && <Controls />}
      {show("setup") && <Setup />}
      {show("blender") && <Blender />}
      {show("actions") && <ActionsView />}
      {show("tasks") && <TaskCards />}
      {show("math") && <MathCard />}
      {show("build") && <BuildCards />}
      {show("missed") && <Missed />}
      {show("quick") && <QuickCrew />}
      {view === "widget" && <Widget />}
      {view === "happened" && <Happened />}
      {view === "inbox" && <InboxView />}
      {show("swarm") && <><Swarm n={100} /><Swarm n={6} /></>}
      {show("mentions") && <Mentions />}
      {show("home") && <HomeView />}
    </div>
  </div>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><Preview /></React.StrictMode>);
