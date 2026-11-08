import { invoke } from "@tauri-apps/api/core";
import { Github, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { ConnectorStatus, GitHubAuthStatus } from "../types";
import { connectorLabels } from "./ConnectorPermissions";

const openGitHubUrl = (url: string) => invoke<void>("open_github_url", { url });

export function GitHubSignIn({ provider, onChanged, openUrl = openGitHubUrl }: {
  provider: ConnectorStatus; onChanged: () => void; openUrl?: (url: string) => Promise<void>;
}) {
  const [status, setStatus] = useState<GitHubAuthStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repositories, setRepositories] = useState<string[]>([]);
  const active = useRef(true);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const lastPhase = useRef<string | null>(null);
  const installationOpened = useRef(false);
  const pending = !!status && ["starting", "pending", "exchanging", "installing"].includes(status.phase);

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let mounted = true;
    let fetching = false;
    async function refresh() {
      if (fetching) return;
      fetching = true;
      try {
        const value = await api.githubAuth();
        if (!mounted) return;
        setStatus(value);
        if (value.phase === "complete" && lastPhase.current !== "complete") {
          onChangedRef.current();
          const crews = await api.crew();
          if (mounted && active.current) useAppStore.getState().setCrew(crews);
        }
        lastPhase.current = value.phase;
      } catch (e) { if (mounted) setError(e instanceof Error ? e.message : "Could not check GitHub."); }
      finally { fetching = false; }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), pending ? 1000 : 5000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [pending]);

  useEffect(() => {
    if (status?.phase !== "installing" || !status.installation_url || installationOpened.current) return;
    installationOpened.current = true;
    void openUrl(status.installation_url).catch(e => { if (active.current) setError(String(e)); });
  }, [status?.phase, status?.installation_url, openUrl]);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await action(); }
    catch (e) { if (active.current) setError(e instanceof Error ? e.message : "GitHub connection failed."); }
    finally { if (active.current) setBusy(false); }
  }
  async function connect() {
    await perform(async () => {
      installationOpened.current = false;
      lastPhase.current = null;
      const value = await api.startGitHub();
      if (!active.current) return;
      setStatus(value); setRepositories([]);
      await openUrl(value.verification_uri);
    });
  }
  async function revoke() {
    await perform(async () => {
      const result = await api.revokeGitHub();
      if (!active.current) return;
      setStatus(await api.githubAuth());
      onChangedRef.current();
      const crews = await api.crew();
      if (!active.current) return;
      useAppStore.getState().setCrew(crews);
      if (result.requires_provider_action) await openUrl(result.manage_url);
    });
  }
  async function cancel() {
    await perform(async () => { const value = await api.cancelGitHub(); if (active.current) setStatus(value); });
  }
  async function connectSelected() {
    await perform(async () => {
      const rows = status?.repositories.filter(r => repositories.includes(r.full_name)) ?? [];
      const common = rows[0]?.capabilities.filter(cap => rows.every(r => r.capabilities.includes(cap))) ?? [];
      await api.authorizeGitHub(repositories, common);
      if (active.current) { setStatus(await api.githubAuth()); onChangedRef.current(); }
    });
  }
  const receipt = provider.revocation;

  return <section className="surface p-6" aria-label="GitHub connection">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3"><span className="crew-icon"><Github size={18} /></span>
        <div><h2 className="text-sm font-semibold">GitHub</h2><p className="mt-1 text-xs text-zinc-500">{provider.connected ? provider.email : "Repositories, issues, pull requests and CI"}</p></div>
      </div>
      <span className="badge" aria-label="GitHub connection status">{provider.connected ? "Connected" : !status ? "Checking…" : pending ? "Connecting…" : "Not connected"}</span>
    </div>
    {provider.connected && <details className="mt-4 text-xs" aria-label="GitHub access details">
      <summary className="cursor-pointer text-zinc-300">{provider.connected ? "What Mavo can access" : "What you’re connecting"}</summary>
      <ul className="mt-3 space-y-2 leading-5 text-zinc-400">
        {provider.connected ? provider.capabilities.map(cap => <li key={cap}>{connectorLabels[cap] ?? cap}</li>) : <>
          <li>Read selected repository metadata, issues, pull requests and CI status.</li>
          <li>Create issues and comments only when GitHub access and your Actions settings allow.</li>
          <li>No code editing, merging, workflow execution or repository deletion.</li>
        </>}
      </ul>
      {provider.connected && <p className="mt-3 break-words leading-5 text-zinc-500">Repositories: {provider.repositories?.join(", ") || "None selected"}.</p>}
    </details>}
    <div className="mt-5 flex items-center gap-2">
      {provider.connected ? <button className="btn-secondary" disabled={busy} onClick={() => void revoke()}>{busy && <LoaderCircle size={14} className="animate-spin" />}Revoke access</button>
        : <button className="btn-primary" disabled={busy || pending || !status?.configured} onClick={() => void connect()}>{(busy || pending) && <LoaderCircle size={14} className="animate-spin" />}{pending ? "Connecting…" : "Connect GitHub"}</button>}
      {(pending || status?.phase === "select_repositories") && <button className="btn-quiet" disabled={busy} onClick={() => void cancel()}>Cancel</button>}
    </div>
    {status?.phase === "pending" && <div role="status" className="mt-4 space-y-2 text-xs text-zinc-400">
      <p>Enter this code on GitHub.</p>
      <div className="flex items-center gap-3"><span className="select-all font-mono text-lg tracking-widest text-zinc-100" aria-label="GitHub verification code">{status.user_code}</span><button className="btn-quiet" onClick={() => void navigator.clipboard.writeText(status.user_code ?? "").catch(() => setError("Select the code above and copy it manually."))}>Copy code</button></div>
    </div>}
    {status?.phase === "exchanging" && <p role="status" className="mt-3 text-xs text-zinc-400">Checking GitHub’s authorization…</p>}
    {status?.phase === "installing" && <p role="status" className="mt-3 text-xs leading-6 text-zinc-400">Choose repositories on GitHub. <button className="underline" onClick={() => void perform(async () => { if (status.installation_url) await openUrl(status.installation_url); })}>Open GitHub</button></p>}
    {status?.phase === "select_repositories" && <div className="mt-4 space-y-3 border-t border-line pt-4">
      <p className="text-xs text-zinc-400">Choose up to 10 repositories.</p>
      <div className="max-h-48 space-y-3 overflow-auto">{status.repositories.map(row => <label key={row.full_name} className="flex items-center gap-3 text-xs text-zinc-300"><input type="checkbox" checked={repositories.includes(row.full_name)} disabled={!repositories.includes(row.full_name) && repositories.length >= 10} onChange={e => setRepositories(e.target.checked ? [...repositories, row.full_name] : repositories.filter(r => r !== row.full_name))} /><span className="break-all">{row.full_name}</span></label>)}</div>
      <button className="btn-primary" disabled={busy || !repositories.length} onClick={() => void connectSelected()}>Connect</button>
    </div>}
    {!status?.configured && status && <p className="mt-3 text-xs text-zinc-500">Unavailable in this build.</p>}
    {!provider.connected && receipt?.requires_provider_action && <p className="mt-3 text-xs leading-5 text-zinc-400">Disconnected here. <button className="underline" onClick={() => void perform(async () => { await openUrl(receipt.manage_url); })}>Finish revoking on GitHub</button>.</p>}
    {(error || status?.error) && <p role="alert" className="notice error-notice mt-3">{error || status?.error}</p>}
  </section>;
}
