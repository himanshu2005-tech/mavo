export interface RuntimeEvent {
  seq:number;kind:'run_started'|'run_finished'|'model_started'|'model_finished';at:string;
  crew_id:string;run_id:string;route_id:string|null;call_id?:string;
  provider?:string;model?:string;status?:string;
}
export interface RuntimeSnapshot {
  stream_id:string;cursor:number;reset:boolean;events:RuntimeEvent[];
  active_runs:RuntimeEvent[];active_calls:RuntimeEvent[];
}
export interface ExecutionFeed { snapshot:RuntimeSnapshot; receivedAt:number; notices:(RuntimeEvent&{seenAt:number})[] }
export interface CrewExecution { crewId:string;runId:string;stage:string;routeId:string|null;provider?:string;at:string }
export interface ProviderExecution { stage:'requesting'|'responded'|'failed'|'cancelled';count:number;model?:string;provider?:string }

export function validRuntimeSnapshot(value:unknown):value is RuntimeSnapshot {
  if (!value||typeof value!=='object') return false;
  const s=value as RuntimeSnapshot;
  const valid=(e:RuntimeEvent)=>e&&typeof e.crew_id==='string'&&typeof e.run_id==='string'
    &&Number.isFinite(e.seq)&&typeof e.at==='string'&&['run_started','run_finished','model_started','model_finished'].includes(e.kind);
  return typeof s.stream_id==='string'&&Number.isFinite(s.cursor)&&typeof s.reset==='boolean'
    &&[s.events,s.active_runs,s.active_calls].every(rows=>Array.isArray(rows)&&rows.every(valid));
}
export function mergeExecution(previous:ExecutionFeed|null,snapshot:RuntimeSnapshot,now=Date.now()):ExecutionFeed {
  const same=previous?.snapshot.stream_id===snapshot.stream_id;
  if (same&&snapshot.cursor<previous!.snapshot.cursor&&!snapshot.reset) return previous!;
  const notices=same&&!snapshot.reset?previous!.notices.filter(e=>now-e.seenAt<12000):[];
  const cursor=same&&!snapshot.reset?previous!.snapshot.cursor:-1;
  return {snapshot,receivedAt:now,notices:[...notices,...snapshot.events.filter(e=>e.seq>cursor).map(e=>({...e,seenAt:now}))].slice(-100)};
}
export function executionSignals(feed:ExecutionFeed|null|undefined,now=Date.now()) {
  const crews:Record<string,CrewExecution>={},providers:Record<string,ProviderExecution>={};
  if (!feed) return {crews,providers,calls:[] as RuntimeEvent[]};
  const fresh=now-feed.receivedAt<8000;
  const recent=feed.notices.filter(e=>now-e.seenAt<12000);
  for (const e of recent) {
    if (e.kind==='run_finished') crews[e.crew_id]={crewId:e.crew_id,runId:e.run_id,stage:e.status??'interrupted',routeId:e.route_id,at:e.at};
    if (e.kind==='model_finished'&&e.route_id) providers[e.route_id]={stage:e.status==='responded'?'responded':e.status==='cancelled'?'cancelled':'failed',count:0,model:e.model,provider:e.provider};
  }
  if (fresh) {
    for (const e of feed.snapshot.active_runs) crews[e.crew_id]={crewId:e.crew_id,runId:e.run_id,stage:'running',routeId:e.route_id,at:e.at};
    for (const e of feed.snapshot.active_calls) {
      crews[e.crew_id]={crewId:e.crew_id,runId:e.run_id,stage:'requesting',routeId:e.route_id,provider:e.provider,at:e.at};
      if (e.route_id) providers[e.route_id]={stage:'requesting',count:(providers[e.route_id]?.stage==='requesting'?providers[e.route_id].count:0)+1,model:e.model,provider:e.provider};
    }
  } else {
    // An old snapshot cannot prove that a provider is still handling a request.
    for (const key of Object.keys(providers)) delete providers[key];
  }
  return {crews,providers,calls:fresh?feed.snapshot.active_calls:[]};
}
export function executionLabel(stage:string):string {
  return ({requesting:'Model request in progress',running:'Running check',completed:'Run completed',failed:'Run failed',cancelled:'Run stopped',interrupted:'Run interrupted'} as Record<string,string>)[stage]??'Run ended';
}
