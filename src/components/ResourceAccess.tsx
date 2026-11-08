import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AccessLevel, PoolResource, ResourcePool } from "../types";

const LEVELS: { id: AccessLevel; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "read", label: "Read" },
  { id: "write", label: "Read & act" },
];

export function describeAccess(item: PoolResource): string {
  if (!item.connected) return item.kind === "google" || item.kind === "github" || item.kind === "feeds" ? "Not connected" : "Unavailable";
  if (item.level === "off") return "Mavo will not use this";
  if (item.level === "read" && !item.reads.length) return "Mavo will not use this";
  if (item.level === "read") return item.can_act ? "Crew can read. Actions are blocked." : "Crew can read.";
  const available = item.actions.filter(a => a.available);
  if (!item.reads.length) return available.length ? "Crew can use " + available.length + (available.length === 1 ? " action." : " actions.") : "No actions are available.";
  return available.length ? "Crew can read and use " + available.length + (available.length === 1 ? " action" : " actions") + "." : "Crew can read. No actions are connected yet.";
}

export function ResourceAccess() {
  const [pool, setPool] = useState<ResourcePool | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api.pool().then(value => { if (active) setPool(value); }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Could not load resources."); });
    return () => { active = false; };
  }, []);

  async function choose(item: PoolResource, level: AccessLevel) {
    if (busy || item.level === level) return;
    setBusy(item.id); setError(null);
    try {
      const saved = await api.setPoolLevel(item.id, level);
      setPool(p => p && { ...p, resources: p.resources.map(r => r.id === saved.id ? saved : r) });
    } catch (e) { setError(e instanceof Error ? e.message : "Could not change access."); }
    finally { setBusy(null); }
  }

  if (!pool) return error ? <p role="alert" className="notice error-notice">{error}</p> : <p role="status" className="flex items-center gap-2 text-xs text-zinc-500"><LoaderCircle size={13} className="animate-spin" />Loading resources…</p>;
  const groups = [...new Set(pool.resources.filter(r => r.kind !== "toggle").map(r => r.group))];
  return <div className="space-y-8">
    {error && <p role="alert" className="notice error-notice">{error}</p>}
    {groups.map(group => <section key={group} aria-label={group}>
      <h2 className="group-title">{group}</h2>
      <div className="list-card">
        {pool.resources.filter(r => r.group === group && r.kind !== "toggle").map(item => {
          const usable = item.connected;
          return <div className="list-row" key={item.id}>
            <div className="min-w-0 flex-1">
              <h3 className="text-[14px] font-medium text-zinc-50">{item.name}{item.account && <span className="ml-2 font-normal text-zinc-500">{item.account}</span>}</h3>
              <p className="mt-0.5 text-[12px] leading-5 text-zinc-500">{item.description} {describeAccess(item)}</p>
            </div>
            <div className="segmented" role="radiogroup" aria-label={item.name + " access"}>
              {LEVELS.filter(l => item.reads.length || l.id !== "read").map(l => {
                const unavailable = !usable || (l.id === "write" && !item.can_act);
                const label = !item.reads.length && l.id === "write" ? "On" : l.label;
                const selected = item.level === l.id || (!item.reads.length && l.id === "off" && item.level === "read");
                return <button key={l.id} type="button" role="radio" aria-checked={selected} aria-pressed={selected} disabled={unavailable || busy === item.id} onClick={() => void choose(item, l.id)}>{label}</button>;
              })}
            </div>
          </div>;
        })}
      </div>
    </section>)}
    <p className="text-[12px] leading-5 text-zinc-500">Read lets a Crew look. Read &amp; act lets it make the changes you allow in Actions.</p>

    {pool.open_data?.length ? <section aria-label="Open data sources">
      <h2 className="group-title">Open data · no key needed</h2>
      <p className="mb-3 text-[12px] leading-5 text-zinc-500">Any Crew can look these up while Open data is on. Only the place, word or code it asks about is sent.</p>
      {[...new Set(pool.open_data.map(s => s.group))].map(group => <div key={group} className="mb-4">
        <h3 className="od-group">{group}</h3>
        <div className="od-grid">{(pool.open_data ?? []).filter(s => s.group === group).map(s =>
          <div key={s.id} className="od-tile" title={"Sends: " + s.sends}><b>{s.name}</b><span>{s.gives}</span></div>)}</div>
      </div>)}
    </section> : null}

    <section aria-label="Open source">
      <h2 className="group-title">Coming later</h2>
      <div className="list-card">
        {pool.community.map(item => <div className="list-row" key={item.id}>
          <div className="min-w-0 flex-1"><h3 className="text-[14px] font-medium text-zinc-50">{item.name}</h3><p className="mt-0.5 text-[12px] text-zinc-500">{item.adds}</p></div>
          <span className="text-[12px] text-zinc-600">Open source</span>
        </div>)}
      </div>
    </section>
  </div>;
}
