import { useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";

export type DeskParams = { start?: string; end?: string; days?: string[]; duration_minutes?: number };

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const NAMES: Record<string, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

/** "9:00 AM" from "09:00". */
function clock(value = "09:00"): string {
  const [h, m] = value.split(":").map(Number);
  return `${h % 12 || 12}:${String(m || 0).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

/** "Mon–Fri", or "Mon, Wed, Sat" when not one run of days. */
export function daysLine(days: string[] = WEEK.slice(0, 5)): string {
  const picked = WEEK.filter(d => days.includes(d));
  const at = picked.map(d => WEEK.indexOf(d));
  const run = at.length > 2 && at.every((n, i) => i === 0 || n === at[i - 1] + 1);
  return run ? `${NAMES[picked[0]]}–${NAMES[picked[picked.length - 1]]}` : picked.map(d => NAMES[d]).join(", ");
}

export function deskLine(p: DeskParams): string {
  return `${daysLine(p.days)}, ${clock(p.start ?? "09:00")}–${clock(p.end ?? "18:00")}, your time zone · ${p.duration_minutes ?? 30} min meetings`;
}

/** The hours the agent books in, in one line the person can change. */
export function DeskSettingsLine({ crewId, params }: { crewId: string; params: DeskParams }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<DeskParams>(params);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const days = form.days ?? WEEK.slice(0, 5);

  async function save() {
    setBusy(true); setError(null);
    try {
      const crew = await api.setDeskSettings(crewId, { start: form.start ?? "09:00", end: form.end ?? "18:00", days, duration_minutes: form.duration_minutes ?? 30 });
      useAppStore.getState().upsertCrew(crew);
      setOpen(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
    finally { setBusy(false); }
  }

  if (!open) return <p className="mt-3 text-[13px] text-zinc-400">
    {deskLine(params)} <button type="button" className="btn-quiet !px-1" onClick={() => { setForm(params); setOpen(true); }}>Change</button>
  </p>;
  return <div className="mt-3 space-y-2 text-[13px] text-zinc-400">
    <div className="flex flex-wrap gap-1" role="group" aria-label="Days">
      {WEEK.map(d => <button key={d} type="button" aria-pressed={days.includes(d)} className={days.includes(d) ? "btn-secondary !px-2 !py-1" : "btn-quiet !px-2 !py-1"}
        onClick={() => setForm({ ...form, days: days.includes(d) ? days.filter(x => x !== d) : [...days, d] })}>{NAMES[d]}</button>)}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <input type="time" aria-label="From" className="form-control !w-auto" value={form.start ?? "09:00"} onChange={e => setForm({ ...form, start: e.target.value })} />
      <span>to</span>
      <input type="time" aria-label="To" className="form-control !w-auto" value={form.end ?? "18:00"} onChange={e => setForm({ ...form, end: e.target.value })} />
      <input type="number" aria-label="Meeting length in minutes" className="form-control !w-20" min={5} max={480} step={5} value={form.duration_minutes ?? 30} onChange={e => setForm({ ...form, duration_minutes: Number(e.target.value) })} />
      <span>min</span>
    </div>
    <div className="flex gap-2">
      <button type="button" className="btn-secondary" disabled={busy || !days.length} onClick={() => void save()}>Save</button>
      <button type="button" className="btn-quiet" onClick={() => setOpen(false)}>Cancel</button>
    </div>
    {error && <p role="alert" className="text-red-400">{error}</p>}
  </div>;
}
