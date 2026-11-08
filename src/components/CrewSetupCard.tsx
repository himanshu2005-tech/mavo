import { ArrowUpRight, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CrewInstance, GoogleCapability } from "../types";
import { api } from "../lib/api";
import { useGoogleConnection } from "../lib/useGoogleConnection";
import { RUN_TOOLS } from "../lib/actionRuleInput";
import { useAppStore } from "../store/app";
import { ActionAccessEditor, emptyRule, type ActionRule } from "./ActionAccessEditor";
import { PermissionPicker } from "./PermissionPicker";
import { DeleteCrewDialog } from "./DeleteCrewDialog";
import { SignedInLine } from "./SignedInLine";
import { ConnectorPermissions } from "./ConnectorAccounts";
import { SaveLoginForm } from "./SaveLogin";
import { SampleRun } from "./SampleRun";
import { SiteLine } from "./SiteLine";
import { endLine, triggerLine } from "./CrewCard";
import { CrewArt, artStyle } from "./CrewArt";

// Reading and finding things is allowed with one switch; doing things (click, type) is its own switch.
const SAFE_OPERATIONS = ["browser.open", "browser.read", "browser.search", "browser.navigate", "browser.scroll", "browser.wait"];
const ACTING_OPERATIONS = ["browser.click", "browser.double_click", "browser.drag", "browser.hover", "browser.fill", "browser.select", "browser.press"];
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

function AddedMark() {
  return <div className="added" role="status"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle className="added-ring" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" /><path className="added-tick" d="M7.5 12.5l3 3 6-6.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg><span>Added to Crew</span></div>;
}

export function CrewSetupCard({ crew, onRefine }: { crew: CrewInstance; onRefine?: (id: string) => void }) {
  const plan = crew.configuration_plan?.draft ? crew.configuration_plan : null;
  const awaitingAnswers = !!plan?.questions?.length || plan?.phase === "needs_input";
  const routes = useAppStore(s => s.routes);
  const draftMode = crew.status === "draft";

  const [caps, setCaps] = useState<GoogleCapability[]>(() => crew.permissions.filter(p => p.startsWith("google:")).map(p => p.slice(7)) as GoogleCapability[]);
  const [connectorCaps, setConnectorCaps] = useState<string[]>(() => crew.permissions.filter(p => p.startsWith("github:") || p.startsWith("feeds:")));
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
  const [route, setRoute] = useState(crew.route_id ?? routes[0]?.id ?? "");
  const touched = useRef(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(plan?.phase !== "ready");
  const [loading, setLoading] = useState(!!plan?.draft.requested_actions.length);
  const [error, setError] = useState<string | null>(null);
  const google = useGoogleConnection(!awaitingAnswers);
  const fresh = celebrated.has(crew.id) && crew.status !== "draft";

  useEffect(() => { if (!route && routes.length === 1) setRoute(routes[0].id); }, [routes, route]);
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
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [crew.id, awaitingAnswers]);

  // Safe, read-only browsing starts allowed for a new draft; the person can switch it off.
  useEffect(() => {
    if (loading || !plan || !draftMode) return;
    setActions(prev => {
      const next = { ...prev };
      let changed = false;
      for (const a of plan.draft.requested_actions) {
        if ((SAFE_OPERATIONS.includes(a.operation) || PROJECT_OPERATIONS.includes(a.operation)) && !next[a.operation]?.enabled && !savedOperations.includes(a.operation) && !touched.current) {
          next[a.operation] = { ...emptyRule(), enabled: true, mode: "restricted", domains: a.domains, folders: a.folders, apps: appsOf(a), recipients: a.recipients };
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [loading]);

  if (!plan) return null;
  const draft = plan.draft;
  const requestedActions = draft.requested_actions as Requested[];
  const safeOps = requestedActions.map(a => a.operation).filter(op => SAFE_OPERATIONS.includes(op));
  const actingOps = requestedActions.map(a => a.operation).filter(op => ACTING_OPERATIONS.includes(op));
  const projectOps = requestedActions.map(a => a.operation).filter(op => PROJECT_OPERATIONS.includes(op));
  const sites = awaitingAnswers ? [] : sitesOf(crew.responsibilities, requestedActions);
  const requested = unique([...draft.requested_google_capabilities, ...draft.requested_google_resources.map(r => `${r}:read`)]) as GoogleCapability[];
  const missing = caps.filter(c => !google.status?.capabilities?.includes(c));
  const usesBrowser = requestedActions.some(a => a.operation.startsWith("browser."));

  function setGroup(ops: string[], enabled: boolean) {
    touched.current = true;
    setActions(prev => ({
      ...prev,
      ...Object.fromEntries(requestedActions.filter(a => ops.includes(a.operation)).map(a => [a.operation, { ...(prev[a.operation] ?? emptyRule()), enabled, mode: "restricted" as const, domains: a.domains ?? [], folders: a.folders ?? [], apps: appsOf(a), recipients: a.recipients ?? [] }])),
    }));
  }
  const groupOn = (ops: string[]) => ops.length > 0 && ops.every(op => actions[op]?.enabled);

  async function save() {
    setBusy(true); setError(null);
    try {
      const item = await api.setCrewAccess(crew.id, { capabilities: caps, connector_capabilities: connectorCaps, actions, notifications, device_metrics: device, temp_cleanup: !!cleanup && clean, weather, route_id: route || null });
      if (draftMode && item.status !== "draft") celebrated.add(item.id);
      useAppStore.getState().upsertCrew(item);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
    finally { setBusy(false); }
  }

  const summary = [triggerLine({ ...crew, trigger_type: draft.trigger_type, schedule_time: draft.schedule_time ?? null, daily_triggers: draft.daily_triggers ?? [] } as CrewInstance), endLine(plan), sites[0], notifications && crew.template_id !== "schedule" ? "Desktop alerts" : null].filter(Boolean).join(" · ");

  return <section className={"surface setup birth" + (fresh ? " birth-added" : "")} aria-label="Crew setup" style={artStyle(crew.template_id)}>
    <div className="tile-art setup-art" aria-hidden="true"><CrewArt template={crew.template_id} /><span className="crew-chip"><i />{draftMode ? "Draft" : "Added"}</span></div>
    {fresh && <AddedMark />}
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0"><h3 className="text-[16px] font-semibold tracking-[-0.01em] text-zinc-50">{crew.name}</h3>{!awaitingAnswers && <p className="mt-0.5 text-[13px] text-zinc-500">{summary}</p>}</div>
      {!draftMode && <button className="btn-quiet !p-1.5" aria-label={"Open " + crew.name} onClick={() => useAppStore.getState().openAgent(crew.id)}><ArrowUpRight size={14} /></button>}
    </div>

    {awaitingAnswers && <p className="mt-3 text-[13px] leading-5 text-zinc-500">{plan.questions?.[0] ?? "Answer in chat."} Connections and permissions come next.</p>}
    {!editing && !plan.questions?.length && !!plan.missing?.length && <div className="notice mt-4">{plan.missing[0]}</div>}

    {editing && !awaitingAnswers && <div className="mt-5 space-y-4">
      {routes.length !== 1 && <label className="block text-[12px] font-medium text-zinc-500">Agent connection
        <select className="form-control" value={route} onChange={e => setRoute(e.target.value)}><option value="">Choose a connection</option>{routes.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
        {!routes.length && <button type="button" className="btn-secondary mt-3" onClick={() => useAppStore.getState().setPage("settings")}>Add connection</button>}
      </label>}

      {sites.length > 0 && <SiteLine sites={sites} />}
      {(/https?:\/\//.test(crew.responsibilities) || usesBrowser) && <SampleRun crewId={crew.id} />}

      {requested.length > 0 && <div><h4 className="group-title">Resource access</h4><PermissionPicker value={caps} suggested={requested} allowed={requested} onChange={setCaps} /></div>}
      {!!draft.requested_connector_capabilities?.length && <div><h4 className="group-title">Connected accounts</h4><ConnectorPermissions value={connectorCaps} onChange={setConnectorCaps} allowed={draft.requested_connector_capabilities} /><button className="btn-quiet mt-2" onClick={() => useAppStore.getState().setPage("resources")}>Connect in Resources</button></div>}

      {(actingOps.length > 0 || projectOps.length > 0 || draft.requested_device_metrics || draft.requested_weather) && !loading && <div className="list-card">
        {projectOps.length > 0 && <Row title="Make files and run scripts" hint="Each script run asks you first."><Switch on={groupOn(projectOps)} onChange={v => setGroup(projectOps, v)} /></Row>}
        {actingOps.length > 0 && <Row title={"Click and type on " + (unique(requestedActions.filter(a => actingOps.includes(a.operation)).flatMap(a => a.domains ?? [])).join(", ") || "the site")} hint="Never passwords."><Switch on={groupOn(actingOps)} onChange={v => setGroup(actingOps, v)} /></Row>}
        {draft.requested_device_metrics && <Row title="Read device metrics"><Switch on={device} onChange={setDevice} /></Row>}
        {cleanup && <Row title={cleanup.empty_recycle_bin ? "Free space: temp files and Recycle Bin" : "Delete old temp files"}
          hint={`Your temp files older than ${cleanup.clean_temp_days === 1 ? "a day" : cleanup.clean_temp_days + " days"}${cleanup.empty_recycle_bin ? ", and empty the Recycle Bin (permanent)" : ""}. Nothing else.`}>
          <Switch on={clean} onChange={setClean} /></Row>}
        {draft.requested_weather && <Row title="Read weather forecasts"><Switch on={weather} onChange={setWeather} /></Row>}
      </div>}
      {loading && <p role="status" className="flex items-center gap-2 text-[13px] text-zinc-500"><LoaderCircle size={13} className="animate-spin" />Loading…</p>}
      {missing.length > 0 && <div className="notice"><p>Connect Google to continue.</p><button className="btn-secondary mt-2" disabled={google.pending || google.busy} onClick={() => void google.connect({ capabilities: caps })}>{google.pending ? "Connecting…" : "Connect Google"}</button></div>}

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={busy || loading || (draftMode && !!plan.questions?.length)} onClick={() => void save()}>
          {(busy || loading) && <LoaderCircle size={13} className="animate-spin" />}{draftMode ? "Add Crew" : "Save"}
        </button>
        {onRefine && <button type="button" className="btn-quiet" onClick={() => onRefine(crew.id)}>Refine</button>}
        {/* Signing in is offered above and during "Try it first"; it never blocks adding the Crew,
            because the agent decides at run time whether the site needs it. */}
      </div>

      <details className="setup-more"><summary>More options</summary>
        <div className="mt-4 space-y-4">
          {!loading && (safeOps.length > 0 || crew.template_id !== "schedule") && <div className="list-card">
            {safeOps.length > 0 && <Row title={describeSafe(requestedActions.filter(a => safeOps.includes(a.operation)))}><Switch on={groupOn(safeOps)} onChange={v => setGroup(safeOps, v)} /></Row>}
            {crew.template_id !== "schedule" && <Row title="Tell me on my desktop" hint="New results show on the Mavo icon."><Switch on={notifications} onChange={setNotifications} /></Row>}
          </div>}
          <details className="setup-more"><summary>Advanced</summary><div className="mt-4 space-y-4">
          <p className="whitespace-pre-wrap text-[13px] leading-6 text-zinc-400">{crew.responsibilities}</p>
          {requestedActions.length > 0 && <div className="space-y-3">{requestedActions.map(a => <div key={a.operation} className="rounded-xl border border-line p-4"><p className="mb-3 text-xs font-medium">{a.operation.replace(".", " · ")}{savedOperations.includes(a.operation) && <span className="ml-2 badge">Already allowed</span>}</p><ActionAccessEditor operation={a.operation} value={actions[a.operation] ?? { ...emptyRule(), folders: a.folders ?? [], domains: a.domains ?? [], apps: a.apps ?? [], recipients: a.recipients ?? [] }} onChange={v => { touched.current = true; setActions({ ...actions, [a.operation]: v }); }} /></div>)}</div>}
          {usesBrowser && <div><h4 className="group-title">Site uses a password?</h4><SaveLoginForm compact defaultSite={siteGuess(crew.responsibilities, requestedActions)} /></div>}
          </div></details>
          {draftMode && <button type="button" className="btn-quiet" onClick={() => setDeleting(true)}>Discard this draft</button>}
        </div>
      </details>
    </div>}

    {!awaitingAnswers && !editing && <button className="btn-quiet mt-3" onClick={() => setEditing(true)}>Edit</button>}
    {!awaitingAnswers && !editing && (onRefine || draftMode) && <div className="mt-3 flex gap-1">
      {onRefine && <button className="btn-quiet" onClick={() => onRefine(crew.id)}>Refine</button>}
      {draftMode && <button className="btn-quiet" onClick={() => setDeleting(true)}>Discard</button>}
    </div>}
    {error && <p role="alert" className="notice error-notice mt-3">{error}</p>}
    {!awaitingAnswers && (google.error || google.status?.error) && <p role="alert" className="notice error-notice mt-3">{google.error || google.status?.error}</p>}
    {awaitingAnswers && draftMode && <div className="mt-3"><button className="btn-quiet" onClick={() => setDeleting(true)}>Discard</button></div>}
    {!draftMode && !awaitingAnswers && usesBrowser && sites.length > 0 && <div className="mt-4"><SignedInLine sites={sites} /></div>}
    {deleting && <DeleteCrewDialog crew={crew} onClose={() => setDeleting(false)} />}
  </section>;
}
