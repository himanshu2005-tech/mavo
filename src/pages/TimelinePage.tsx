import { clockTime, parseTime } from "../lib/time";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { CrewGraph } from "../components/CrewGraph";
import { Chip, hueFor } from "../components/Studio";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewGraphData, Timeline, TimelineEvent } from "../types";

const RANGES = [{ days: 1, label: "Today" }, { days: 7, label: "7 days" }, { days: 30, label: "30 days" }];

export function dayHeading(iso: string, now = new Date()): string {
  const day = parseTime(iso);
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(day) - start(now)) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return day.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export function groupByDay(events: TimelineEvent[], now = new Date()) {
  const groups: { heading: string; events: TimelineEvent[] }[] = [];
  for (const event of events) {
    const heading = dayHeading(event.at, now);
    const last = groups[groups.length - 1];
    if (last?.heading === heading) last.events.push(event); else groups.push({ heading, events: [event] });
  }
  return groups;
}

const timeOf = (iso: string) => clockTime(iso);

export function TimelineView() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<Timeline | null>(null);
  const [graph, setGraph] = useState<CrewGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const setPage = useAppStore(s => s.setPage);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [timeline, crewGraph] = await Promise.all([api.timeline(days), api.crewGraph()]);
      setData(timeline); setGraph(crewGraph);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load the timeline."); }
    finally { setLoading(false); }
  }, [days]);
  useEffect(() => { void load(); }, [load]);

  const groups = data ? groupByDay(data.upcoming) : [];
  return <>
    <div className="space-y-10">
      {error && <p role="alert" className="notice error-notice">{error}</p>}
      {graph && graph.edges.length > 0 && <section aria-label="How your Crew connect">
        <h2 className="group-title">How your Crew connect</h2>
        <div className="list-card p-5"><CrewGraph nodes={graph.nodes.filter(n => graph.edges.some(e => e.from === n.id || e.to === n.id)).map(n => ({ id: n.id, name: n.name, state: n.state, running: n.running, status: n.status }))} edges={graph.edges} onSelect={() => setPage("crew")} /></div>
      </section>}

      <section aria-label="Upcoming">
        <div className="mb-3 flex items-center justify-between"><h2 className="group-title !mb-0">Upcoming</h2><div className="flex items-center gap-3"><button className="btn-quiet !px-2 !py-1" aria-label="Refresh timeline" disabled={loading} onClick={() => void load()}><RefreshCw size={13} className={loading ? "animate-spin" : ""} /></button><div className="segmented" role="radiogroup" aria-label="Range">{RANGES.map(r => <button key={r.days} role="radio" aria-checked={days === r.days} aria-pressed={days === r.days} onClick={() => setDays(r.days)}>{r.label}</button>)}</div></div></div>
        {loading && !data && <p role="status" className="flex items-center gap-2 text-xs text-zinc-500"><LoaderCircle size={13} className="animate-spin" />Loading…</p>}
        {data && !groups.length && <div className="list-card px-5 py-8 text-[13px] leading-6 text-zinc-500">Nothing scheduled.</div>}
        <div className="tl">{groups.map(group => <div key={group.heading} className="mb-6">
          <h3 className="tl-day">{group.heading}</h3>
          {group.events.map(e => <div key={e.at + (e.crew_id ?? "") + e.title} className="tl-event" style={{ ["--hue" as string]: hueFor(e.crew_name) }}>
            <time className="tl-time" dateTime={e.at}>{timeOf(e.at)}</time>
            <div className="min-w-0 flex-1"><p className="truncate text-[14px] text-zinc-50">{e.title}</p><p className="mt-0.5 text-[12px] text-zinc-500">{e.crew_name}</p></div>
            <Chip tone={e.uses_model ? "info" : "off"}>{e.uses_model ? "Uses a model call" : "No model call"}</Chip>
          </div>)}
        </div>)}</div>
      </section>

      {data && data.standing.length > 0 && <section aria-label="Always listening">
        <h2 className="group-title">Always listening</h2>
        <div className="list-card">{data.standing.map(s => <div key={s.crew_id} className="list-row"><div className="min-w-0 flex-1"><p className="text-[14px] text-zinc-50">{s.crew_name}</p><p className="mt-0.5 text-[12px] text-zinc-500">{s.label}</p></div></div>)}</div>
        <p className="mt-3 text-[12px] text-zinc-500">No set time.</p>
      </section>}

      {data && data.recent.length > 0 && <section aria-label="Recent">
        <h2 className="group-title">Recent</h2>
        <div className="list-card">{data.recent.slice(0, 12).map(e => <div key={e.at + e.title + e.crew_name} className="list-row">
          <time className="w-36 shrink-0 text-[12px] tabular-nums text-zinc-500" dateTime={e.at}>{dayHeading(e.at)}, {timeOf(e.at)}</time>
          <div className="min-w-0 flex-1"><p className="truncate text-[14px] text-zinc-200">{e.title}</p><p className="mt-0.5 text-[12px] text-zinc-500">{e.crew_name}</p></div>
          {e.status && <span className="text-[12px] capitalize text-zinc-500">{e.status}</span>}
        </div>)}</div>
      </section>}
    </div>
  </>;
}
