import { LoaderCircle, Palette, Plus, Power, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { PageHeader } from "../components/PageHeader";
import { IconTile } from "../components/Studio";
import { CoreServiceCard } from "../components/CoreServiceCard";
import { FirebaseLoginCard } from "../components/FirebaseLoginCard";
import { DesktopAlertTest } from "../components/DesktopAlertTest";
import { api } from "../lib/api";
import { chooseTheme, storedTheme, type ThemeChoice } from "../lib/theme";
import { useAppStore } from "../store/app";
import type { ModelRoute, ModelRouteInput, ProviderId } from "../types";

const providerNames: Record<ProviderId, string> = { ollama: "Ollama", openai: "OpenAI", anthropic: "Anthropic Claude", openrouter: "OpenRouter", airouter: "AIRouter" };

const THEMES: { id: ThemeChoice; label: string }[] = [{ id: "auto", label: "Match Windows" }, { id: "light", label: "Light" }, { id: "dark", label: "Dark" }];

/** How the person hears from their agents: Mavo holds routine reports and sends one summary
 *  every so often; questions, alerts and timed plans still come at once. */
function MavoSummary(){
  const [value,setValue]=useState<{digest_minutes:number;enabled:boolean}|null>(null),[error,setError]=useState<string|null>(null);
  useEffect(()=>{let live=true;void api.attention().then(v=>{if(live)setValue(v);}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[]);
  async function save(change:{digest_minutes?:number;enabled?:boolean}){setError(null);try{setValue(await api.saveAttention(change));}catch(e){setError(String(e));}}
  if(!value)return error?<section className="list-card p-5 text-xs text-rose-300">{error}</section>:null;
  return <section className="list-card p-5"><label className="text-sm" htmlFor="mavo-summary">Hear from your agents through Mavo</label>
    <p className="text-xs text-zinc-500 my-2">Routine reports wait and come as one message from Mavo. Questions, approvals, failures, mail alerts and plans at a set time still reach you at once.</p>
    <div className="flex items-center gap-3"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={value.enabled} onChange={e=>void save({enabled:e.target.checked})}/>Send a summary</label>
    <select id="mavo-summary" className="field !w-auto" disabled={!value.enabled} value={value.digest_minutes} onChange={e=>void save({digest_minutes:Number(e.target.value)})}>
      {[15,30,60,120,240].map(m=><option key={m} value={m}>{m<60?`every ${m} min`:`every ${m/60} h`}</option>)}
      {![15,30,60,120,240].includes(value.digest_minutes)&&<option value={value.digest_minutes}>every {value.digest_minutes} min</option>}
    </select></div>
    {error&&<p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
  </section>;
}

/** Which model Mavo itself plans with when you chat. */
function PlanningConnection(){
  const routes=useAppStore(s=>s.routes);
  const [selected,setSelected]=useState<string|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  useEffect(()=>{let live=true;void api.planningRoute().then(r=>{if(live)setSelected(r.route_id);}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[]);
  async function save(value:string){setBusy(true);setError(null);try{setSelected((await api.setPlanningRoute(value||null)).route_id);}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="model-planning"><label htmlFor="planning-connection">Chat uses</label>
    <select id="planning-connection" className="field !w-auto" value={selected??''} disabled={busy} onChange={e=>void save(e.target.value)}><option value="">Mavo Core</option>{routes.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select>{error&&<p className="notice error-notice" role="alert">{error}</p>}
  </div>;
}

function Appearance() {
  const [choice, setChoice] = useState<ThemeChoice>(storedTheme);
  return <div className="list-card">
    <div className="list-row">
      <IconTile icon={Palette} hue={322} />
      <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Appearance</p><p className="text-[12px] text-zinc-500">Light, dark, or follow Windows.</p></div>
      <div className="segmented" role="group" aria-label="Appearance">{THEMES.map(t => <button key={t.id} type="button" aria-pressed={choice === t.id} onClick={() => { setChoice(t.id); chooseTheme(t.id); }}>{t.label}</button>)}</div>
    </div>
  </div>;
}

/** Keep Crew working after a restart: Mavo starts when you sign in to Windows. */
function StartWithWindows() {
  const [state, setState] = useState<{ supported: boolean; enabled: boolean; mode: "app" | "development" | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; api.autostart().then(s => { if (active) setState(s); }).catch(() => { if (active) setState(null); }); return () => { active = false; }; }, []);
  if (!state) return null;
  async function change() {
    if (!state) return;
    setBusy(true); setError(null);
    try { setState(await api.setAutostart(!state.enabled)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change that."); }
    finally { setBusy(false); }
  }
  const hint = !state.supported ? "Not available in this copy of Mavo."
    : state.enabled ? (state.mode === "development" ? "On. After a restart, the assistant service and Mavo start by themselves; give it a minute or two." : "On. Your Crew keep working after a restart.")
    : "Off. After a restart, Crew wait until you open Mavo.";
  return <div className="list-card"><div className="list-row">
    <IconTile icon={Power} hue={158} />
    <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Start Mavo when I sign in to Windows</p><p className="text-[12px] text-zinc-500">{hint}</p></div>
    <button type="button" role="switch" aria-checked={state.enabled} aria-label="Start with Windows" disabled={busy || !state.supported} className="resource-switch" onClick={() => void change()}><span /></button>
  </div>{error && <p role="alert" className="notice error-notice m-3">{error}</p>}</div>;
}

/** Start fresh: every agent and draft deleted, one by one, as each one's own Delete does (it is
 *  stopped, its waiting work cancelled, its helpers removed). Asks first; cannot be undone. */
export function DeleteAllAgents() {
  const crews = useAppStore(s => s.crew);
  const drafts = useAppStore(s => s.setupDrafts);
  const all = [...crews, ...drafts.filter(d => !crews.some(c => c.id === d.id))];
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!all.length) return null;
  async function remove() {
    setError(null);
    const failed: string[] = [];
    for (const crew of all) {
      setBusy(crew.name);
      try { await api.deleteCrew(crew.id); } catch { failed.push(crew.name); }
    }
    try { useAppStore.getState().setCrew(await api.crew()); } catch { /* the list refreshes on its own */ }
    useAppStore.setState({ setupDrafts: [] } as never);
    try { localStorage.removeItem("mavo-room"); } catch { /* fine */ }
    setBusy(null); setAsking(false);
    if (failed.length) setError(`Could not delete: ${failed.join(", ")}. Try again.`);
  }
  const live = all.filter(c => c.status !== "draft").length, setting = all.length - live;
  const what = [live ? `${live} agent${live === 1 ? "" : "s"}` : "", setting ? `${setting} draft${setting === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");
  return <div className="list-card"><div className="list-row">
    <IconTile icon={Trash2} hue={350} />
    <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Delete all agents</p>
      <p className="text-[12px] text-zinc-500">{asking ? `Delete ${what}, with their history and reports? This can't be undone.` : `Start fresh: removes ${what}.`}</p></div>
    {asking ? <div className="flex gap-2">
      <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => setAsking(false)}>Cancel</button>
      <button type="button" className="btn-primary btn-danger" disabled={!!busy} onClick={() => void remove()}>{busy ? <><LoaderCircle size={13} className="animate-spin" />Deleting {busy}…</> : "Delete all"}</button>
    </div> : <button type="button" className="btn-secondary" onClick={() => setAsking(true)}>Delete all…</button>}
  </div>{error && <p role="alert" className="notice error-notice m-3">{error}</p>}</div>;
}

export function SettingsPage() {
  return <>
    <PageHeader eyebrow="Settings" title="Settings" description="Models, look and startup." />
    <div className="page-content space-y-5">
      <Models />
      <Appearance />
      <StartWithWindows />
      <DesktopAlertTest />
      <FirebaseLoginCard />
      <CoreServiceCard />
      <MavoSummary/>
      <DeleteAllAgents />
    </div>
  </>;
}

/** The models agents think with: a plain list, Add, and a test per row. */
function Models() {
  const routes = useAppStore((state) => state.routes);
  const [form, setForm] = useState<ModelRoute | "new" | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; text: string }>>({});

  async function test(route: ModelRoute) {
    setBusyId(route.id);
    setTests((previous) => { const next = { ...previous }; delete next[route.id]; return next; });
    try { const result = await api.testRoute(route.id); setTests((previous) => ({ ...previous, [route.id]: { ok: true, text: `Works · ${(result.latency_ms / 1000).toFixed(1)}s` } })); }
    catch (reason) { setTests((previous) => ({ ...previous, [route.id]: { ok: false, text: reason instanceof Error ? reason.message : "Didn't reply" } })); }
    finally { setBusyId(null); }
  }

  async function remove(route: ModelRoute) {
    if (!window.confirm(`Remove ${route.name}?`)) return;
    setBusyId(route.id); setError(null);
    try { await api.deleteRoute(route.id); useAppStore.getState().removeRoute(route.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not remove it."); }
    finally { setBusyId(null); }
  }

  return <section className="models" aria-label="Models">
    <div className="models-head"><h2>Models</h2>{form === null && <button className="btn-quiet" onClick={() => setForm("new")}><Plus size={14} />Add</button>}</div>
    {form !== null && <RouteForm key={form === "new" ? "new" : form.id} existing={form === "new" ? null : form} onClose={() => setForm(null)} />}
    {!routes.length && form === null && <p className="models-empty">No models yet. Add one so your agents can think.</p>}
    <ul>{routes.map((route) => <li key={route.id} className="model-row">
      <i data-ok={route.has_credential} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p>{route.name}</p>
        <small>{providerNames[route.provider]} · {route.model}{!route.has_credential && " · key missing"}</small>
        {tests[route.id] && <small role="status" data-ok={tests[route.id].ok} className="model-test">{tests[route.id].text}</small>}
      </div>
      <button className="btn-quiet" disabled={busyId !== null || !route.has_credential} onClick={() => void test(route)}>{busyId === route.id ? <LoaderCircle size={13} className="animate-spin" /> : "Test"}</button>
      <button className="btn-quiet" aria-label={`Edit ${route.name}`} onClick={() => setForm(route)}>Edit</button>
      <button className="btn-quiet !px-2" aria-label={`Delete ${route.name}`} disabled={busyId === route.id} onClick={() => void remove(route)}><Trash2 size={13} /></button>
    </li>)}</ul>
    {routes.length > 0 && <PlanningConnection />}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
    <p className="models-note">Keys are encrypted by Windows and stay on this computer.</p>
  </section>;
}

export function RouteForm({ existing, onClose }: { existing: ModelRoute | null; onClose: () => void }) {
  const status = useAppStore((state) => state.status);
  const [form, setForm] = useState<ModelRouteInput>({ name: existing?.name ?? "", provider: existing?.provider ?? "openai", model: existing?.model ?? "", api_key: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const provider = status?.providers.find((item) => item.id === form.provider);
  const needsKey = form.provider !== "ollama";
  const keyRequired = needsKey && (!existing || existing.provider !== form.provider);

  function changeProvider(value: ProviderId) {
    const next = status?.providers.find((item) => item.id === value);
    setForm({ ...form, provider: value, model: next?.default_model ?? next?.models[0]?.name ?? "", api_key: "" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      const payload = { ...form, name: form.name.trim() || form.model.trim() };
      if (!payload.api_key) delete payload.api_key;
      const route = existing ? await api.updateRoute(existing.id, payload) : await api.createRoute(payload);
      useAppStore.getState().upsertRoute(route);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save it.");
    } finally { setSaving(false); }
  }

  return <form onSubmit={submit} className="model-form">
    <label>Provider<select aria-label="Provider" value={form.provider} onChange={(event) => changeProvider(event.target.value as ProviderId)} className="form-control">{Object.entries(providerNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
    <label>Model<input autoFocus value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} list="route-models" placeholder="e.g. gpt-5-mini" className="form-control" required /><datalist id="route-models">{provider?.models.map((model) => <option key={model.name} value={model.name} />)}</datalist></label>
    {needsKey && <label>API key<input type="password" autoComplete="off" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={keyRequired ? `Your ${providerNames[form.provider]} key` : "Leave blank to keep the saved key"} className="form-control" required={keyRequired} minLength={8} /></label>}
    <label>Name (optional)<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder={form.model || "e.g. Fast"} className="form-control" /></label>
    {error && <p role="alert" className="notice error-notice">{error}</p>}
    <div className="model-form-actions"><button type="button" onClick={onClose} className="btn-quiet">Cancel</button><button disabled={saving || !form.model.trim() || (keyRequired && !form.api_key)} className="btn-primary">{saving ? "Saving…" : existing ? "Save" : "Add model"}</button></div>
  </form>;
}
