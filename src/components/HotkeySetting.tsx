import { Keyboard, LoaderCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";

const MODS: ["ctrlKey" | "altKey" | "shiftKey" | "metaKey", string][] = [["ctrlKey", "Ctrl"], ["altKey", "Alt"], ["shiftKey", "Shift"], ["metaKey", "Win"]];

/** "Ctrl+Alt+R" from a key press, or null while only modifiers are held. */
export function comboOf(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "altKey" | "shiftKey" | "metaKey">): string | null {
  const mods = MODS.filter(([flag]) => e[flag]).map(([, name]) => name);
  const code = e.code ?? "";
  const key = /^F\d{1,2}$/.test(e.key) ? e.key
    : code.startsWith("Key") ? code.slice(3) : code.startsWith("Digit") ? code.slice(5)
    : e.key === " " ? "Space" : ["Insert", "Delete", "Home", "End", "PageUp", "PageDown", "Pause"].includes(e.key) ? e.key : null;
  return key ? [...mods, key].join("+") : null;
}

/** The keys that run this agent at once, from anywhere. Press them to set; Windows' own keys and
 *  ones another app holds are said so. */
export function HotkeySetting({ crew }: { crew: CrewInstance }) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(hotkey: string | null) {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.setHotkey(crew.id, hotkey)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not set it."); }
    finally { setBusy(false); setRecording(false); }
  }
  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") { setRecording(false); return; }
      const combo = comboOf(e);
      if (combo) void save(combo);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording]);
  return <div className="hotkey-setting">
    <div className="hotkey-row">
      <Keyboard size={15} className="text-zinc-500" aria-hidden="true" />
      <div className="min-w-0 flex-1"><p className="text-[13px] text-zinc-200">Keyboard shortcut</p>
        <p className="text-[12px] text-zinc-500">{recording ? "Press the keys now… (Esc to cancel)" : crew.hotkey ? "Runs it at once, from any app." : "Run it at once from anywhere, with keys you pick."}</p></div>
      {crew.hotkey && !recording && <kbd className="hotkey-keys">{crew.hotkey.split("+").map(k => <span key={k}>{k}</span>)}</kbd>}
      <button type="button" className="btn-secondary !py-1.5" disabled={busy} onClick={() => setRecording(r => !r)}>
        {busy ? <LoaderCircle size={13} className="animate-spin" /> : recording ? "Cancel" : crew.hotkey ? "Change" : "Set"}</button>
      {crew.hotkey && !recording && <button type="button" className="btn-quiet !p-1.5" aria-label="Remove the shortcut" disabled={busy} onClick={() => void save(null)}><X size={14} /></button>}
    </div>
    {(error || crew.hotkey_problem) && <p role="alert" className="hotkey-problem">{error || crew.hotkey_problem}</p>}
  </div>;
}
