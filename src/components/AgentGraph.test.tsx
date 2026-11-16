import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { AgentGraph,agentActivity,gatherNodes } from "./AgentGraph";
import {mergeExecution,type RuntimeEvent,type RuntimeSnapshot} from '../lib/executionSignals';
import { flowOrder, wiring } from "./crewWiring";
import { detailAt, fitView, initials, isHub, myInitials, radialLayout, ringCounts, spread, zoomAt } from "../lib/radialGraph";
import type { CrewInstance } from "../types";

const crew = (over: Partial<CrewInstance>): CrewInstance => ({
  id: "c", template_id: "research", name: "Crew", responsibilities: "Does things.", autonomy_mode: "reactive", status: "active",
  provider: "airouter", model: "m", route_id: "r", max_model_calls: 4, schedule_time: null, reminder_text: null, resources: [],
  permissions: [], triggers: [], created_at: "2026-11-05T10:00:00", updated_at: "2026-11-05T10:00:00", is_running: true,
  runtime_state: "waiting", review_interval_minutes: 30, daily_call_limit: 96, daily_calls_used: 0, trigger_type: "interval",
  next_run_at: null, last_runtime_error: null, ...over,
} as CrewInstance);

const inbox = crew({ id: "inbox", name: "Inbox Watch", permissions: ["google:gmail:read", "notifications:show"] });

it('explains real route/resource/action links and distinguishes missing access and stopped Crew',()=>{
  const stopped=crew({...inbox,id:'paused',name:'Paused inbox',is_running:false,
    configuration_plan:{draft:{requested_actions:[{operation:'gmail.send'}]}}} as Partial<CrewInstance>);
  const html=renderToStaticMarkup(<AgentGraph crews={[stopped]} drafts={[]} me="H" memory={[]}
    selection={null} onSelect={()=>{}} onNew={()=>{}} routes={[{id:'r',name:'Main',model:'m',provider:'airouter'}]}/>);
  expect(html).toContain('Paused inbox reads Gmail · Access granted · Crew stopped. Select to inspect.');
  expect(html).toContain('Paused inbox uses model route Main · Crew stopped. Select to inspect.');
  expect(html).toMatch(/data-link-kind="action"[^>]*data-target="gmail.send"[^>]*data-allowed="false"/);
  expect(html).toContain('lg-route-link');
  expect(html).toContain('lg-resource-link');
  expect(html).toContain('lg-action-link');
  expect(html).not.toContain('provider-request-pulse');
});

it('traces every active specialist route when selecting its parent Crew',()=>{
  const row=crew({agents:[{id:'specialist',crew_id:'c',key:'reader',name:'Reader',role:'Read',
    instructions:'Read',route_id:'special',status:'active',runtime_state:'waiting',
    checkpoint:null,execution:{},definition:{}}]});
  const html=renderToStaticMarkup(<AgentGraph crews={[row]} drafts={[]} me="H" memory={[]}
    selection={{kind:'agent',id:'c'}} onSelect={()=>{}} onNew={()=>{}}
    routes={['r','special','unassigned'].map(id=>({id,name:id,model:'m',provider:'airouter'}))}/>);
  for(const id of ['r','special']) {
    expect(html).toMatch(new RegExp(`data-link-kind="route" data-target="route:${id}"[^>]*data-on="true"`));
  }
  expect(html).not.toMatch(/data-link-kind="route" data-target="route:unassigned"/);
});

it('highlights a provider only for observed model requests and keeps fast completions visible',()=>{
  const run:RuntimeEvent={seq:1,kind:'run_started',at:'2026-11-08T06:00:00Z',crew_id:inbox.id,run_id:'observed-run',route_id:'r'};
  const call:RuntimeEvent={...run,seq:2,kind:'model_started',call_id:'request',provider:'airouter',model:'m'};
  const snapshot:RuntimeSnapshot={stream_id:'observed',cursor:2,reset:false,events:[run,call],active_runs:[run],active_calls:[call]};
  const render=(execution:ReturnType<typeof mergeExecution>|null)=>renderToStaticMarkup(<AgentGraph crews={[inbox]} drafts={[]} me="H" memory={[]} selection={null} onSelect={()=>undefined} onNew={()=>undefined} routes={[{id:'r',name:'Real route',provider:'airouter',model:'m'}]} execution={execution}/>);
  const live=mergeExecution(null,snapshot);
  expect(render(null)).not.toContain('class="provider-request-pulse"');
  expect(render(live)).toContain('data-provider-active="true"');
  expect(render(live)).toContain('Model request in progress');
  expect(render(live)).toContain('data-live-detail="true"');
  expect(render(live)).toContain('Model request in progress · Real route');
  expect(render(live)).not.toContain('graph-run-notice');
  const finished=mergeExecution(live,{...snapshot,cursor:4,events:[{...call,seq:3,kind:'model_finished',status:'responded'},{...run,seq:4,kind:'run_finished',status:'completed'}],active_runs:[],active_calls:[]});
  expect(render(finished)).toContain('Run completed');
  expect(render(finished)).toContain('Responded');
  expect(render(finished)).not.toContain('class="provider-request-pulse"');
});

it('keeps a dense graph compact and gives active work priority over recent outcomes',()=>{
  // A feed is "fresh" for 8 s; 500 agents drawn twice can take longer on a busy machine.
  const clock=vi.spyOn(Date,'now').mockReturnValue(Date.parse('2026-11-08T06:00:01Z'));
  try {
  const rows=Array.from({length:500},(_,i)=>crew({id:`crew-${i}`}));
  const events:RuntimeEvent[]=rows.slice(0,8).map((row,i)=>({seq:i+1,kind:'run_finished',at:'2026-11-08T06:00:00Z',crew_id:row.id,run_id:`run-${i}`,route_id:'r',status:'completed'}));
  const active:RuntimeEvent={seq:9,kind:'run_started',at:'2026-11-08T05:00:00Z',crew_id:'crew-499',run_id:'active',route_id:'r'};
  const execution=mergeExecution(null,{stream_id:'dense',cursor:9,reset:false,events,active_runs:[active],active_calls:[]});
  const render=(selection:{kind:'agent';id:string}|null)=>renderToStaticMarkup(<AgentGraph crews={rows} drafts={[]} me="You" memory={[]} selection={selection} onSelect={()=>undefined} onNew={()=>undefined} execution={execution}/>);
  const html=render(null);
  expect(html.match(/data-live-detail="true"/g)).toHaveLength(3);
  expect(html).toMatch(/data-identity="crew:crew-499"[^>]*data-live-detail="true"/);
  expect(html).not.toContain('graph-run-notice');
  expect(render({kind:'agent',id:'crew-499'}).match(/data-live-detail="true"/g)).toHaveLength(1);
  } finally { clock.mockRestore(); }
},15000); // 500 agents drawn twice: it checks what is shown, not how fast

it("distinguishes enabled responsibilities, actual work and blocked or stopped agents", () => {
  expect(agentActivity(crew({runtime_state:'waiting'}))).toBe('ready');
  expect(agentActivity(crew({runtime_state:'waiting_event'}))).toBe('ready');
  expect(agentActivity(crew({runtime_state:'working'}))).toBe('working');
  for (const runtime_state of ['budget_paused','waiting_browser','needs_setup','needs_input','error'])
    expect(agentActivity(crew({runtime_state}))).toBe('waiting');
  expect(agentActivity(crew({runtime_state:'working',is_running:false}))).toBe('off');
  expect(agentActivity(crew({runtime_state:'working',status:'paused'}))).toBe('off');
  expect(agentActivity(crew({status:'draft'}))).toBe('off');
});

it("renders identity-colored activity rings only for enabled and actively working agents", () => {
  const rows=[crew({id:'ready'}),crew({id:'work',runtime_state:'working'}),crew({id:'stopped',is_running:false}),
    crew({id:'browser',runtime_state:'waiting_browser'}),crew({id:'budget',runtime_state:'budget_paused'})];
  const html=renderToStaticMarkup(<AgentGraph crews={rows} drafts={[]} me="H" memory={[]} selection={null} onSelect={()=>undefined} onNew={()=>undefined}/>);
  expect(html.match(/class="agent-state-ring"/g)).toHaveLength(2);
  expect(html).toContain('data-activity="ready"');
  expect(html).toContain('data-activity="working"');
  expect(html).toContain('data-activity="waiting"');
  expect(html).toContain('Waiting for browser');
  expect(html).toContain('Back tomorrow');
});
const lunch = crew({ id: "lunch", name: "Lunch Reminder", permissions: ["google:calendar:read"] });
const wind = crew({
  id: "wind", name: "Wind Down", trigger_type: "manual", permissions: ["action:windows.sleep"],
  configuration_plan: { upstream: ["lunch"], draft: { requested_actions: [{ operation: "windows.sleep" }, { operation: "gmail.send" }] } },
} as Partial<CrewInstance>);
const draft = crew({ id: "trip", name: "Trip Planner", status: "draft" } as Partial<CrewInstance>);

it("splits what an agent reads from what it can do, and marks what it asked for but was not given", () => {
  expect(wiring(inbox)).toEqual({
    reads: [{ key: "google.gmail", label: "Gmail", icon: "mail", allowed: true, kind: "resource" }],
    does: [{ key: "notify", label: "Desktop alerts", icon: "bell", allowed: true, kind: "action" }],
  });
  const { does } = wiring(wind);
  expect(does.find(d => d.key === "windows.sleep")?.allowed).toBe(true);
  expect(does.find(d => d.key === "gmail.send")?.allowed).toBe(false);
});

it('shows proposed Google, device, weather and notification access without granting it',()=>{
  const row=crew({template_id:'computer',configuration_plan:{phase:'needs_access',questions:[],draft:{
    name:'Pending setup',responsibilities:'Review authorized data.',template_id:'computer',trigger_type:'interval',
    requested_google_resources:['gmail'],requested_google_capabilities:['calendar:read'],
    requested_connector_capabilities:['github:repo:read'],requested_device_metrics:true,
    requested_weather:true,requested_notifications:true,requested_actions:[],daily_call_limit:8,max_model_calls:4,
  }}});
  const result=wiring(row);
  expect(result.reads.map(w=>w.key)).toEqual(['google.gmail','google.calendar','github','device','weather']);
  expect(result.does.map(w=>w.key)).toEqual(['notify']);
  expect([...result.reads,...result.does].every(w=>!w.allowed)).toBe(true);
  expect(row.permissions).toEqual([]);
});

it('retains pending per-agent access when another agent already has the shared connection',()=>{
  const [node]=gatherNodes([
    {crew:inbox,list:[{key:'google.gmail',label:'Gmail',icon:'mail',allowed:true,kind:'resource'}]},
    {crew:lunch,list:[{key:'google.gmail',label:'Gmail',icon:'mail',allowed:false,kind:'resource'}]},
  ]);
  expect(node.allowed).toBe(true);
  expect(node.users).toEqual([inbox.id,lunch.id]);
  expect(node.pendingUsers).toEqual([lunch.id]);
});

it('keeps real scheduled countdowns in a large graph without inventing clocks for manual or blocked agents',()=>{
  const next=new Date(Date.now()+30*60000).toISOString();
  const rows=Array.from({length:100},(_,i)=>crew({id:`scheduled-${i}`,next_run_at:next,
    trigger_type:i<10?'manual':'interval',runtime_state:i>=90?'needs_input':'waiting'}));
  const html=renderToStaticMarkup(<AgentGraph crews={rows} drafts={[]} me="You" memory={[]} selection={null} onSelect={()=>undefined} onNew={()=>undefined}/>);
  expect(html.match(/class="run-clock"/g)).toHaveLength(80);
  expect(html).toContain('data-many="true"');
  expect(html).toContain('data-links="true"');
  expect(html).toContain('data-clocks="true"');
  expect(html).toContain('aria-label="Show connections"');
});

it("reading notes or a folder is something an agent reads; writing and organising is something it does", () => {
  const journal = crew({ permissions: ["action:notes.find", "action:notes.read", "action:notes.write", "action:files.move", "action:windows.media"] });
  const { reads, does } = wiring(journal);
  expect(reads.map(r => r.label)).toEqual(["Notes"]);
  expect(does.map(d => d.label)).toEqual(["Write notes", "Organise files", "Play & pause music"]);
});

it("shows a site an agent reads, and acting on it, per site", () => {
  const shop = crew({ configuration_plan: { draft: { requested_actions: [{ operation: "browser.open", domains: ["airouter.io"] }, { operation: "browser.click", domains: ["airouter.io"] }] } } } as Partial<CrewInstance>);
  const { reads, does } = wiring(shop);
  expect(reads.map(r => r.label)).toEqual(["airouter.io"]);
  expect(does.map(d => d.label)).toEqual(["Act on airouter.io"]);
});

it("keeps a chain together: an agent sits after the one it hears from", () => {
  expect(flowOrder([wind, inbox, lunch]).map(c => c.name)).toEqual(["Inbox Watch", "Lunch Reminder", "Wind Down"]);
});

it("draws you in the middle, your agents and drafts around you, and what they use outside", () => {
  const memory = [{ id: "m1", text: "My mom is Sita", source: "Lunch Reminder", at: "2026-11-06T10:00:00" }];
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, lunch, wind]} drafts={[draft]} me="Asha R" memory={memory} selection={null} onSelect={() => undefined} onNew={() => undefined} />);
  for (const text of ["Asha R", ">AR<", "1 thing remembered", "My mom is Sita", "Inbox Watch", "Wind Down", "Trip Planner", "Draft · finish setting it up", "New agent", "Gmail", "Calendar", "Desktop alerts", "Sleep the PC", "Send mail", "Zoom in", "Fit to screen"]) expect(html).toContain(text);
  expect(html).toContain('data-allowed="false"'); // Send mail: asked for, not given
  expect(html.match(/class="wire wire-talk"/g)).toHaveLength(1); // Lunch Reminder hands over to Wind Down
});

it("fades everything a picked agent does not touch", () => {
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, lunch, wind]} drafts={[]} me="H" memory={[]} selection={{ kind: "agent", id: "lunch" }} onSelect={() => undefined} onNew={() => undefined} />);
  const dim = (name: string) => new RegExp(`data-dim="true"[^>]*title="${name}`).test(html);
  expect(dim("Inbox Watch")).toBe(true);
  expect(dim("Lunch Reminder")).toBe(false);
  expect(dim("Wind Down")).toBe(false); // it hears from Lunch Reminder
});

it("shows a scheduled agent's clock, makes a working agent's lines flow, and lets a mail watcher listen", () => {
  const soon = new Date(Date.now() + 12 * 60_000).toISOString();
  const news = crew({ id: "news", name: "News Watch", next_run_at: soon, permissions: ["notifications:show"] });
  const busy = crew({ id: "busy", name: "Busy", runtime_state: "working", permissions: ["google:gmail:read"] });
  const mail = crew({ id: "mail", name: "Mail Watch", trigger_type: "gmail_message", permissions: ["google:gmail:read"] });
  const html = renderToStaticMarkup(<AgentGraph crews={[news, busy, mail]} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} onRun={async () => undefined} />);
  expect(html).toMatch(/class="run-clock"[^>]*aria-label="News Watch runs in 1[12] min, every 30 min. Press to run it now."/);
  expect(html.match(/class="run-clock"/g)).toHaveLength(1); // only the one counting down
  expect(html).not.toContain('data-flow="in"'); // Working does not prove it is reading Gmail.
  expect(html).toMatch(/data-listen="true"[^>]*title="Mail Watch/);
});

it("lays agents on a ring and keeps outside nodes apart, near the agents that use them", () => {
  const out = radialLayout(["a", "b", "c"], [{ key: "x", users: ["a"] }, { key: "y", users: ["a"] }, { key: "z", users: ["c"] }], 2);
  const d = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.hypot(p.x - q.x, p.y - q.y);
  expect(out.memory).toHaveLength(2);
  expect(d(out.nodes.x, out.nodes.y)).toBeGreaterThan(60);
  expect(d(out.nodes.x, out.agents.a)).toBeLessThan(d(out.nodes.x, out.agents.c));
  const sorted = [...spread([0, 0, 0, 0], 0.5)].sort((p, q) => p - q);
  for (let i = 1; i < sorted.length; i++) expect(sorted[i] - sorted[i - 1]).toBeGreaterThan(0.49);
});

it("fits the drawing in the window and zooms about the cursor", () => {
  const v = fitView(1000, 500, 1200, 800);
  expect(v.k).toBeCloseTo(1.1);
  expect(v.x).toBeCloseTo((1200 - 1100) / 2);
  const z = zoomAt({ x: 0, y: 0, k: 1 }, 2, { x: 100, y: 100 });
  expect(z).toEqual({ k: 2, x: -100, y: -100 }); // the point under the cursor stays put
  expect(zoomAt({ x: 0, y: 0, k: 2.4 }, 10, { x: 0, y: 0 }).k).toBe(2.5);
});

it("writes initials the way people read them", () => {
  expect([initials("The Inbox Summary"), initials("Battery"), myInitials("Asha"), myInitials("Asha Rao")]).toEqual(["IS", "BA", "A", "AR"]);
});

it("puts what most agents share in the middle with you, and what a few use at the edge, near them", () => {
  const ids = Array.from({ length: 14 }, (_, i) => "a" + i);
  const out = radialLayout(ids, [
    { key: "notify", users: ids.slice(0, 10) }, // desktop alerts: most of them
    { key: "site", users: ["a3"] },
  ], 0);
  expect(out.hubs).toEqual(["notify"]);
  const d = (p: { x: number; y: number }) => Math.hypot((p.x - out.c.x) / 1.45, (p.y - out.c.y) / 0.8);
  expect(d(out.nodes.notify)).toBeLessThan(d(out.agents.a0)); // inside the agents' ring
  expect(d(out.nodes.site)).toBeGreaterThan(d(out.agents.a3)); // outside, by its agent
  expect([isHub(2, 10), isHub(3, 10), isHub(3, 20), isHub(6, 100), isHub(3, 3)]).toEqual([false, true, false, true, false]);
});

it("three hundred agents get rings of their own, every one placed, no two cards on top of each other", () => {
  const ids = Array.from({ length: 300 }, (_, i) => "agent-" + i);
  const out = radialLayout(ids, [{ key: "notify", users: ids }], 3);
  expect(out.rings).toBeGreaterThan(5);
  expect(Object.keys(out.agents)).toHaveLength(300);
  expect(ringCounts(301, 300).reduce((s, r) => s + r.count, 0)).toBe(301);
  const pts = Object.values(out.agents);
  let overlaps = 0;
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++)
    if (Math.abs(pts[i].x - pts[j].x) < 200 && Math.abs(pts[i].y - pts[j].y) < 40) overlaps++; // a card is about 200 by 40
  expect(overlaps).toBe(0);
  // It still fits a laptop screen, zoomed out.
  expect(fitView(out.W, out.H, 1400, 860).k).toBeGreaterThanOrEqual(0.08);
});

it("draws less the further out you zoom", () => {
  expect([detailAt(1), detailAt(0.6), detailAt(0.3)]).toEqual(["near", "mid", "far"]);
});

it("YouTube is something an agent reads, through the Google sign-in", () => {
  const { reads } = wiring(crew({ permissions: ["google:youtube:read"] }));
  expect(reads).toEqual([{ key: "google.youtube", label: "YouTube", icon: "youtube", allowed: true, kind: "resource" }]);
});

it("draws a one-time task with its team, the member at work marked", async () => {
  const { taskOnMap } = await import("../lib/taskTeams");
  const run = { id: "t1", title: "Build: Combined RSS Reader", status: "running", steps: [
    { id: "1", title: "Lead picked the team", kind: "helper", status: "done", team: ["React developer", "Tester"] },
    { id: "2", title: "React developer writes it", kind: "helper", status: "running" },
  ] } as never;
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox]} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} tasks={[taskOnMap(run)]} />);
  for (const text of ["Combined RSS Reader", "Working · 1 of 2 steps", "React developer", "Tester"]) expect(html).toContain(text);
  expect(html).toMatch(/class="graph-task-member" [^>]*data-state="working"[^>]*title="React developer/);
});

it("initials skip symbols", () => {
  expect(initials("Accessibility & UX reviewer")).toBe("AU");
});

const routes = [
  { id: "r", name: "AIRouter default", provider: "airouter", model: "deepseek-v4-flash", connected: true },
  { id: "local", name: "Ollama", provider: "ollama", model: "qwen2.5:3b", connected: false },
  { id: "unused", name: "Spare", provider: "airouter", model: "x" },
];

it("shows the models agents think with, near you, and says when one can't be reached", () => {
  const poem = crew({ id: "poem", name: "Poem", route_id: "local", provider: "ollama", model: "qwen2.5:3b" });
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, lunch, poem]} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  expect(html.match(/class="graph-route connection-row"/g)).toHaveLength(3); // saved routes remain visible even before assignment
  expect(html).toContain('Spare');
  expect(html).toContain('Not assigned');
  expect(html).toContain("AIRouter default");
  expect(html).toMatch(/data-local="true" data-down="true"[^>]*title="Ollama \(qwen2.5:3b, on this PC\): 1 agent think with it. Not reachable now"/);
  expect(html.match(/data-link-kind="route"/g)).toHaveLength(3); // one line per agent to its model
  expect(html).not.toContain("trace-dot"); // nothing picked: no path traced
});

it("selecting an agent does not fabricate model or tool activity", () => {
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, lunch]} drafts={[]} me="H" memory={[]} selection={{ kind: "agent", id: "inbox" }} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  const kinds = [...html.matchAll(/class="trace-dot" data-kind="(\w+)"/g)].map(m => m[1]);
  expect(kinds).toEqual([]); // Structural access is not evidence of a live operation.
  const delays = [...html.matchAll(/animation-delay:([\d.]+)s/g)].map(m => Number(m[1]));
  expect(delays).toEqual([...delays].sort((a, b) => a - b));
});

it("picking a model lights the agents that think with it and fades the rest", () => {
  const poem = crew({ id: "poem", name: "Poem", route_id: "local" });
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, poem]} drafts={[]} me="H" memory={[]} selection={{ kind: "route", key: "route:local" }} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  expect(/data-dim="true"[^>]*title="Inbox Watch/.test(html)).toBe(true);
  expect(/data-dim="false"[^>]*title="Poem/.test(html)).toBe(true);
  expect([...html.matchAll(/class="trace-dot" data-kind="(\w+)"/g)].map(m => m[1])).toEqual([]);
});

it("puts every model on the inner ring, even one only a single agent uses", () => {
  const ids = Array.from({ length: 20 }, (_, i) => "a" + i);
  const out = radialLayout(ids, [{ key: "route:local", users: ["a3"] }, { key: "gmail", users: ["a3"] }], 0);
  expect(out.hubs).toContain("route:local");
  expect(out.hubs).not.toContain("gmail");
  const d = (p: { x: number; y: number }) => Math.hypot((p.x - out.c.x) / 1.45, (p.y - out.c.y) / 0.8);
  expect(d(out.nodes["route:local"])).toBeLessThan(d(out.agents.a3));
});

it("keeps the handoff map structure without board or screen-space packing", () => {
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox, lunch]} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  expect(html).toContain('class="graph-world"');
  expect(html).toContain('class="graph-wires"');
  expect(html).toContain('data-link-kind="route"');
  expect(html).not.toContain('data-readable');
  expect(html).not.toContain('flow-board');
  expect(html).not.toContain('Back to workflow');
  expect(html).not.toContain('data-large');
  expect(html).not.toContain('--node-scale');
});

it("retains the compact overview for dense radial graphs", () => {
  const many = Array.from({length:50}, (_,i) => crew({id:`large-${i}`,name:`Agent ${i}`}));
  const html = renderToStaticMarkup(<AgentGraph crews={many} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} />);
  expect(html).toContain('data-many="true"');
  expect(html).not.toContain('data-large');
  expect(html.match(/data-motion="true"/g)).toHaveLength(24);
});

it("names resources, actions and model routes without changing the radial map", () => {
  const html = renderToStaticMarkup(<AgentGraph crews={[inbox]} drafts={[]} me="H" memory={[]} selection={null} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  expect(html).toContain('class="graph-connections" aria-label="Connections"');
  expect(html).toContain('data-group="resource" aria-label="Resources"');
  expect(html).toContain('data-group="action" aria-label="Actions"');
  expect(html).toContain('data-group="route" aria-label="Model routes"');
  expect(html).toContain('aria-label="Graph help"');
  expect(html).not.toContain('flow-board');
  expect(html).not.toContain('--node-scale');
});

it("does not animate denied actions or stopped agents after restoring the handoff map", () => {
  const render = (value: CrewInstance) => renderToStaticMarkup(<AgentGraph crews={[value]} drafts={[]} me="H" memory={[]} selection={{kind:"agent",id:value.id}} onSelect={() => undefined} onNew={() => undefined} routes={routes} />);
  const denied = crew({ id:"denied", route_id:"r", permissions:[], configuration_plan:{phase:"ready",questions:[],draft:{requested_actions:[{operation:"google.gmail.send"}]}} } as unknown as Partial<CrewInstance>);
  expect(render(denied)).not.toContain('data-kind="act"');
  expect(render(crew({...inbox,is_running:false,runtime_state:"working"}))).not.toContain('class="trace-dot"');
});
