import { invoke } from "@tauri-apps/api/core";
import { Bell, LoaderCircle, Pencil, Play, RotateCw, Square, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CrewReplyBox } from "./CrewReplyBox";
import { CrewAsk } from "./CrewAsk";
import { CrewGrants } from "./CrewGrants";
import { CrewSetupCard } from "./CrewSetupCard";
import { Report } from "./Report";
import { endLine, triggerLine } from "./CrewCard";
import { SampleRun } from "./SampleRun";
import { DeleteCrewDialog } from "./DeleteCrewDialog";
import { CrewAccountPermissions } from "./CrewAccountPermissions";
import { runClock } from "../lib/runClock";
import { HandoverCard } from "./LinePanel";
import { LimitCard, limitOf } from "./LimitCard";
import { clockTime } from "../lib/time";
import { api } from "../lib/api";
import { localTimestamp } from "../lib/time";
import { useAppStore } from "../store/app";
import type { CrewInput, CrewInstance, CrewRun, GoogleResource, GoogleCapability, TriggerType } from "../types";

const triggerNames: Record<TriggerType, string> = { manual: "When I request it", interval: "At a review interval", daily: "At a daily reminder time", gmail_message: "When matching email arrives", calendar_changed: "When calendar events change", tasks_changed: "When Google Tasks change" };
const eventResources: Partial<Record<TriggerType, GoogleResource>> = { gmail_message: "gmail", calendar_changed: "calendar", tasks_changed: "tasks" };

/** One agent, in the panel beside the graph: what it is doing, what it found, asking it, and
 *  changing it. `access` is its switches (what it reads and can do), drawn by the graph. */
export function CrewDetail({ crew, access }: { crew: CrewInstance; access?: ReactNode }) {
  const routes = useAppStore(s => s.routes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  async function toggle() {
    setBusy(true); setError(null);
    try {
      const updated = crew.is_running ? await api.stopCrew(crew.id) : await api.startCrew(crew.id);
      useAppStore.getState().upsertCrew(updated);
      if (crew.is_running) {
        const pending = await invoke<{ crew_name: string } | null>("pending_reminder").catch(() => null);
        if (pending?.crew_name === crew.name) await invoke("close_reminder_overlay").catch(() => undefined);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function check() {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.checkCrew(crew.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not check it."); }
    finally { setBusy(false); }
  }
  async function preview() {
    try {
      if (await invoke("pending_reminder")) { setError("Dismiss the open reminder before previewing another."); return; }
      await invoke("show_reminder_overlay", { reminder: { id: `preview-${crew.id}`, crew_name: crew.name, message: crew.reminder_text || crew.responsibilities, scheduled_for: new Date().toISOString(), preview: true } });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Open the desktop app to preview reminders."); }
  }
  const draft = crew.status === "draft";
  const signIn = /sign in|human check/i.test(crew.question || crew.last_runtime_error || "");
  return <div className="crew-detail-panel">
    <div className="flex items-center justify-between gap-3">
      <p className="min-w-0 text-[12.5px] leading-5 text-zinc-400">{[triggerLine(crew), endLine(crew.configuration_plan)].filter(Boolean).join(" · ")}</p>
      {!draft && <button className={crew.is_running ? "btn-secondary btn-stop !py-1.5" : "btn-primary !py-1.5"} disabled={busy} onClick={() => void toggle()}>{busy ? <LoaderCircle size={13} className="animate-spin" /> : crew.is_running ? <Square size={12} /> : <Play size={12} />}{crew.is_running ? "Stop" : "Start"}</button>}
    </div>
    {!draft && <NextRun crew={crew} />}
    {!draft && crew.skipped_run && <p className="skipped-run">Skipped {new Date(crew.skipped_run.day + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}'s run: Mavo wasn't running at {crew.schedule_time ?? "its time"}. It ran again at its next time instead of twice in a row.</p>}
    {error && <div role="alert" className="notice error-notice">{error}</div>}
    {draft && crew.configuration_plan?.draft && <CrewSetupCard key={crew.id + JSON.stringify(crew.configuration_plan)} crew={crew} onRefine={id => useAppStore.getState().refineInChat(id)} />}
    {crew.configuration_plan?.finished && <div className="notice" role="status">Done: {crew.configuration_plan.finished.why}. Start it again any time.</div>}
    {crew.held && <HandoverCard crew={crew} />}
    {!crew.held && limitOf(crew) && <LimitCard crew={crew} />}
    {!crew.held && !limitOf(crew) && (crew.question || crew.last_runtime_error) && <div className="notice" role="status">{crew.question || crew.last_runtime_error}{signIn && <div className="mt-3 flex gap-2"><button className="btn-secondary" onClick={() => useAppStore.getState().setPage("resources")}>Open browser to sign in</button><button className="btn-primary" disabled={busy} onClick={() => void toggle()}>Resume</button></div>}<CrewGrants crew={crew} /></div>}
    {access}
    {!draft && <CrewOutput crew={crew} onCheck={crew.is_running && crew.trigger_type !== "daily" ? () => void check() : undefined} checking={crew.runtime_state === "working" || busy} onPreview={crew.trigger_type === "daily" ? () => void preview() : undefined} />}
    {crew.is_running && /https?:\/\//.test(crew.responsibilities) && <SampleRun key={crew.id} crewId={crew.id} />}
    {!draft && <details className="setup-more"><summary>Details</summary>
      <div className="mt-4">
        <p className="whitespace-pre-wrap text-[13px] leading-6 text-zinc-400">{crew.responsibilities}</p>
        <div className="mt-4 border-y border-line py-1"><Detail label="Connection" value={routes.find((r) => r.id === crew.route_id)?.name ?? "Choose one in Edit"} /><Detail label="When" value={triggerNames[crew.trigger_type]} />{crew.trigger_type === "daily" && <Detail label="Times" value={crew.daily_triggers?.length ? crew.daily_triggers.filter(t => t.enabled).map(t => formatTime(t.time) + (t.weekdays.length < 7 ? " (" + t.weekdays.map(d => "MTWTFSS"[d]).join("") + ")" : "")).join(" · ") : crew.schedule_time ? formatTime(crew.schedule_time) : "Set a time in Edit"} />}{crew.trigger_type !== "daily" && <Detail label="Model calls today" value={`${crew.daily_calls_used} of ${crew.daily_call_limit}`} />}{crew.is_running && crew.next_run_at && <Detail label="Next check" value={localTimestamp(crew.next_run_at)} />}</div>
        <div className="mt-4 flex flex-wrap gap-1"><button className="btn-quiet" onClick={() => setEditing(true)}><Pencil size={13} />Edit</button><button className="btn-quiet" onClick={() => { sessionStorage.setItem("mavo-action-actor", crew.id); useAppStore.getState().setPage("actions"); }}>All action rules</button><button className="btn-quiet btn-stop" disabled={busy} onClick={() => setDeleting(true)}><Trash2 size={13} />Delete</button></div>
        <CrewHistory crew={crew} />
      </div>
    </details>}
    {draft && <div><button className="btn-quiet btn-stop" onClick={() => setDeleting(true)}><Trash2 size={13} />Discard draft</button></div>}
    {editing && <CrewForm existing={crew} onClose={() => setEditing(false)} onSaved={() => setEditing(false)} />}
    {deleting && <DeleteCrewDialog crew={crew} onClose={() => setDeleting(false)} />}
  </div>;
}

function Detail({ label, value }: { label: string; value: string }) { return <div className="settings-row"><span className="shrink-0 text-zinc-500">{label}</span><span className="max-w-[65%] text-right leading-5 text-zinc-200">{value}</span></div>; }

/** The same clock as on the map: how far the agent is to its next run. */
function NextRun({ crew }: { crew: CrewInstance }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const clock = runClock(crew, now);
  if (!clock) return null;
  return <div className="next-run" aria-label={`Next run ${clock.label}`}>
    <div className="next-run-line"><span>Next run <b>{clock.label}</b></span><span>{crew.next_run_at && clock.left > 0 ? clockTime(crew.next_run_at) : ""}</span></div>
    <div className="next-run-bar"><i style={{ width: `${clock.progress * 100}%` }} /></div>
  </div>;
}

function CrewOutput({ crew, onCheck, checking, onPreview }: { crew: CrewInstance; onCheck?: () => void; checking: boolean; onPreview?: () => void }) {
  const empty = crew.trigger_type === "daily" ? "Reminders appear on your desktop at the scheduled time." : crew.is_running ? "Waiting for the first result." : "No work yet. Press Run.";
  return <section aria-label="Latest report">
    <div className="mb-3 flex items-center justify-between gap-3"><h3 className="text-[13px] font-semibold text-zinc-300">Latest report</h3>
      <div className="flex gap-1">{onCheck && <button className="btn-quiet" disabled={checking} onClick={onCheck}><RotateCw size={13} className={checking ? "animate-spin" : ""} />Check now</button>}{onPreview && <button className="btn-quiet" onClick={onPreview}><Bell size={13} />Preview</button>}</div></div>
    {crew.checkpoint ? <Report text={crew.checkpoint} /> : <p className="text-[13px] text-zinc-500">{empty}</p>}
    {crew.question && !crew.held && !limitOf(crew) && <CrewReplyBox crew={crew} />}
    {crew.trigger_type !== "daily" && !crew.question && <div className="mt-6"><h3 className="mb-3 text-[13px] font-semibold text-zinc-300">Ask</h3>
      <Preferences crew={crew} />
      <CrewAsk key={crew.id} crewId={crew.id} name={crew.name} onPreference={() => void api.crew().then(all => useAppStore.getState().setCrew(all)).catch(() => undefined)} /></div>}
  </section>;
}

/** What the person told this Crew to do from now on. Each can be taken back. */
function Preferences({ crew }: { crew: CrewInstance }) {
  const list = crew.configuration_plan?.preferences ?? [];
  const [busy, setBusy] = useState<number | null>(null);
  if (!list.length) return null;
  async function forget(index: number) {
    setBusy(index);
    try { useAppStore.getState().upsertCrew(await api.forgetPreference(crew.id, index)); } catch { /* stays */ } finally { setBusy(null); }
  }
  return <div className="prefs" aria-label="Its standing preferences">{list.map((p, i) =>
    <span key={p.at + i} className="pref">{p.text}<button aria-label={`Forget: ${p.text}`} disabled={busy === i} onClick={() => void forget(i)}><X size={11} /></button></span>)}</div>;
}

function CrewHistory({ crew }: { crew: CrewInstance }) {
  const [runs, setRuns] = useState<CrewRun[]>([]);
  useEffect(() => { let active = true; api.crewRuns(crew.id).then((r) => { if (active) setRuns(r); }).catch(() => undefined); return () => { active = false; }; }, [crew.id, crew.last_run_at, crew.runtime_state]);
  if (!runs.length) return null;
  return <details className="mt-4 text-[12px] text-zinc-500"><summary>Earlier work ({runs.length})</summary><div className="mt-3 space-y-3">{runs.slice(0, 10).map((run) => <div key={run.id} className="rounded-2xl border border-line p-4"><div className="mb-2 flex justify-between gap-2"><span className="capitalize">{run.status}</span><time className="text-[11px]">{localTimestamp(run.created_at)}</time></div><Report compact text={run.result ?? run.error ?? "Working…"} /></div>)}</div></details>;
}

function CrewForm({ existing, onClose, onSaved }: { existing: CrewInstance | null; onClose: () => void; onSaved: (id: string) => void }) {
  const { crewTemplates: templates, routes } = useAppStore();
  const [form, setForm] = useState<CrewInput>({ template_id: existing?.template_id ?? "schedule", name: existing?.name ?? "", responsibilities: existing?.responsibilities ?? "", autonomy_mode: existing?.autonomy_mode ?? "proactive", route_id: existing?.route_id ?? routes[0]?.id ?? "", max_model_calls: existing?.max_model_calls ?? 5, schedule_time: existing?.schedule_time ?? null, reminder_text: existing?.reminder_text ?? "", review_interval_minutes: existing?.review_interval_minutes ?? 60, daily_call_limit: existing?.daily_call_limit ?? 12, allow_device_metrics: existing?.permissions.includes("device:read") ?? false, trigger_type: existing?.trigger_type ?? "daily", google_resources: (existing?.resources.filter((r) => r.startsWith("google:")).map((r) => r.split(":")[1]) ?? []) as GoogleResource[], mail_sender: existing?.mail_sender ?? null, mail_subject: existing?.mail_subject ?? null });
  const [saving, setSaving] = useState(false);
  useEffect(()=>{setForm(f=>({...f,allow_notifications:existing?.permissions.includes("notifications:show")??false,device_monitor:existing?.configuration_plan?.device_monitor??existing?.configuration_plan?.draft?.device_monitor??null}));},[existing]);
  useEffect(()=>{setForm(f=>({...f,connector_capabilities:existing?.permissions.filter(p=>p.startsWith("github:")||p.startsWith("feeds:"))??[]}));},[existing]);
  useEffect(() => { setForm(f => ({...f, allow_weather: existing?.permissions.includes("weather:read") ?? false})); }, [existing]);
  useEffect(() => { const caps = (existing?.permissions.filter(p => p.startsWith("google:")).map(p => p.slice(7)) ?? []) as GoogleCapability[]; setForm(f => ({ ...f, google_capabilities: caps, google_resources: caps.filter(c => c.endsWith(":read")).map(c => c.split(":")[0]) as GoogleResource[] })); }, [existing]);
  const [error, setError] = useState<string | null>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const returnFocus = useRef(document.activeElement as HTMLElement | null);
  const closeRef = useRef(onClose);
  const savingRef = useRef(saving);
  closeRef.current = onClose;
  savingRef.current = saving;
  const template = templates.find((t) => t.id === form.template_id);
  const needed = eventResources[form.trigger_type ?? "interval"];
  useEffect(() => {
    const previous = returnFocus.current;
    function keydown(e: KeyboardEvent) {
      if (e.key === "Escape" && !savingRef.current) closeRef.current();
      if (e.key === "Tab") {
        const fields = sheet.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary');
        if (!fields?.length) return;
        const first = fields[0], last = fields[fields.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); previous?.focus(); };
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault(); setSaving(true); setError(null);
    try {
      const { template_id: _, ...changes } = form;
      const saved = existing ? await api.updateCrew(existing.id, changes) : await api.createCrew(form);
      useAppStore.getState().upsertCrew(saved); onSaved(saved.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not save Crew."); }
    finally { setSaving(false); }
  }
  return <div className="sheet-backdrop"><div className="sheet" ref={sheet} role="dialog" aria-modal="true" aria-labelledby="crew-form-title"><form onSubmit={submit}><div className="mb-6 flex items-center justify-between"><h2 id="crew-form-title" className="text-lg font-semibold text-zinc-100">{existing ? "Edit Crew" : "New Crew"}</h2><button type="button" className="btn-quiet !p-1" aria-label="Close Crew form" disabled={saving} onClick={onClose}><X size={17} /></button></div>
    {error && <div className="notice error-notice mb-4" role="alert">{error}</div>}
    <div className="space-y-4"><Field label="Name"><input className="form-control" autoFocus required maxLength={80} placeholder="Crew name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field><div className="grid grid-cols-2 gap-4"><Field label="Type"><select className="form-control" disabled={!!existing} value={form.template_id} onChange={(e) => { const t = templates.find((t) => t.id === e.target.value); setForm({ ...form, template_id: e.target.value, review_interval_minutes: t?.default_interval_minutes ?? 30, schedule_time: null, allow_device_metrics: false, trigger_type: e.target.value === "schedule" ? "daily" : e.target.value === "communication" ? "gmail_message" : "interval", google_resources: [] }); }}>{templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field><Field label="Model route"><select className="form-control" required value={form.route_id} onChange={(e) => setForm({ ...form, route_id: e.target.value })}><option value="" disabled>Select a route</option>{routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field></div><p className="text-[11px] leading-5 text-zinc-500">{template?.behavior}</p><Field label="Instructions"><textarea className="form-control resize-y" rows={4} required maxLength={4000} placeholder="Describe what this Crew should do." value={form.responsibilities} onChange={(e) => setForm({ ...form, responsibilities: e.target.value })} /></Field>
    <Field label="When should this Crew work?"><select aria-label="Crew trigger" className="form-control" value={form.trigger_type} onChange={(e) => setForm({ ...form, trigger_type: e.target.value as TriggerType, schedule_time: e.target.value === "daily" ? form.schedule_time : null })}>{Object.entries(triggerNames).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>
    {form.trigger_type === "daily" ? <div className="grid grid-cols-[.8fr_1.5fr] gap-4"><Field label="Daily time"><input type="time" className="form-control" value={form.schedule_time ?? ""} onChange={(e) => setForm({ ...form, schedule_time: e.target.value || null })} /><span className="mt-1 block text-[10px] text-zinc-500">Windows local time</span></Field><Field label="Reminder message"><input className="form-control" maxLength={1000} placeholder="Uses your instructions if empty" value={form.reminder_text ?? ""} onChange={(e) => setForm({ ...form, reminder_text: e.target.value })} /></Field></div> : <div className="grid grid-cols-2 gap-4">{form.trigger_type === "interval" && <Field label="Check every (minutes)"><input className="form-control" type="number" min={5} max={10080} required value={form.review_interval_minutes} onChange={(e) => setForm({ ...form, review_interval_minutes: Number(e.target.value) })} /></Field>}<Field label="AI calls per run"><input className="form-control" type="number" min={2} max={50} required value={form.max_model_calls} onChange={(e) => setForm({ ...form, max_model_calls: Number(e.target.value) })} /><span className="mt-1 block text-[10px] text-zinc-500">Each step it thinks through is one call. At least 2: one to decide, one to finish.</span></Field><Field label="AI calls per day"><input className="form-control" type="number" min={Math.max(2, form.max_model_calls ?? 2)} max={100} required value={form.daily_call_limit} onChange={(e) => setForm({ ...form, daily_call_limit: Number(e.target.value) })} /><span className="mt-1 block text-[10px] text-zinc-500">Out of calls, it waits until tomorrow by itself.</span></Field></div>}
    {form.trigger_type === "gmail_message" && <div className="grid grid-cols-2 gap-4"><Field label="Sender contains (optional)"><input className="form-control" value={form.mail_sender ?? ""} maxLength={160} onChange={(e) => setForm({ ...form, mail_sender: e.target.value || null })} placeholder="admissions@college.edu" /></Field><Field label="Subject contains (optional)"><input className="form-control" value={form.mail_subject ?? ""} maxLength={160} onChange={(e) => setForm({ ...form, mail_subject: e.target.value || null })} placeholder="deadline" /></Field></div>}
    <CrewAccountPermissions googleValue={form.google_capabilities??[]} connectorValue={form.connector_capabilities??[]} onChange={(caps,connectors)=>setForm({...form,google_capabilities:caps,google_resources:caps.filter(c=>c.endsWith(":read")).map(c=>c.split(":")[0]) as GoogleResource[],connector_capabilities:connectors})}/>
    {form.template_id === "computer" && <label className="flex items-start gap-3 rounded-lg border border-line bg-panel p-3 text-xs"><input type="checkbox" checked={form.allow_device_metrics} onChange={(e) => setForm({ ...form, allow_device_metrics: e.target.checked })} className="mt-0.5 accent-[#007aff]" /><span><span className="font-medium text-zinc-200">Allow device metrics</span><span className="mt-1 block text-[11px] leading-5 text-zinc-500">Read CPU, memory, battery, and system-drive usage. Does not read files or change settings. Device checks use no model calls.</span></span></label>}
    {form.template_id==="computer"&&<div className="space-y-3 rounded-lg border border-line p-3"><Field label="Monitor"><select className="form-control" value={form.device_monitor?.kind??"system"} onChange={e=>setForm({...form,device_monitor:{drive:"system",notifications:"alerts",min_free_gb:5,...form.device_monitor,kind:e.target.value as "system"|"storage"}})}><option value="system">Computer health</option><option value="storage">Drive free space</option></select></Field>{form.device_monitor?.kind==="storage"&&<div className="grid grid-cols-2 gap-3"><Field label="Local drive"><input className="form-control" placeholder="C:" maxLength={3} value={form.device_monitor.drive} onChange={e=>setForm({...form,device_monitor:{...form.device_monitor!,drive:e.target.value}})}/></Field><Field label="Warn below (GB free)"><input className="form-control" type="number" min={0} max={10000} step="0.1" value={form.device_monitor.min_free_gb} onChange={e=>setForm({...form,device_monitor:{...form.device_monitor!,min_free_gb:Number(e.target.value)}})}/></Field></div>}<Field label="Desktop reports"><select className="form-control" value={form.device_monitor?.notifications??"alerts"} onChange={e=>setForm({...form,device_monitor:{kind:"system",drive:"system",min_free_gb:5,...form.device_monitor,notifications:e.target.value as "alerts"|"each_check"|"changes"}})}><option value="alerts">Only warnings and recovery</option><option value="each_check">Every check</option><option value="changes">When status changes</option></select></Field><p className="text-[11px] leading-5 text-zinc-500">Read-only counters. Reports appear on the desktop icon only when desktop updates below are enabled.</p></div>}
    <label className="flex items-center gap-2 text-xs text-zinc-400"><input type="checkbox" checked={form.allow_notifications??false} onChange={e=>setForm({...form,allow_notifications:e.target.checked})}/>Show updates on the desktop icon</label>
    <label className="flex items-start gap-3 rounded-lg border border-line bg-panel p-3 text-xs"><input type="checkbox" checked={form.allow_weather ?? false} onChange={e=>setForm({...form,allow_weather:e.target.checked})} className="mt-0.5"/><span><span className="font-medium text-zinc-200">Weather for any destination</span><span className="mt-1 block text-[11px] leading-5 text-zinc-500">Also enable Weather in Resources. No device location is required. Weather-assisted sessions need at least 2 model calls. Use scheduled reviews to monitor changing forecasts.</span></span></label>
    <p className="text-[11px] leading-5 text-zinc-500">{form.trigger_type === "daily" ? "Reminders use no model calls. A daily AM/PM time can also be detected from your instructions." : needed ? "No reminder timer is required. Mavo checks the connected resource about once per minute and uses a model only for matching changes. Existing items form a baseline, not new events." : "One model call per work cycle, or up to two with weather lookup. Manual work waits for another request; interval work continues only while useful steps remain."}</p>
    </div><div className="mt-6 flex items-center justify-between gap-5 border-t border-line pt-4"><p className="text-[11px] text-zinc-500">{existing?.is_running ? "Changes apply to the running Crew." : "Saved Crew start only when you select Run."}</p><div className="flex gap-2"><button type="button" className="btn-secondary" disabled={saving} onClick={onClose}>Cancel</button><button className="btn-primary" disabled={saving || !form.route_id || !form.name.trim() || !form.responsibilities.trim() || !!(needed && !form.google_resources?.includes(needed))}>{saving && <LoaderCircle size={13} className="animate-spin" />}{saving ? "Saving…" : existing ? "Save Changes" : "Save Crew"}</button></div></div></form></div></div>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block text-xs font-medium text-zinc-400">{label}{children}</label>; }
function formatTime(value: string) { const [h, m] = value.split(":").map(Number); return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`; }
