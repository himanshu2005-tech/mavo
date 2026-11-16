import { LoaderCircle, Play, RotateCw, Settings2, Square } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";
import { agentState, botState } from "./AgentGraph";
import { AgentBot } from "./AgentBot";
import { QuestionAnswer } from "./AlertAnswer";
import { agentStyle } from "./CrewFan";
import { CrewAsk } from "./CrewAsk";
import { triggerLine } from "./CrewCard";
import { CrewGrants } from "./CrewGrants";
import { CrewOutput, Notes, Preferences } from "./CrewDetail";
import { RunHistory } from "./RunHistory";
import { TaskAnswer } from "./TaskAnswer";
import { NeedsAccess, needsAccess } from "./NeedsAccess";
import { RoomMap, progressLines } from "./RoomMap";
import { useGoogleConnection } from "../lib/useGoogleConnection";
import { CrewReplyBox } from "./CrewReplyBox";
import { LimitCard, limitOf } from "./LimitCard";
import { HandoverCard } from "./LinePanel";
import { WaitingChanges } from "./WaitingChanges";
import { workerLabel } from "../lib/crewStatus";

const refresh = () => void api.crew().then(all => useAppStore.getState().setCrew(all)).catch(() => undefined);

/** One agent's room: on the left, everything it did and needs, in order, as a timeline you read
 *  top to bottom, and the box to talk to it at the end; on the right, its line map, lit as it
 *  works. Earlier work, its latest report (or what it is
 *  doing now), then whatever waits for you (a question with its answers, a change to Run or
 *  Deny), then your conversation with it. Its settings open from the gear. */
/** What a run is doing, as lines under the message that started it: the steps it has done, the
 *  one it is on, then what that step says it is doing. */
export function liveLines(cycle: CrewInstance["cycle"]): string[] | undefined {
  if (!cycle) return undefined;
  if (cycle.asked) return progressLines(cycle.thoughts);
  const done = cycle.steps.filter(s => s.status === "done").map(s => s.title);
  const now = cycle.steps.find(s => s.status === "running" || s.status === "asking");
  // The step it is on is the live line (with what it says it is doing), never ticked off.
  const said = progressLines(cycle.thoughts).at(-1);
  const live = now ? (said ? `${now.title} · ${said}` : now.title) : said;
  return [...done, ...(live ? [live] : [])];
}

export function AgentRoom({ crew, onSettings }: { crew: CrewInstance; onSettings: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [past, setPast] = useState<number | null>(null);
  const state = agentState(crew);
  const google = useGoogleConnection();

  async function toggle() {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(crew.is_running ? await api.stopCrew(crew.id) : await api.startCrew(crew.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change it."); }
    finally { setBusy(false); }
  }
  // Check now is a message in the room: "Check now", its steps as they happen, then its answer.
  const [reload, setReload] = useState(0);
  async function check() {
    setBusy(true); setError(null); setNote(null);
    try { await api.crewRunNow(crew.id); setReload(n => n + 1); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not check it."); }
    finally { setBusy(false); }
  }

  const needs = !crew.held && !limitOf(crew) && (crew.question || (crew.asks?.length ?? 0) > 0);
  const cycle = crew.cycle;
  const waits = <>
    {needsAccess(crew, google.status) && <div className="room-card"><NeedsAccess crew={crew} google={google} onSettings={onSettings} /></div>}
    {crew.held && <div className="room-card"><HandoverCard crew={crew} /></div>}
    {!crew.held && limitOf(crew) && <div className="room-card"><LimitCard crew={crew} /></div>}
    {needs && <div className="room-card room-needs" aria-label="It needs you">
      {crew.question && <><p className="room-question">{crew.question}</p><CrewGrants crew={crew} /><WaitingChanges crewId={crew.id} onAnswered={refresh} /><CrewReplyBox crew={crew} /></>}
      {crew.asks?.map(ask => <div key={ask.id} className="room-ask"><p className="room-question">{ask.question}</p>
        <QuestionAnswer id={ask.id} choices={ask.choices} onAnswered={refresh} /></div>)}
    </div>}
    {cycle?.question && cycle.question_step && <div className="room-card room-needs room-ask"><p className="room-question">{cycle.question}</p>
      <TaskAnswer runId={cycle.run_id} stepId={cycle.question_step} onAnswered={refresh} /></div>}
    {note && <p className="room-note" role="status">{note}</p>}
    {error && <p className="room-error" role="alert">{error}</p>}
  </>;
  return <section className="room" aria-label={crew.name}>
    <header className="room-head">
      <span className="map-face lg has-bot" style={agentStyle(crew)}><AgentBot seed={crew.id} state={botState(crew)} /></span>
      <div className="min-w-0 flex-1">
        <h2>{crew.name}</h2>
        <p className="room-sub"><span className="map-state" data-state={state}><i />{workerLabel(crew)}</span> · {triggerLine(crew)}{crew.hotkey && <> · <kbd className="hotkey-keys hotkey-small" title={`Press ${crew.hotkey} anywhere to run it now`}>{crew.hotkey.split("+").map(k => <span key={k}>{k}</span>)}</kbd></>}</p>
      </div>
      <div className="room-tools">
        {crew.is_running && crew.trigger_type !== "daily" && <button className="btn-quiet !py-1.5" disabled={busy || crew.runtime_state === "working"} onClick={() => void check()}>
          <RotateCw size={13} className={crew.runtime_state === "working" ? "animate-spin" : ""} />Check now</button>}
        <button className="btn-secondary !py-1.5" disabled={busy} onClick={() => void toggle()}>
          {busy ? <LoaderCircle size={13} className="animate-spin" /> : crew.is_running ? <Square size={12} /> : <Play size={12} />}{crew.is_running ? "Stop" : "Start"}</button>
        <button className="btn-quiet !p-1.5" aria-label="Its settings" title="Its settings" onClick={onSettings}><Settings2 size={15} /></button>
      </div>
    </header>

    <div className="room-body">
    {/* Left: the conversation only. What waits for you arrives in it like a message; the box
        stays at the bottom. Right: what it does and everything it did. */}
    <div className="room-chat">
      {crew.trigger_type !== "daily"
        ? <CrewAsk key={crew.id} crewId={crew.id} name={crew.name} onPreference={refresh} steps={liveLines(crew.cycle)} live={crew.cycle} reload={reload}>{waits}</CrewAsk>
        : <div className="ask"><div className="ask-thread">{waits}</div></div>}
    </div>
    <RoomMap crew={crew}>
      <Notes crew={crew} />
      <Preferences crew={crew} />
      <div className="room-past" data-empty={!past || undefined}>
        <p className="room-map-head">Earlier</p>
        <RunHistory crew={crew} onCount={setPast} />
      </div>
      {!past && !crew.cycle && <CrewOutput crew={{ ...crew, cycle: null }} ask={false} />}
    </RoomMap>
    </div>
  </section>;
}
