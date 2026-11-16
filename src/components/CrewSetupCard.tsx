import { TypedText } from "./TypedText";
import { ArrowUpRight, Bell, Check, Clock, Globe, Keyboard, LoaderCircle, Send } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { CrewMembers } from './CrewMembers';
import { firstRunLabel, firstRunLine, firstRunResult, setupBlockers } from '../lib/firstRun';
import {CrewChangePreview} from './CrewChangePreview';
import { Understood } from './Understood';
import { Report } from './Report';
import { WorkflowFlow, type FlowStep } from './WorkflowFlow';
import { AgentBot } from './AgentBot';
import { agentStyle } from './CrewFan';
import { DeskSettingsLine, type DeskParams } from './DeskSettingsLine';
import {FirstRunReads} from './FirstRunReads';
import type { TelegramStatus } from "../types";
import type { CrewInstance, GoogleCapability } from "../types";
import { api, ApiError } from "../lib/api";
import { useGoogleConnection } from "../lib/useGoogleConnection";
import { RUN_TOOLS } from "../lib/actionRuleInput";
import { useAppStore } from "../store/app";
import { ActionAccessEditor, emptyRule, type ActionRule } from "./ActionAccessEditor";
import { PermissionPicker, permissionLabels } from "./PermissionPicker";
import { titles as actionTitles } from "./GoogleActionsPanel";
import { DeleteCrewDialog } from "./DeleteCrewDialog";
import { SignedInLine } from "./SignedInLine";
import { ConnectorPermissions } from "./ConnectorAccounts";
import { SaveLoginForm } from "./SaveLogin";
import { SignInLink } from "./SiteLine";
import { endLine, triggerLine } from "./CrewCard";

// Reading and finding things is allowed with one switch; doing things (click, type) is its own switch.
const SAFE_OPERATIONS = ["browser.open", "browser.read", "browser.search", "browser.navigate", "browser.scroll", "browser.wait"];
const ACTING_OPERATIONS = ["browser.click", "browser.double_click", "browser.drag", "browser.hover", "browser.fill", "browser.upload", "browser.select", "browser.press"];
// Making files and running scripts in a project folder. Allowing it is one switch because every
// script run still waits for the person's own OK (and the files stay inside the project).
const PROJECT_OPERATIONS = ["project.create", "project.write", "project.read", "project.run"];
const ACTION_FIELDS = ["domains", "folders", "apps", "recipients", "repositories"] as const;

type Requested = { operation: string; domains?: string[]; folders?: string[]; apps?: string[]; recipients?: string[]; repositories?: string[] };

const unique = (values: string[]) => [...new Set(values)];
// Running a script with limits needs its tools named; when the chat did not, offer the two that exist.
const appsOf = (a: Requested) => a.apps?.length ? a.apps : a.operation === "project.run" ? RUN_TOOLS : [];

function sitesOf(text: string, actions: Requested[]): string[] {
  const fromActions = actions.filter(a => a.operation.startsWith("browser.")).flatMap(a => a.domains ?? []);
  const url = text.match(/https?:\/\/([a-z0-9.-]+\.[a-z]{2,})/i)?.[1];
  const all = unique([...fromActions, ...(url ? [url] : [])]).filter(d => d !== "html.duckduckgo.com");
  // placements.example.com and example.com are one site to the person: keep the specific one.
  return all.filter(d => !all.some(other => other !== d && other.endsWith("." + d))).slice(0, 3);
}

function siteGuess(text: string, actions: Requested[]): string {
  return sitesOf(text, actions)[0] ?? "";
}

function describeSafe(actions: Requested[]): string {
  const sites = unique(actions.flatMap(a => a.domains ?? []));
  const searches = actions.some(a => a.operation === "browser.search");
  return (searches ? "Search the web" : "Open and read") + (sites.length ? " " + sites.join(", ") : "");
}

export function Switch({ on, onChange, disabled }: { on: boolean; onChange: (value: boolean) => void; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={on} disabled={disabled} className="resource-switch" onClick={() => onChange(!on)}><span /></button>;
}

export function Row({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <div className="list-row"><div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">{title}</p>{hint && <p className="mt-0.5 text-[12px] text-zinc-500">{hint}</p>}</div>{children}</div>;
}

const celebrated = new Set<string>();

// A test seen running, then passed: celebrated once, for a few seconds. Kept outside the card
// because the card is rebuilt whenever its plan changes (and would forget it had seen it run).
const lastStatus = new Map<string, string>();
const appeared = new Set<string>();
const celebrateUntil = new Map<string, number>();
const CELEBRATE_MS = 6500;

/** The moment it works: a tick drawn in a ring, a soft burst of dots once, its bot hopping, and
 *  one line saying it is on. Plays once; still for reduced motion. */
export function Celebration({ seed, name, passed }: { seed: string; name: string; passed: boolean }) {
  return <div className="celebrate" role="status">
    <div className="celebrate-art" aria-hidden="true">
      {Array.from({ length: 10 }, (_, i) => <i key={i} className="celebrate-dot" style={{ ["--a" as string]: `${i * 36}deg`, ["--d" as string]: `${(i % 3) * 60}ms` }} />)}
      <svg className="celebrate-check" width="44" height="44" viewBox="0 0 44 44" fill="none">
        <circle className="celebrate-ring" cx="22" cy="22" r="19" />
        <path className="celebrate-tick" d="M13.5 22.5l6 6 11-12" />
      </svg>
      <span className="celebrate-bot"><AgentBot seed={seed} state="ready" /></span>
    </div>
    <div className="celebrate-words">
      <b>{passed ? "It works" : "It's on"}</b>
      <span>{`${name} works on its own from now. You'll hear from it when something matters.`}</span>
    </div>
  </div>;
}

const WRITES = /:(send|modify|write)$/;

/** The Google access an action needs: what allowing it grants. */
function capabilityOf(operation: string): string | null {
  const [service, verb] = operation.split('.');
  if (service === 'gmail') return verb === 'send' ? 'gmail:send' : 'gmail:modify';
  if (['calendar', 'tasks', 'drive', 'contacts'].includes(service)) return `${service}:write`;
  return null;
}

/** Settings shown in full, or folded under one line when they are one easy step. */
function FoldIf({ fold, label, children }: { fold: boolean; label: string; children: ReactNode }) {
  return fold ? <details className="setup-more"><summary>{label}</summary><div className="mt-3 space-y-4">{children}</div></details> : <>{children}</>;
}

type TestLevel = 'quick' | 'standard' | 'thorough';
const TEST_LEVELS: [TestLevel, string][] = [['quick', 'Quick'], ['standard', 'Standard'], ['thorough', 'Thorough']];
const TEST_HINTS: Record<TestLevel, string> = {
  quick: '4 test emails, 1 fix round. Fastest and cheapest.',
  standard: '8 test emails, up to 2 fix rounds.',
  thorough: 'Every test email, up to 3 fix rounds. Most confidence, most cost.',
};

const SERVICE_NAMES: Record<string, string> = { gmail: "Gmail", calendar: "Calendar", tasks: "Tasks", drive: "Drive", contacts: "Contacts", youtube: "YouTube" };

/** The Google reads a draft asks for (its capabilities, and each resource's read). */
function draftReads(draft: { requested_google_capabilities: string[]; requested_google_resources: string[] }): GoogleCapability[] {
  return unique([...draft.requested_google_capabilities, ...draft.requested_google_resources.map(r => `${r}:read`)]).filter(c => !WRITES.test(c)) as GoogleCapability[];
}

/** "on airouter.in", "to ravi@…": the limits an action was asked with, in a few words. */
function limitsOf(a: { domains?: string[]; folders?: string[]; recipients?: string[] }): string {
  const parts = [a.domains?.length ? `on ${a.domains.join(", ")}` : "", a.recipients?.length ? `to ${a.recipients.join(", ")}` : "", a.folders?.length ? `in ${a.folders.join(", ")}` : ""].filter(Boolean);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

/** Everything a new agent will be allowed, in full, before it is added: what Mavo decided it
 *  needs. Switched off below, an item shows as off. */
/** While it is tested: what Mavo is doing, and why an agent is tested at all. Lines arrive one
 *  after another, typed, over a quiet moving scan. */
export function TestingNote({ sample }: { sample: boolean }) {
  const lines = [
    sample
      ? "Mavo wrote a few made-up emails for this, so nothing real is read, sent or changed."
      : "It works on your real data, but nothing is sent or changed while it is tested.",
    "An agent is software: it cannot be held accountable for a mistake. So Mavo watches what it does on these before it does anything for you.",
    "If it gets something wrong, you see it here first, and nothing happens.",
  ];
  const [shown, setShown] = useState(1);
  useEffect(() => {
    if (shown >= lines.length) return;
    const t = window.setTimeout(() => setShown(n => n + 1), 1800);
    return () => window.clearTimeout(t);
  }, [shown, lines.length]);
  return <div className="testing-note" role="status" aria-live="polite">
    <div className="testing-head"><span className="testing-scan" aria-hidden="true"><i /></span><b>Testing how it behaves</b></div>
    {lines.slice(0, shown).map((line, n) => <p key={n}><TypedText text={line} animate /></p>)}
  </div>;
}

export function NeedsList({ items }: { items: { key: string; text: string; on: boolean }[] }) {
  if (!items.length) return null;
  return <div className="needs-list"><p className="needs-list-head">It will be allowed to</p>
    <ul>{items.map((i, n) => <li key={i.key} data-off={!i.on || undefined} style={{ ["--i" as string]: n } as CSSProperties}><Check size={13} aria-hidden="true" /><span>{i.text}</span>{!i.on && <em>off</em>}</li>)}</ul></div>;
}

/** "Send to Telegram": this agent's alerts on the person's phone too. Saved at once. Needs its
 *  alerts on; offers setup when Telegram is not linked yet. */
export function TelegramRow({ crew, alerts, initial }: { crew: CrewInstance; alerts: boolean; initial?: TelegramStatus | null }) {
  const [status, setStatus] = useState<TelegramStatus | null | undefined>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (status === undefined) void api.telegram().then(setStatus).catch(() => setStatus(null)); }, [status]);
  const saved = !!crew.configuration_plan?.draft?.requested_telegram;
  // It moves when pressed; it goes back only if saving fails, saying why.
  const [shown, setShown] = useState<boolean | null>(null);
  const on = shown ?? saved;
  useEffect(() => { setShown(null); }, [saved]);
  async function change(next: boolean) {
    setBusy(true); setError(null); setShown(next);
    try { useAppStore.getState().upsertCrew(await api.crewTelegram(crew.id, next)); }
    catch (e) {
      setShown(null);
      const why = e instanceof Error ? e.message : "";
      // "Not Found" is an older Mavo running behind this screen; "Crew not found", a removed agent.
      setError(why === "Not Found" ? "Restart Mavo to use this switch." : /crew not found/i.test(why) ? "This agent was removed." : why || "Could not change it.");
    }
    finally { setBusy(false); }
  }
  const linked = !!status?.linked;
  const hint = !alerts ? "Turn on desktop alerts first: Telegram gets the same ones."
    : !linked ? "Your Telegram is not linked yet."
    : status?.alerts ? "All your alerts already go there; this keeps it on if you change that."
    : "Its alerts go to your Telegram too.";
  return <Row title="Send to Telegram" hint={hint}>
    <div className="flex items-center gap-2">
      {!linked && status !== undefined && <button type="button" className="btn-quiet !py-1" onClick={() => useAppStore.getState().setPage("resources")}>Set up</button>}
      <Switch on={on && alerts} onChange={v => void change(v)} disabled={busy || !alerts} />
    </div>
    {error && <p className="na-error" role="alert">{error}</p>}
  </Row>;
}

export function CrewSetupCard({ crew, onRefine }: { crew: CrewInstance; onRefine?: (id: string) => void }) {
  const plan = crew.configuration_plan?.draft ? crew.configuration_plan : null;
  const awaitingAnswers = !!plan?.questions?.length || plan?.phase === "needs_input";
  const routes = useAppStore(s => s.routes);
  const draftMode = crew.status === "draft";

  // Mavo decided what a new agent needs: all of it starts on, shown in full, for the person to
  // allow with one press (an inbox agent was added without its Gmail access, because those boxes
  // started empty under "Change what it can do", and sat at "needs setup").
  const grantedReads = crew.permissions.filter(p => p.startsWith("google:")).map(p => p.slice(7)) as GoogleCapability[];
  const askedReads = draftMode && plan ? draftReads(plan.draft) : [];
  const [caps, setCaps] = useState<GoogleCapability[]>(() => unique([...grantedReads, ...askedReads]) as GoogleCapability[]);
  const [connectorCaps, setConnectorCaps] = useState<string[]>(() => draftMode && plan?.draft.requested_connector_capabilities?.length
    ? unique([...crew.permissions.filter(p => p.startsWith("github:") || p.startsWith("feeds:")), ...plan.draft.requested_connector_capabilities])
    : crew.permissions.filter(p => p.startsWith("github:") || p.startsWith("feeds:")));
  const [actions, setActions] = useState<Record<string, ActionRule>>({});
  const [savedOperations, setSavedOperations] = useState<string[]>([]);
  // What the draft asks for is what the person asked for in chat, so it starts on.
  const [notifications, setNotifications] = useState(draftMode ? !!plan?.draft.requested_notifications : crew.permissions.includes("notifications:show"));
  const [device, setDevice] = useState(draftMode ? !!plan?.draft.requested_device_metrics : crew.permissions.includes("device:read"));
  const [weather, setWeather] = useState(draftMode ? !!plan?.draft.requested_weather : crew.permissions.includes("weather:read"));
  // Freeing space deletes files: its own switch, saying exactly what goes. On for a draft, since
  // the person asked for it in so many words; they see it before adding.
  const cleanup = plan?.draft.device_monitor?.clean_temp_days ? plan.draft.device_monitor : null;
  const [clean, setClean] = useState(draftMode ? !!cleanup : crew.permissions.includes("device:clean_temp"));
  // Each agent names its own model; the crew's main one is picked for it when saved.
  const route = crew.route_id ?? "";
  const touched = useRef(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(plan?.phase !== "ready");
  const [loading, setLoading] = useState(!!plan?.draft.requested_actions.length);
  const [policyError,setPolicyError]=useState(false);
  const [policyRetry,setPolicyRetry]=useState(0);
  const [error, setError] = useState<string | null>(null);
  const google = useGoogleConnection(!awaitingAnswers);
  const fresh = celebrated.has(crew.id) && crew.status !== "draft";
  // Its parts rise in one after another the first time it appears, not on every update after.
  const [entering] = useState(() => { const first = !appeared.has(crew.id); appeared.add(crew.id); return first; });
  // Seen running, now passed: the moment to celebrate.
  const status = (plan as { first_run?: { status?: string } } | undefined)?.first_run?.status ?? "";
  if (lastStatus.get(crew.id) === "running" && status === "passed") celebrateUntil.set(crew.id, Date.now() + CELEBRATE_MS);
  lastStatus.set(crew.id, status);
  const [, redraw] = useState(0);
  const celebrating = (celebrateUntil.get(crew.id) ?? 0) > Date.now();
  useEffect(() => { if (!celebrating) return; const t = window.setTimeout(() => redraw(n => n + 1), (celebrateUntil.get(crew.id) ?? 0) - Date.now() + 50); return () => window.clearTimeout(t); }, [celebrating, crew.id]);

  useEffect(() => { if (!fresh) return; const t = window.setTimeout(() => celebrated.delete(crew.id), 4500); return () => window.clearTimeout(t); }, [fresh, crew.id]);

  useEffect(() => {
    if (awaitingAnswers || !plan?.draft.requested_actions.length) return;
    let active = true;
    void api.actionPolicies(crew.id, true).then(v => {
      if (!active) return;
      const relevant = v.actions.filter(a => plan.draft.requested_actions.some(r => r.operation === a.operation));
      setSavedOperations(relevant.filter(a => a.rule.enabled).map(a => a.operation));
      setActions(Object.fromEntries(relevant.map(a => {
        const requested = plan.draft.requested_actions.find(r => r.operation === a.operation)!;
        const rule = { ...a.rule };
        if (!rule.enabled) for (const field of ACTION_FIELDS) if (!rule[field]?.length) rule[field] = requested[field] ?? [];
        return [a.operation, rule];
      })));
      setLoading(false);
      setPolicyError(false);
    }).catch(e => { if (active) {setLoading(false);setPolicyError(true);setError(e instanceof Error?e.message:'Could not load action access.');} });
    return () => { active = false; };
  }, [crew.id, awaitingAnswers,policyRetry]);

  // A draft refined in chat may now ask to read more: still untouched, it follows.
  const askedKey = askedReads.join("|");
  useEffect(() => {
    if (!draftMode || touched.current || !askedKey) return;
    setCaps(prev => unique([...prev, ...askedKey.split("|")]) as GoogleCapability[]);
  }, [askedKey, draftMode]);

  // Every action Mavo decided a new draft needs starts allowed, within the limits it asked for
  // (its sites, folders, recipients); each is listed on the card, and can be switched off.
  useEffect(() => {
    if (loading || !plan || !draftMode) return;
    setActions(prev => {
      const next = { ...prev };
      let changed = false;
      for (const a of plan.draft.requested_actions) {
        if (!next[a.operation]?.enabled && !savedOperations.includes(a.operation) && !touched.current) {
          const limited = [a.domains, a.folders, appsOf(a), a.recipients].some(v => v?.length);
          next[a.operation] = { ...emptyRule(), enabled: true, mode: limited ? "restricted" : "unrestricted", domains: a.domains ?? [], folders: a.folders ?? [], apps: appsOf(a), recipients: a.recipients ?? [] };
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [loading]);

  // A draft that sends, books or writes gets its whole path written as soon as it is shown:
  // the person sees every case it handles before testing it.
  const paths = (plan ?? {}) as { recipe?: { steps?: FlowStep[]; final?: string }; workflow_draft?: { steps?: FlowStep[]; final?: string }; workflow_writing?: string; workflow_problem?: string; changes_things?: boolean };
  const wantsPath = draftMode && !!plan && !plan.questions?.length && !!paths.changes_things && !paths.recipe && !paths.workflow_draft && !paths.workflow_writing && !paths.workflow_problem;
  const asked = useRef<string | null>(null);
  // How thoroughly its path is tested: the person's choice of time and cost against confidence.
  const [level, setLevel] = useState<TestLevel>(((plan as { test_level?: TestLevel } | undefined)?.test_level) ?? 'standard');
  useEffect(() => {
    if (!wantsPath || asked.current === crew.id) return;
    asked.current = crew.id;
    api.draftWorkflow(crew.id).then(item => useAppStore.getState().upsertCrew(item)).catch(() => undefined);
  }, [wantsPath, crew.id]);

  if (!plan) return null;
  const draft = plan.draft;
  const firstRun = firstRunResult(plan);
  const requestedActions = draft.requested_actions as Requested[];
  const safeOps = requestedActions.map(a => a.operation).filter(op => SAFE_OPERATIONS.includes(op));
  const actingOps = requestedActions.map(a => a.operation).filter(op => ACTING_OPERATIONS.includes(op));
  const projectOps = requestedActions.map(a => a.operation).filter(op => PROJECT_OPERATIONS.includes(op));
  const sites = awaitingAnswers ? [] : sitesOf(crew.responsibilities, requestedActions);
  const requested = unique([...draft.requested_google_capabilities, ...draft.requested_google_resources.map(r => `${r}:read`)]) as GoogleCapability[];
  // Reading is chosen here; writing comes with the action allowed below (Send email brings
  // Gmail's send access): one choice, never the same thing asked twice.
  const readsAsked = requested.filter(c => !WRITES.test(c));
  const writesGiven = requested.filter(c => WRITES.test(c) && requestedActions.some(a => actions[a.operation]?.enabled && capabilityOf(a.operation) === c));
  const granted = unique([...caps.filter(c => !WRITES.test(c)), ...writesGiven]) as GoogleCapability[];
  const missing = granted.filter(c => !google.status?.capabilities?.includes(c));
  const usesBrowser = requestedActions.some(a => a.operation.startsWith("browser."));
  const directActions=requestedActions.filter(a=>![...safeOps,...actingOps,...projectOps].includes(a.operation));
  const blockers=setupBlockers(plan.missing).filter(item=>!granted.some(cap=>item===`Allow ${cap}`)&&!connectorCaps.some(cap=>item===`Allow ${cap}`)&&!requestedActions.some(action=>item===`Allow ${action.operation}`&&actions[action.operation]?.enabled)&&!(item==='Allow desktop updates'&&notifications));
  const samePermissions=!touched.current&&notifications===crew.permissions.includes('notifications:show')&&device===crew.permissions.includes('device:read')&&weather===crew.permissions.includes('weather:read')&&clean===crew.permissions.includes('device:clean_temp')&&[...connectorCaps].sort().join('|')===crew.permissions.filter(p=>p.startsWith('github:')||p.startsWith('feeds:')).sort().join('|');
  // The test can be skipped. One that changes things then asks before its first few changes.
  const testing = firstRun?.status === 'running';
  const blocked = testing;
  // The cases a tested path may get wrong, by name: the person decides knowing them.
  const weak = (firstRun?.tests ?? []).filter(t => !t.passed).map(t => t.name);
  // A draft that sends or books and whose plan Core writes: turned on directly, tested by choice.
  // What turning it on grants, in one line: the details fold away (granting is one step).
  const services = unique(requested.map(c => String(c).split(':')[0]));
  const READ_NAMES: Record<string, string> = { gmail: 'your Gmail', calendar: 'your Calendar', tasks: 'your Tasks', drive: 'your Drive', contacts: 'your contacts' };
  const doing = unique(requestedActions.map(a => (actionTitles[a.operation] ?? a.operation.replace('.', ' ')).toLowerCase()));
  const grantLine = `It will ${services.length ? `read ${services.map(s => READ_NAMES[s] ?? s).join(' and ')}` : 'work'}${doing.length ? `, and ${doing.join(', ')}` : ''}.`;
  const direct = draftMode && !!paths.changes_things && !awaitingAnswers && (!!paths.workflow_draft || !!paths.workflow_writing || !!paths.recipe);
  const grant = draftMode && !!paths.changes_things && !awaitingAnswers && (services.length > 0 || doing.length > 0);
  // The path its tests passed, drawn: once there is one, it is what the crew runs.
  // Or the path written for this draft (Mavo drops one written for an earlier version of it).
  const flow = paths.recipe ?? paths.workflow_draft;
  const shownFlow = flow?.steps?.length ? flow : null;
  // The guarded agent: no program is written for it, so there is nothing of its own to test.
  const deskStep = shownFlow?.steps?.find(s => s.automation === 'agent.desk');
  const desk = !!deskStep;
  // Its hours, shown only where they mean something: a crew that reads a calendar.
  const deskParams = deskStep && shownFlow?.steps?.some(s => s.source === 'calendar') ? (deskStep.params ?? {}) as DeskParams : null;
  const canSkip = draftMode && !plan.questions?.length && firstRun?.status !== 'passed' && firstRun?.status !== 'running';
  const askFirst = plan.ask_first && firstRun?.status === 'skipped' ? ` It asks you before its first ${plan.ask_first.count} changes.` : '';

  function setGroup(ops: string[], enabled: boolean) {
    touched.current = true;
    setActions(prev => ({
      ...prev,
      ...Object.fromEntries(requestedActions.filter(a => ops.includes(a.operation)).map(a => [a.operation, { ...(prev[a.operation] ?? emptyRule()), enabled, mode: "restricted" as const, domains: a.domains ?? [], folders: a.folders ?? [], apps: appsOf(a), recipients: a.recipients ?? [] }])),
    }));
  }
  const groupOn = (ops: string[]) => ops.length > 0 && ops.every(op => actions[op]?.enabled);

  async function save(skipTest = false) {
    const confirming=!skipTest&&firstRun?.status==='passed'&&firstRun.kind==='action_preview'&&!firstRun.confirmed&&samePermissions&&!!firstRun.adapter_id&&firstRun.generation!=null;
    setBusy(true); setError(null);
    try {
      const item = confirming
        ? await api.activateCrewPreview(crew.id,firstRun!.adapter_id!,firstRun!.generation!)
        : await api.setCrewAccess(crew.id, { capabilities: granted, connector_capabilities: connectorCaps, actions, notifications, device_metrics: device, temp_cleanup: !!cleanup && clean, weather, route_id: route || null, skip_test: skipTest, test_level: level });
      touched.current=false;
      if (draftMode && item.status !== "draft") celebrated.add(item.id);
      useAppStore.getState().upsertCrew(item);
    } catch (e) { if(confirming&&e instanceof ApiError&&e.status===409)touched.current=true;setError(e instanceof Error ? e.message : "Could not save."); }
    finally { setBusy(false); }
  }

  const summary = [triggerLine({ ...crew, trigger_type: draft.trigger_type, schedule_time: draft.schedule_time ?? null, daily_triggers: draft.daily_triggers ?? [] } as CrewInstance), endLine(plan), sites[0], notifications && crew.template_id !== "schedule" ? "Desktop alerts" : null].filter(Boolean).join(" · ");

  // Added: no setup left to do. Its own card: who it is, that it is on, what it does, the facts
  // as chips, and one row of what to do next.
  if (!draftMode && !editing && !awaitingAnswers) {
    const facts = [
      firstRun?.status === "passed" ? { icon: <Check size={12} />, text: "Test passed", tone: "good" } : firstRun?.status === "skipped" ? { icon: <Check size={12} />, text: "On without a test", tone: "" } : null,
      { icon: <Clock size={12} />, text: [triggerLine(crew), endLine(plan)].filter(Boolean).join(" · "), tone: "" },
      crew.permissions.includes("notifications:show") ? { icon: <Bell size={12} />, text: "Desktop alerts", tone: "" } : null,
      plan.draft.requested_telegram ? { icon: <Send size={12} />, text: "Telegram", tone: "" } : null,
      sites[0] ? { icon: <Globe size={12} />, text: sites[0], tone: "" } : null,
      crew.hotkey ? { icon: <Keyboard size={12} />, text: crew.hotkey, tone: "" } : null,
    ].filter(Boolean) as { icon: ReactNode; text: string; tone: string }[];
    return <section className={"surface setup added-card" + (entering ? " setup-enter" : "")} aria-label={`${crew.name} is added`}>
      {(fresh || celebrating) && <Celebration seed={crew.id} name={crew.name} passed={celebrating} />}
      <div className="added-top">
        <span className="map-face lg has-bot" style={agentStyle(crew)}><AgentBot seed={crew.id} state={crew.is_running ? "ready" : "stopped"} /></span>
        <div className="min-w-0 flex-1">
          <h3>{crew.name}</h3>
          <p className="added-job">{crew.responsibilities}</p>
        </div>
        <span className="added-state" data-on={crew.is_running || undefined}><i />{crew.is_running ? "On" : "Stopped"}</span>
      </div>
      <ul className="added-facts">{facts.map(f => <li key={f.text} data-tone={f.tone || undefined}>{f.icon}{f.text}</li>)}</ul>
      <div className="added-actions">
        <button type="button" className="btn-primary" onClick={() => useAppStore.getState().openAgent(crew.id)}>Open its room<ArrowUpRight size={14} /></button>
        <button type="button" className="btn-quiet" onClick={() => setEditing(true)}>Edit</button>
        {onRefine && <button type="button" className="btn-quiet" onClick={() => onRefine(crew.id)}>Refine</button>}
      </div>
      {error && <p role="alert" className="notice error-notice mt-3">{error}</p>}
      {usesBrowser && sites.length > 0 && <div className="mt-3"><SignedInLine sites={sites} /></div>}
    </section>;
  }
  return <section className={"surface setup birth" + (fresh ? " birth-added" : "") + (entering ? " setup-enter" : "")} aria-label="Crew setup">
    {(fresh || celebrating) && <Celebration seed={crew.id} name={crew.name} passed={celebrating} />}
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 items-start gap-3">
        <span className={"map-face lg has-bot" + (entering ? " orb-forming" : "")} style={agentStyle(crew)}><AgentBot seed={crew.id} state={testing ? "working" : "ready"} /></span>
        <div className="min-w-0"><h3 className="text-[15px] font-semibold text-zinc-50"><TypedText text={crew.name} animate={entering} /><span className="setup-state">{draftMode ? "Draft" : "Added"}</span></h3>{!awaitingAnswers && <p className="mt-0.5 text-[13px] text-zinc-500">{summary}</p>}</div>
      </div>
      {!draftMode && <button className="btn-quiet !p-1.5" aria-label={"Open " + crew.name} onClick={() => useAppStore.getState().openAgent(crew.id)}><ArrowUpRight size={14} /></button>}
    </div>
    {draftMode && !awaitingAnswers && <Understood text={draft.responsibilities} onChange={onRefine ? () => onRefine(crew.id) : undefined} />}
    {testing && <TestingNote sample={firstRun?.tested_with === "sample"} />}
    {!shownFlow && paths.workflow_writing && draftMode && <p className="mt-3 text-[13px] text-zinc-400" role="status">Writing its full path, every case it handles…</p>}
    {!shownFlow && paths.workflow_problem && draftMode && <p className="mt-3 text-[13px] text-zinc-500">Its path could not be written: {paths.workflow_problem} <button type="button" className="btn-quiet !px-0" onClick={() => { asked.current = null; api.draftWorkflow(crew.id).then(item => useAppStore.getState().upsertCrew(item)).catch(() => undefined); }}>Try again</button></p>}
    {desk && deskParams && <DeskSettingsLine crewId={crew.id} params={deskParams} />}
    {/* How it works, step by step: there when wanted, folded otherwise. */}
    {shownFlow && <details className="setup-how"><summary>How it works</summary><WorkflowFlow steps={shownFlow.steps!} final={shownFlow.final} actions={requestedActions.map(a => a.operation)}/></details>}

    {awaitingAnswers && <p className="mt-3 text-[13px] leading-5 text-zinc-500">{plan.questions?.[0] ?? "Answer in chat."}</p>}
    {awaitingAnswers && !!plan.question_choices?.length && <SetupChoices crewId={crew.id} choices={plan.question_choices} />}
    {firstRunLine(firstRun) && <p className="mt-3 text-[13px] text-zinc-400" role="status">{firstRunLine(firstRun)}{askFirst}</p>}
    {firstRun?.status === 'running' && firstRun.kind === 'workflow' && <p className="mt-1 text-[12px] text-zinc-500">Sit back. Mavo is trying this crew on different emails so it handles real ones well.</p>}
    {weak.length > 0 && (firstRun?.status === 'partial' || firstRun?.status === 'failed') && <div className="mt-2 text-[13px] text-zinc-400">
      <p>It may not handle these well:</p>
      <ul className="mt-1 list-inside list-disc text-zinc-500">{weak.slice(0, 5).map(name => <li key={name}>{name}</li>)}</ul>
      {firstRun?.status === 'partial' && draftMode && <button type="button" className="btn-secondary mt-2" disabled={busy} onClick={() => { setBusy(true); setError(null); api.acceptWorkflow(crew.id).then(item => useAppStore.getState().upsertCrew(item)).catch(e => setError(e instanceof Error ? e.message : 'Could not turn it on.')).finally(() => setBusy(false)); }}>Turn it on anyway</button>}
    </div>}
    <FirstRunReads result={firstRun}/>
    {!!firstRun?.tests?.length && firstRun.status !== 'running' && <details className="mt-2 text-[13px] text-zinc-400"><summary>Tests</summary>
      <ul className="mt-2 space-y-2">{firstRun.tests.map(t => <li key={t.name}>
        <span>{`${t.passed ? '✓' : '✗'} ${t.name}`}</span>
        {!t.passed && <span className="block pl-4 text-zinc-500">
          {t.email && <span className="block">{t.email}</span>}
          {t.expected && <span className="block">{`Should: ${t.expected.join(', ')}`}</span>}
          {t.did && <span className="block">{`Did: ${t.did.join(', ')}`}</span>}
          {(t.stopped?.length ? t.stopped : t.problems).slice(0, 2).map(line => <span key={line} className="block">{t.stopped?.length ? `Stopped at ${line}` : line}</span>)}
        </span>}
      </li>)}</ul>
    </details>}
    {firstRun?.preview && firstRun.kind!=='action_preview' && firstRun.tested_with!=='sample' && <div className="mt-3"><Report compact text={firstRun.preview} /></div>}
    {!editing && !plan.questions?.length && !!plan.missing?.length && <div className="notice mt-4">{plan.missing[0]}</div>}

    {editing && !awaitingAnswers && <div className="mt-5 space-y-4">
      {!routes.length && <div className="notice"><p>Add a model first: this agent needs one to think with.</p><button type="button" className="btn-secondary mt-2" onClick={() => useAppStore.getState().setPage("settings")}>Add a model</button></div>}


      {loading && <p role="status" className="flex items-center gap-2 text-[13px] text-zinc-500"><LoaderCircle size={13} className="animate-spin" />Loading…</p>}
      {missing.length > 0 && <div className="notice"><p>Connect Google to continue.</p><button className="btn-secondary mt-2" disabled={google.pending || google.busy} onClick={() => void google.connect({ capabilities: granted })}>{google.pending ? "Connecting…" : "Connect Google"}</button></div>}
      {draftMode && <NeedsList items={[
        ...readsAsked.map(c => ({ key: c, text: `${SERVICE_NAMES[c.split(":")[0]] ?? c}: ${permissionLabels[c]}`, on: caps.includes(c) })),
        ...requestedActions.map(a => ({ key: a.operation, text: (actionTitles[a.operation] ?? a.operation.replace(".", " ")) + limitsOf(a), on: loading || !!actions[a.operation]?.enabled })),
        ...(draft.requested_notifications ? [{ key: "alerts", text: "Show desktop alerts", on: notifications }] : []),
        ...(draft.requested_telegram ? [{ key: "telegram", text: "Send its alerts to your Telegram", on: notifications }] : []),
        ...(draft.requested_device_metrics ? [{ key: "device", text: "Read device metrics", on: device }] : []),
        ...(draft.requested_weather ? [{ key: "weather", text: "Read weather forecasts", on: weather }] : []),
        ...(draft.requested_connector_capabilities ?? []).map(c => ({ key: c, text: c, on: connectorCaps.includes(c) })),
      ]} />}
      <FoldIf fold={grant} label="Change what it can do">
      {readsAsked.length > 0 && <div><h4 className="group-title">What it can read</h4><PermissionPicker value={caps.filter(c => !WRITES.test(c))} suggested={readsAsked} allowed={readsAsked} disabled={busy} onChange={value=>{touched.current=true;setCaps(value);}} /></div>}
      {!!draft.requested_connector_capabilities?.length && <div><h4 className="group-title">Accounts</h4><ConnectorPermissions value={connectorCaps} onChange={setConnectorCaps} allowed={draft.requested_connector_capabilities} /><button className="btn-quiet mt-2" onClick={() => useAppStore.getState().setPage("resources")}>Connect in Resources</button></div>}

      {(actingOps.length > 0 || projectOps.length > 0 || draft.requested_device_metrics || draft.requested_weather) && !loading && <div className="list-card">
        {projectOps.length > 0 && <Row title="Make files and run scripts" hint="Each script run asks you first."><Switch on={groupOn(projectOps)} onChange={v => setGroup(projectOps, v)} /></Row>}
        {actingOps.length > 0 && <Row title={"Click and type on " + (unique(requestedActions.filter(a => actingOps.includes(a.operation)).flatMap(a => a.domains ?? [])).join(", ") || "the site")} hint="Never passwords."><Switch on={groupOn(actingOps)} onChange={v => setGroup(actingOps, v)} /></Row>}
        {draft.requested_device_metrics && <Row title="Read device metrics"><Switch on={device} onChange={setDevice} /></Row>}
        {cleanup && <Row title={cleanup.empty_recycle_bin ? "Free space: temp files and Recycle Bin" : "Delete old temp files"}
          hint={`Your temp files older than ${cleanup.clean_temp_days === 1 ? "a day" : cleanup.clean_temp_days + " days"}${cleanup.empty_recycle_bin ? ", and empty the Recycle Bin (permanent)" : ""}. Nothing else.`}>
          <Switch on={clean} onChange={setClean} /></Row>}
        {draft.requested_weather && <Row title="Read weather forecasts"><Switch on={weather} onChange={setWeather} /></Row>}
      </div>}

      {!!directActions.length&&!loading&&<div><h4 className="group-title">What it can change</h4><div className="space-y-3">{directActions.map(action=><div key={action.operation} className="rounded-xl border border-line p-4"><p className="mb-2 text-sm">{actionTitles[action.operation] ?? action.operation.replace('.', ' · ')}</p><ActionAccessEditor operation={action.operation} value={actions[action.operation]??emptyRule()} onChange={value=>{touched.current=true;setActions(previous=>({...previous,[action.operation]:value}));}}/></div>)}</div></div>}
      </FoldIf>
      {blockers.length>0&&<div className="notice" role={'setup_confirmed' in plan&&plan.setup_confirmed?'alert':'status'}><p>To test it, first:</p><ul className="mt-2 list-inside list-disc">{blockers.map(item=><li key={item}>{readableBlocker(item)}</li>)}</ul></div>}
      {firstRun?.status==='failed'&&firstRun.kind!=='workflow'&&<p role="alert" className="notice error-notice">The test didn’t work: {firstRun.message}</p>}
      <CrewChangePreview result={firstRun}/>

      {direct ? <>
        {/* A crew that sends or books: on at once with its checked plan, asking before its first
            changes. Testing is there for whoever wants it, never in the way. */}
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-primary" disabled={busy || loading || policyError || testing || !shownFlow} onClick={() => void save(true)}>
            {(busy || loading) && <LoaderCircle size={13} className="animate-spin" />}{shownFlow ? 'Turn it on' : 'Writing its plan…'}
          </button>
        </div>
        {!desk && <details className="text-[13px] text-zinc-400" open={testing || undefined}><summary>Test it first</summary>
          <div className="mt-2 space-y-2">
            <div className="segmented" role="radiogroup" aria-label="How thoroughly to test">
              {TEST_LEVELS.map(([key, label]) => <button key={key} type="button" role="radio" aria-checked={level === key} aria-pressed={level === key} disabled={busy || testing} onClick={() => setLevel(key)}>{label}</button>)}
            </div>
            <p className="text-[12px] text-zinc-500">{TEST_HINTS[level]}</p>
            <button type="button" className="btn-secondary" disabled={busy || loading || policyError || testing || !shownFlow} onClick={() => void save()}>{testing ? 'Testing…' : firstRun?.kind === 'workflow' ? 'Test again' : 'Start the test'}</button>
          </div>
        </details>}
      </> : <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={busy || loading || policyError || blocked || (draftMode && !!plan.questions?.length)} onClick={() => void save()}>
          {(busy || loading || testing) && <LoaderCircle size={13} className="animate-spin" />}{draftMode ? busy ? 'Testing…' : firstRun?.kind==='action_preview'&&!samePermissions?'Test again':firstRunLabel(firstRun) : "Save"}
        </button>
        {canSkip && <button type="button" className="btn-quiet" disabled={busy || loading || policyError} onClick={() => void save(true)}>Skip test</button>}
        {canSkip && plan.changes_things && <span className="text-[12px] text-zinc-500">If you skip it, it asks you before its first 3 changes.</span>}
        {policyError&&<button type="button" className="btn-secondary" onClick={()=>{setLoading(true);setError(null);setPolicyRetry(value=>value+1);}}>Retry loading actions</button>}
        {/* Signing in ahead is optional: the agent says at run time if the site needs it. */}
        {sites[0] && <SignInLink host={sites[0]} />}
      </div>}

      <details className="setup-more"><summary>More</summary>
        <div className="mt-4 space-y-4">
          {!shownFlow && <CrewMembers crew={crew} onModel={draftMode?(key,routeId)=>{setError(null);api.setAgentModel(crew.id,key,routeId).then(item=>useAppStore.getState().upsertCrew(item)).catch(e=>setError(e instanceof Error?e.message:'Could not change the model.'));}:undefined}/>}
          {!loading && (safeOps.length > 0 || crew.template_id !== "schedule") && <div className="list-card">
            {safeOps.length > 0 && <Row title={describeSafe(requestedActions.filter(a => safeOps.includes(a.operation)))}><Switch on={groupOn(safeOps)} onChange={v => setGroup(safeOps, v)} /></Row>}
            {crew.template_id !== "schedule" && <Row title="Tell me on my desktop" hint="New results show on the Mavo icon."><Switch on={notifications} onChange={setNotifications} /></Row>}
            {crew.template_id !== "schedule" && <TelegramRow crew={crew} alerts={notifications} />}
          </div>}
          <details className="setup-more"><summary>Advanced</summary><div className="mt-4 space-y-4">
          <p className="whitespace-pre-wrap text-[13px] leading-6 text-zinc-400">{crew.responsibilities}</p>
          {requestedActions.length > directActions.length && <div className="space-y-3">{requestedActions.filter(a=>!directActions.includes(a)).map(a => <div key={a.operation} className="rounded-xl border border-line p-4"><p className="mb-3 text-xs font-medium">{a.operation.replace(".", " · ")}{savedOperations.includes(a.operation) && <span className="ml-2 badge">Already allowed</span>}</p><ActionAccessEditor operation={a.operation} value={actions[a.operation] ?? { ...emptyRule(), folders: a.folders ?? [], domains: a.domains ?? [], apps: a.apps ?? [], recipients: a.recipients ?? [] }} onChange={v => { touched.current = true; setActions({ ...actions, [a.operation]: v }); }} /></div>)}</div>}
          {usesBrowser && <div><h4 className="group-title">Site uses a password?</h4><SaveLoginForm compact defaultSite={siteGuess(crew.responsibilities, requestedActions)} /></div>}
          </div></details>
          {draftMode && <button type="button" className="btn-quiet" onClick={() => setDeleting(true)}>Discard this draft</button>}
        </div>
      </details>
    </div>}

    {!awaitingAnswers && !editing && <button className="btn-quiet mt-3" onClick={() => setEditing(true)}>Edit</button>}
    {!awaitingAnswers && !editing && (onRefine || draftMode) && <div className="mt-3 flex gap-1">
      {onRefine && !draftMode && <button className="btn-quiet" onClick={() => onRefine(crew.id)}>Refine</button>}
      {draftMode && <button className="btn-quiet" onClick={() => setDeleting(true)}>Discard</button>}
    </div>}
    {error && <p role="alert" className="notice error-notice mt-3">{error}</p>}
    {!awaitingAnswers && (google.error || google.status?.error) && <p role="alert" className="notice error-notice mt-3">{google.error || google.status?.error}</p>}
    {awaitingAnswers && draftMode && <div className="mt-3"><button className="btn-quiet" onClick={() => setDeleting(true)}>Discard</button></div>}
    {!draftMode && !awaitingAnswers && usesBrowser && sites.length > 0 && <div className="mt-4"><SignedInLine sites={sites} /></div>}
    {deleting && <DeleteCrewDialog crew={crew} onClose={() => setDeleting(false)} />}
  </section>;
}

/** A setup question's options as buttons: the one pressed is sent as the answer, as if typed. */
function SetupChoices({ crewId, choices }: { crewId: string; choices: string[] }) {
  const [sent, setSent] = useState<string | null>(null);
  const chat = useAppStore(s => s.conversations.find(c => c.id === s.activeConversationId) ?? null);
  if (!chat) return null;
  return <div className="setup-choices" role="group" aria-label="Pick one">
    {choices.map((choice, i) => <button key={choice} type="button" className={i === 0 ? "btn-primary" : "btn-secondary"} disabled={!!sent}
      onClick={() => { setSent(choice); void import("../lib/chatController").then(m => m.sendCoreMessage(chat, choice, crewId)); }}>{choice}</button>)}
  </div>;
}

/** "Allow gmail:read" as a person reads it: "Allow it to read inbox headers". */
function readableBlocker(item: string): string {
  const grant = /^Allow ([a-z]+:[a-z]+)$/.exec(item)?.[1] as keyof typeof permissionLabels | undefined;
  if (grant && permissionLabels[grant]) return "Allow it to " + permissionLabels[grant].charAt(0).toLowerCase() + permissionLabels[grant].slice(1);
  const op = /^Allow (\w+\.\w+)$/.exec(item)?.[1];
  if (op) return "Allow it to " + (actionTitles[op] ?? op.replace(".", " ")).toLowerCase();
  return item;
}
