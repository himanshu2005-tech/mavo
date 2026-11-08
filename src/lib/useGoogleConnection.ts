import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useState } from "react";
import type { GoogleCapability, GoogleResource, GoogleStatus } from "../types";
import { api } from "./api";

export function useGoogleConnection(enabled = true) {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    const value = await api.googleStatus(); setStatus(value); return value;
  }, []);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    void api.googleStatus().then((value) => { if (active) setStatus(value); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [enabled]);
  const pending = status?.phase === "pending" || status?.phase === "exchanging";
  useEffect(() => {
    if (!enabled || !pending) return;
    let active = true; let fetching = false;
    const timer = window.setInterval(() => {
      if (fetching) return; fetching = true;
      void api.googleStatus().then((value) => { if (active) setStatus(value); })
        .catch((reason: Error) => { if (active) setError(reason.message); })
        .finally(() => { fetching = false; });
    }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [enabled, pending]);
  async function connect(resources: GoogleResource[] | "all" | { capabilities: GoogleCapability[] }) {
    setBusy(true); setError(null);
    try {
      const result = await (resources === "all" ? api.connectAllGoogle() : Array.isArray(resources) ? api.signInGoogle(resources) : api.connectGoogleCapabilities(resources.capabilities)); await refresh();
      await invoke("open_google_url", { url: result.authorization_url });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not open Google sign-in."); }
    finally { setBusy(false); }
  }
  async function cancel() {
    setBusy(true); setError(null);
    try { setStatus(await api.cancelGoogleSignIn()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not cancel sign-in."); }
    finally { setBusy(false); }
  }
  return { status, busy, pending, error, setError, refresh, connect, cancel };
}
