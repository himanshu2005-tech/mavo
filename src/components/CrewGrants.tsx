import { useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";
import { Row, Switch } from "./CrewSetupCard";

type Grant = { key: string; title: string; hint: string; change: Parameters<typeof api.updateCrew>[1] };

/** What a stopped Crew is waiting to be allowed, read from its message ("Allow device metrics in
 *  Edit to monitor this computer."), as switches right under it: one tap allows it and checks
 *  again, instead of finding the setting in Edit. */
export function grantsFor(crew: Pick<CrewInstance, "last_runtime_error" | "question" | "permissions">): Grant[] {
  const said = `${crew.last_runtime_error ?? ""} ${crew.question ?? ""}`;
  const has = (p: string) => (crew.permissions ?? []).includes(p);
  const grants: Grant[] = [];
  if (/device metrics/i.test(said) && !has("device:read"))
    grants.push({ key: "device", title: "Read device metrics", hint: "CPU, memory, battery and storage. Read-only.", change: { allow_device_metrics: true } });
  if (/cleaning temp files/i.test(said) && !has("device:clean_temp"))
    grants.push({ key: "clean", title: "Free space on this computer", hint: "Deletes only old temp files (and empties the Recycle Bin if you asked). Nothing else.", change: { allow_temp_cleanup: true } });
  if (/\bweather\b/i.test(said) && /allow|enable|grant/i.test(said) && !has("weather:read"))
    grants.push({ key: "weather", title: "Read the weather", hint: "Forecasts only, for the places it needs.", change: { allow_weather: true } });
  if (/notification|desktop alert/i.test(said) && /allow|enable|turn on/i.test(said) && !has("notifications:show"))
    grants.push({ key: "notify", title: "Tell me on my desktop", hint: "Its results show on the Mavo icon.", change: { allow_notifications: true } });
  return grants;
}

export function CrewGrants({ crew }: { crew: CrewInstance }) {
  const grants = grantsFor(crew);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!grants.length) return null;
  async function allow(grant: Grant) {
    setBusy(grant.key); setError(null);
    try {
      let updated = await api.updateCrew(crew.id, grant.change);
      // Allowed: look again now, so it starts working instead of waiting for its next turn.
      try { updated = await api.checkCrew(crew.id); } catch { /* it runs at its next check */ }
      useAppStore.getState().upsertCrew(updated);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not allow it."); }
    finally { setBusy(null); }
  }
  return <div className="list-card mt-3" aria-label="What it needs">
    {grants.map(g => <Row key={g.key} title={g.title} hint={g.hint}><Switch on={false} disabled={busy !== null} onChange={() => void allow(g)} /></Row>)}
    {error && <p role="alert" className="notice error-notice mt-2">{error}</p>}
  </div>;
}
