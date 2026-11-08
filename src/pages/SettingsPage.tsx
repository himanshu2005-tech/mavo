import { CheckCircle2, Droplet, Edit3, KeyRound, LoaderCircle, Palette, Plus, Power, Route, Trash2, X, Zap } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { PageHeader } from "../components/PageHeader";
import { IconTile } from "../components/Studio";
import { CoreServiceCard } from "../components/CoreServiceCard";
import { FirebaseLoginCard } from "../components/FirebaseLoginCard";
import { api } from "../lib/api";
import { ACCENTS, chooseAccent, chooseTheme, storedAccent, storedTheme, type AccentChoice, type ThemeChoice } from "../lib/theme";
import { useAppStore } from "../store/app";
import type { ModelRoute, ModelRouteInput, ProviderId, RouteTest } from "../types";

const providerNames: Record<ProviderId, string> = { ollama: "Ollama", openai: "OpenAI", anthropic: "Anthropic Claude", openrouter: "OpenRouter", airouter: "AIRouter" };

const THEMES: { id: ThemeChoice; label: string }[] = [{ id: "auto", label: "Match Windows" }, { id: "light", label: "Light" }, { id: "dark", label: "Dark" }];

function Appearance() {
  const [choice, setChoice] = useState<ThemeChoice>(storedTheme);
  const [accent, setAccent] = useState<AccentChoice>(storedAccent);
  return <div className="list-card">
    <div className="list-row">
      <IconTile icon={Palette} hue={322} />
      <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Appearance</p><p className="text-[12px] text-zinc-500">Light, dark, or whatever Windows is using.</p></div>
      <div className="segmented" role="group" aria-label="Appearance">{THEMES.map(t => <button key={t.id} type="button" aria-pressed={choice === t.id} onClick={() => { setChoice(t.id); chooseTheme(t.id); }}>{t.label}</button>)}</div>
    </div>
    <div className="list-row">
      <IconTile icon={Droplet} hue={32} />
      <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Accent</p><p className="text-[12px] text-zinc-500">The colour of buttons and switches.</p></div>
      <div className="swatches" role="group" aria-label="Accent colour">{ACCENTS.map(a => <button key={a.id} type="button" className="swatch" style={{ background: a.swatch }} aria-label={a.label} title={a.label} aria-pressed={accent === a.id} onClick={() => { setAccent(a.id); chooseAccent(a.id); }} />)}</div>
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

export function SettingsPage() {
  const routes = useAppStore((state) => state.routes);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<ModelRoute | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, RouteTest | string>>({});

  async function test(route: ModelRoute) {
    setTesting(route.id);
    setTests((previous) => { const next = { ...previous }; delete next[route.id]; return next; });
    try { const result = await api.testRoute(route.id); setTests((previous) => ({ ...previous, [route.id]: result })); }
    catch (reason) { setTests((previous) => ({ ...previous, [route.id]: reason instanceof Error ? reason.message : "Connection failed" })); }
    finally { setTesting(null); }
  }

  async function remove(route: ModelRoute) {
    if (!window.confirm(`Delete the route “${route.name}”?`)) return;
    setBusyId(route.id); setError(null);
    try {
      await api.deleteRoute(route.id);
      useAppStore.getState().removeRoute(route.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete route.");
    } finally { setBusyId(null); }
  }

  return <>
    <PageHeader eyebrow="Settings" title="Settings" description="Account and agent connections." action={<button onClick={() => { setEditing(null); setShowForm(true); }} className="btn-primary"><Plus size={14} /> Add connection</button>} />
    <div className="page-content space-y-5">
      <Appearance />
      <StartWithWindows />
      <FirebaseLoginCard />
      <CoreServiceCard />
      {error && <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] p-3 text-xs text-rose-200">{error}</div>}
      {showForm && <RouteForm key={editing?.id ?? "new"} existing={editing} onClose={() => { setShowForm(false); setEditing(null); }} />}
      {!routes.length && !showForm && <div className="rounded-2xl border border-dashed border-line p-10 text-center"><Route className="mx-auto text-zinc-700" /><h2 className="mt-3 text-sm text-zinc-300">No agent connections</h2><p className="mt-1 text-xs text-zinc-600">Crew work needs one.</p></div>}
      <div className="grid grid-cols-2 gap-4">{routes.map((route) => <section key={route.id} className="surface crew-card p-6">
        <div className="flex items-start justify-between"><div className="flex gap-3"><IconTile icon={Route} hue={({ openai: 160, anthropic: 20, ollama: 200, openrouter: 270, airouter: 322 } as Record<string, number>)[route.provider] ?? 250} /><div><h2 className="text-sm font-medium text-zinc-100">{route.name}</h2><p className="mt-1 text-[11px] text-zinc-600">{providerNames[route.provider]}</p></div></div><span className={`rounded-full px-2 py-1 text-[10px] ${route.has_credential ? "bg-mint/10 text-mint" : "bg-rose-400/10 text-rose-300"}`}>{route.has_credential ? "Configured" : "Key missing"}</span></div>
        <div className="mt-4 truncate rounded-lg bg-ink/50 px-3 py-2 text-xs text-zinc-400">{route.model}</div>
        {tests[route.id] && <div role="status" className={`mt-4 rounded-xl border p-3 text-xs leading-5 ${typeof tests[route.id] === "string" ? "border-rose-400/20 bg-rose-400/5 text-rose-200" : "border-mint/20 bg-mint/5 text-mint"}`}>{typeof tests[route.id] === "string" ? String(tests[route.id]) : <><span className="flex items-center gap-2"><CheckCircle2 size={13} />Connected · {((tests[route.id] as RouteTest).latency_ms / 1000).toFixed(1)}s</span><p className="mt-1 text-zinc-400">{(tests[route.id] as RouteTest).response}</p></>}</div>}
        <div className="mt-4 flex items-center justify-between border-t border-line pt-4"><button className="btn-secondary !py-2" disabled={testing !== null || !route.has_credential} onClick={() => void test(route)}>{testing === route.id ? <LoaderCircle size={13} className="animate-spin" /> : <Zap size={13} />}{testing === route.id ? "Testing…" : "Test connection"}</button><div className="flex gap-1"><button aria-label={`Edit ${route.name}`} onClick={() => { setEditing(route); setShowForm(true); }} className="btn-quiet !px-2"><Edit3 size={13} /> Edit</button><button aria-label={`Delete ${route.name}`} disabled={busyId === route.id} onClick={() => void remove(route)} className="btn-quiet !px-2 hover:!text-rose-300"><Trash2 size={13} /></button></div></div>
      </section>)}</div>
      <p className="px-1 text-[12px] leading-5 text-zinc-600">Keys are encrypted by Windows. Chats and Crew sessions stay on this computer.</p>
    </div>
  </>;
}

function RouteForm({ existing, onClose }: { existing: ModelRoute | null; onClose: () => void }) {
  const status = useAppStore((state) => state.status);
  const [form, setForm] = useState<ModelRouteInput>({ name: existing?.name ?? "", provider: existing?.provider ?? "openai", model: existing?.model ?? "", api_key: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const provider = status?.providers.find((item) => item.id === form.provider);
  const needsKey = form.provider !== "ollama";

  function changeProvider(value: ProviderId) {
    const next = status?.providers.find((item) => item.id === value);
    setForm({ ...form, provider: value, model: next?.default_model ?? next?.models[0]?.name ?? "", api_key: "" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      const payload = { ...form };
      if (!payload.api_key) delete payload.api_key;
      const route = existing ? await api.updateRoute(existing.id, payload) : await api.createRoute(payload);
      useAppStore.getState().upsertRoute(route);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save route.");
    } finally { setSaving(false); }
  }

  return <form onSubmit={submit} className="workspace-form">
    <div className="flex items-start justify-between"><div><div className="text-[10px] uppercase tracking-[0.18em] text-mint">Model route</div><h2 className="mt-1 text-lg font-medium text-white">{existing ? `Edit ${existing.name}` : "Add a route"}</h2></div><button type="button" onClick={onClose} className="rounded-lg p-2 text-zinc-600 hover:bg-white/5"><X size={16} /></button></div>
    {error && <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.06] p-3 text-xs text-rose-200">{error}</div>}
    <div className="mt-5 grid grid-cols-2 gap-4">
      <label className="text-[11px] font-medium text-zinc-400">Route name<input autoFocus value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="e.g. Fast OpenAI" className="form-control" required /></label>
      <label className="text-[11px] font-medium text-zinc-400">Provider<select value={form.provider} onChange={(event) => changeProvider(event.target.value as ProviderId)} className="form-control">{Object.entries(providerNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label className="text-[11px] font-medium text-zinc-400">Exact model ID<input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} list="route-models" placeholder="e.g. gpt-5-mini" className="form-control" required /><datalist id="route-models">{provider?.models.map((model) => <option key={model.name} value={model.name} />)}</datalist></label>
      {needsKey ? <label className="text-[11px] font-medium text-zinc-400">API key<input type="password" autoComplete="off" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={existing ? "Leave blank to keep saved key" : `Paste your ${providerNames[form.provider]} key`} className="form-control" required={!existing || existing.provider !== form.provider} minLength={8} /></label> : <div className="rounded-xl border border-line bg-ink/40 p-3 text-[11px] leading-5 text-zinc-500">Ollama routes use your local runtime and do not need an API key.</div>}
    </div>
    <div className="mt-5 flex items-center justify-between border-t border-line pt-5"><div className="flex items-center gap-2 text-[10px] text-zinc-600"><KeyRound size={13} />One credential, one provider, one exact model.</div><div className="flex gap-2"><button type="button" onClick={onClose} className="px-4 py-2.5 text-xs text-zinc-500">Cancel</button><button disabled={saving || !form.name.trim() || !form.model.trim() || (needsKey && !existing && !form.api_key)} className="btn-primary">{saving ? "Saving…" : "Save route"}</button></div></div>
  </form>;
}
