import { useEffect, useRef, useState } from "react";
import { CrewArt, HUES } from "../components/CrewArt";
import { GoogleAccountCard } from "../components/GoogleAccountCard";
import { ConnectorAccounts } from "../components/ConnectorAccounts";
import { PageHeader } from "../components/PageHeader";
import { BrowserCard } from "../components/BrowserCard";
import { ResourceAccess } from "../components/ResourceAccess";
import { api } from "../lib/api";
import { detectDeviceLocation } from "../lib/geolocation";
import type { WeatherLocation, WeatherSnapshot } from "../types";

type ResourceAccess = {weather: boolean; location: boolean};

export function ResourcesPage() {
  const [access, setAccess] = useState<ResourceAccess | null>(null);
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [device, setDevice] = useState<WeatherLocation | null>(null);
  const [weather, setWeather] = useState<WeatherSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<ResourceAccess | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const pendingTicket = useRef<number | null>(null);
  const lastDetection = useRef(0);

  async function detect(policy: ResourceAccess, force = false) {
    if (!policy.location || !active.current) return;
    if (pendingTicket.current === generation.current) return;
    if (!force && Date.now() - lastDetection.current < 300000) return;
    const ticket = ++generation.current;
    pendingTicket.current = ticket; lastDetection.current = Date.now();
    setDetecting(true); setError(null);
    try {
      const location = await detectDeviceLocation();
      if (!active.current || !current.current?.location || generation.current !== ticket) return;
      const saved = await api.saveDeviceLocation(location);
      if (!active.current || generation.current !== ticket) return;
      setDevice(saved);
      if (current.current?.weather) {
        try {
          const forecast = await api.weatherForecast(saved);
          if (active.current && generation.current === ticket && current.current?.weather) setWeather(forecast);
        } catch (e) {
          if (current.current?.weather) throw e;
        }
      }
    } catch (e) {
      if (active.current && generation.current === ticket) {
        lastDetection.current = 0;
        setError(e instanceof Error ? e.message : "Could not detect your location.");
      }
    } finally {
      if (pendingTicket.current === ticket) pendingTicket.current = null;
      if (active.current && generation.current === ticket) setDetecting(false);
    }
  }

  useEffect(() => {
    active.current = true;
    let cancelled = false;
    void api.resourceAccess().then(value => {
      if (cancelled || !active.current) return;
      current.current = value; setAccess(value);
      if (value.location) void detect(value, true);
    }).catch((e: Error) => { if (!cancelled && active.current) setError(e.message); });
    function refresh() {
      if (document.visibilityState === "visible" && current.current?.location) void detect(current.current);
    }
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      cancelled = true; active.current = false; generation.current++; pendingTicket.current = null;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  async function toggle(resource: "weather" | "location") {
    if (!access || saving) return;
    setSaving(true); setError(null);
    try {
      const saved = await api.setResourceAccess({...access, [resource]: !access[resource]});
      current.current = saved; setAccess(saved);
      if (!saved.weather) setWeather(null);
      if (!saved.location) {
        generation.current++; pendingTicket.current = null;
        setDevice(null); setDetecting(false); setWeather(null);
      }
      if (saved.location && saved[resource]) void detect(saved, true);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save resource access."); }
    finally { setSaving(false); }
  }

  const locationDetail = detecting ? "Detecting automatically from this computer..." : device
    ? `Detected automatically on this computer${device.accuracy_m ? ` (about ${Math.round(device.accuracy_m)} m accuracy)` : ""}.`
    : access?.location ? "Windows location access is required for automatic detection." : "Use this computer's location automatically when allowed.";

  return <>
    <PageHeader eyebrow="Workspace" title="Resources" description="What Mavo can use." />
    <div className="page-content masonry">
      <section aria-label="Default resources">
        <h2 className="group-title">On this computer</h2>
        <div className="tile-grid">
          <ResourceTile scene="travel" title="Weather" description={"Forecasts for any destination." + (weather && access?.weather ? ` Near you: ${Math.round(weather.current.temperature_c)}°` : "")} enabled={access?.weather ?? false} disabled={!access || saving} onToggle={() => void toggle("weather")} />
          <ResourceTile scene="home" busy={detecting} title="Location" description={locationDetail} enabled={access?.location ?? false} disabled={!access || saving} onToggle={() => void toggle("location")} />
        </div>
        {!access && !error && <p role="status" className="mt-3 text-xs text-zinc-500">Loading resources...</p>}
      </section>
      <section aria-label="Accounts" className="space-y-4">
        <h2 className="group-title">Accounts</h2>
        <BrowserCard />
        <GoogleAccountCard />
        <ConnectorAccounts />
      </section>
      <section aria-label="Access"><h2 className="group-title">Access</h2><ResourceAccess /></section>
      {error && <p role="alert" className="notice error-notice">{error}</p>}
    </div>
  </>;
}

/** A resource as a card with its own little picture; the switch is the whole decision. */
function ResourceTile({scene, title, description, enabled, disabled, onToggle, busy = false}: {
  scene: string; title: string; description: string; enabled: boolean;
  disabled: boolean; onToggle: () => void; busy?: boolean;
}) {
  return <div className="tile lift" data-on={enabled} style={{["--hue" as string]: HUES[scene]}}>
    <div className="tile-art" aria-hidden="true"><CrewArt template={scene} /></div>
    <div className="tile-body">
      <div className="min-w-0 flex-1"><h3>{title}</h3><p role={busy ? "status" : undefined}>{description}</p></div>
      <button type="button" role="switch" aria-label={title + " access"} aria-checked={enabled} className="resource-switch" disabled={disabled} onClick={onToggle}><span /></button>
    </div>
  </div>;
}
