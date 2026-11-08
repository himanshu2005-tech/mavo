import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, LoaderCircle, Rss } from "lucide-react";
import { api } from "../lib/api";
import type { ConnectorStatus } from "../types";
import { useAppStore } from "../store/app";
import { GitHubSignIn } from "./GitHubSignIn";
export { ConnectorPermissions, connectorLabels } from "./ConnectorPermissions";

export function ConnectorAccounts() {
  const [providers, setProviders] = useState<ConnectorStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const load = useCallback(async () => {
    const ticket = ++generation.current;
    try {
      const value = await api.connectors();
      if (active.current && ticket === generation.current) { setProviders(value); setError(null); }
    } catch (e) { if (active.current && ticket === generation.current) setError(String(e)); }
  }, []);
  useEffect(() => {
    active.current = true;
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => { active.current = false; generation.current++; window.clearInterval(timer); };
  }, [load]);
  return <>
    {providers.filter(p => p.provider === "github").map(provider => <GitHubSignIn key={provider.provider} provider={provider} onChanged={() => void load()} />)}
    {providers.filter(p => p.provider === "feeds").map(provider => <FeedAccount key={provider.provider} provider={provider} onChanged={() => void load()} />)}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </>;
}

export function FeedAccount({ provider, onChanged }: { provider: ConnectorStatus; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [sources, setSources] = useState(provider.sources ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saved = JSON.stringify(provider.sources ?? []);
  useEffect(() => { setSources(JSON.parse(saved) as string[]); }, [saved]);
  async function save() {
    setBusy(true); setError(null);
    try { await api.connectFeeds(sources); setExpanded(false); onChanged(); useAppStore.getState().setCrew(await api.crew()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save feed access"); }
    finally { setBusy(false); }
  }
  const categories = [...new Set((provider.available_sources ?? []).map(s => s.category ?? "Other"))];
  return <section className="surface p-6" aria-label="Public feeds connection">
    <div className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-3"><span className="crew-icon"><Rss size={18} /></span><div><h2 className="text-sm font-semibold">Public feeds</h2><p className="mt-1 text-xs text-zinc-500">News, research and technology.</p></div></div>
      <span className="badge">{provider.connected ? `${provider.sources?.length ?? 0} enabled` : "Off"}</span>
    </div>
    <button className="btn-secondary mt-4" disabled={busy} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>Choose feeds<ChevronDown size={14} className={"transition-transform " + (expanded ? "rotate-180" : "")} /></button>
    {expanded && <div className="mt-4 space-y-5 border-t border-line pt-4">
      {categories.map(category => <div key={category}><h3 className="mb-2 text-[12px] font-semibold text-zinc-500">{category}</h3><div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">{(provider.available_sources ?? []).filter(s => (s.category ?? "Other") === category).map(source => <label key={source.id} className="flex items-center gap-3 text-[13px] text-zinc-300"><input type="checkbox" checked={sources.includes(source.id)} onChange={e => setSources(e.target.checked ? [...sources, source.id] : sources.filter(id => id !== source.id))} />{source.name}</label>)}</div></div>)}
      <p className="text-xs leading-5 text-zinc-500">20 recent headlines per feed.</p>
      <button className="btn-primary" disabled={busy} onClick={() => void save()}>{busy && <LoaderCircle size={13} className="animate-spin" />}Save</button>
    </div>}
    {error && <p role="alert" className="notice error-notice mt-3">{error}</p>}
  </section>;
}
