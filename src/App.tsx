import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { api } from "./lib/api";
import { Home } from "./pages/Home";
import { useAppStore } from "./store/app";
import { SessionGate } from "./components/SessionGate";
import {validRuntimeSnapshot} from './lib/executionSignals';

function WorkspaceApp() {
  useEffect(() => {
    const listeners = [listen("mavo-pause-all", () => { void api.pauseActions().then(async () => useAppStore.getState().setCrew(await api.crew())).catch((e: Error) => useAppStore.getState().setStatusError(e.message)); }), listen("mavo-open-activity", () => useAppStore.getState().setPage("activity"))];
    return () => { listeners.forEach(p => { void p.then(unlisten => unlisten()); }); };
  }, []);
  useEffect(() => {
    let active = true;
    const store = useAppStore.getState();
    const tasks = [
      api.status().then((value) => { if (active) store.setStatus(value); }),
      api.coreConversations().then((value) => { if (active && useAppStore.getState().conversations === store.conversations) store.setConversations(value); }),
      api.crewTemplates().then((value) => { if (active) store.setCrewTemplates(value); }),
      api.routes().then((value) => { if (active) store.setRoutes(value); })
    ];
    void Promise.allSettled(tasks).then((results) => {
      const failure = results.find((result) => result.status === "rejected");
      if (active && failure?.status === "rejected") store.setStatusError(failure.reason instanceof Error ? failure.reason.message : "Some workspace data could not load.");
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    let fetching = false;
    let cursor:number|undefined;
    const refresh = () => {
      if (fetching) return;
      fetching = true;
      const epoch=useAppStore.getState().workspaceEpoch;
      const current=()=>active&&useAppStore.getState().workspaceEpoch===epoch;
      const crews=Promise.all([api.crew(), api.crewDrafts()]).then(([crew,drafts]) => { if (current()) { useAppStore.getState().setCrew(crew); useAppStore.getState().setSetupDrafts(drafts); } })
        .catch((reason) => { if (current()) useAppStore.getState().setStatusError(reason instanceof Error ? reason.message : "Could not refresh Crew status."); });
      const activity=api.runtimeActivity(cursor).then(snapshot=>{
        if (!validRuntimeSnapshot(snapshot)) throw Error('Invalid live activity response.');
        if (current()) {cursor=snapshot.cursor;useAppStore.getState().setExecution(snapshot);}
      }).catch(()=>{if(current())useAppStore.getState().setExecutionError('Live run activity is unavailable. Agent schedules are unchanged.');});
      void Promise.allSettled([crews,activity])
        .finally(() => { fetching = false; });
    };
    refresh();
    const timer = window.setInterval(refresh,2500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  return <div className="app-shell h-screen overflow-hidden text-zinc-200"><ErrorBoundary><Home /></ErrorBoundary></div>;
}

export default function App() { return <SessionGate><WorkspaceApp /></SessionGate>; }
