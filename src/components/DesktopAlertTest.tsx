import { invoke } from "@tauri-apps/api/core";
import { Bell } from "lucide-react";
import { useState } from "react";
import type { Reminder } from "../types";
import { IconTile } from "./Studio";

export async function openDesktopAlertTest() {
  const pending = await invoke<Reminder | null>("pending_reminder");
  const now = new Date().toISOString();
  const reminder: Reminder = pending ?? {
    id: "desktop-test", crew_id: "desktop-test", crew_name: "Mavo", kind: "crew_update",
    message: "Desktop alerts are working.\n\nThis is a local display test. No agent ran and no account data changed.",
    scheduled_for: now, local_date: now.slice(0, 10), preview: true,
  };
  await invoke("show_reminder_overlay", { reminder });
}

/** Local display diagnostic only; never invokes a model or touches a provider. */
export function DesktopAlertTest() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function show() {
    setBusy(true); setError(null);
    try {
      // Reopen a real unread update rather than replacing it with a fake result.
      await openDesktopAlertTest();
    } catch {
      setError("Could not show the desktop card. Restart Mavo and try again; unread updates remain saved.");
    } finally { setBusy(false); }
  }
  return <section className="list-card">
    <div className="list-row"><IconTile icon={Bell} hue={0} />
      <div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">Desktop alerts</p>
        <p className="text-[12px] text-zinc-500">Open a readable card, including while Mavo is open. Crew alerts still respect mute.</p></div>
      <button type="button" className="btn-secondary" aria-label="Test desktop alert" disabled={busy} onClick={() => void show()}>{busy ? "Opening…" : "Test alert"}</button>
    </div>{error && <p className="notice error-notice" role="alert">{error}</p>}
  </section>;
}
