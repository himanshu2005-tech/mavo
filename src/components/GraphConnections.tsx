import { Check,Cloud, Cpu, Database, LoaderCircle, Network, Zap } from "lucide-react";
import { useLayoutEffect, useRef, type HTMLAttributes } from "react";
import type { Brain, GraphNode, Selection } from "./AgentGraph";
import type { Point } from "../lib/radialGraph";
import { WireIcon } from "./WireIcon";
import { identityColor, identityStyle } from "../lib/identityColors";
import type {ProviderExecution} from '../lib/executionSignals';

type Model = { key: string; route: Brain; users: string[] };
interface Props {
  models: Model[]; nodes: GraphNode[]; width: number; selection: Selection;
  lit: { agents: Set<string>; keys: Set<string> } | null;
  onSelect: (selection: Selection) => void;
  hoverable: (selection: Selection) => HTMLAttributes<HTMLButtonElement>;
  onAnchors: (anchors: Record<string, Point>) => void;
  providerActivity?:Record<string,ProviderExecution>;
}

/** One grouped graph object, with individual, permission-aware connection endpoints. */
export function GraphConnections({ models, nodes, width, selection, lit, onSelect, hoverable, onAnchors,providerActivity={} }: Props) {
  const root = useRef<HTMLElement>(null);
  const signature = JSON.stringify([models.map(m => [m.key,m.route.name,m.route.model,m.route.connected]),
    nodes.map(n => [n.key,n.label,n.allowed])]);
  useLayoutEffect(() => {
    const panel = root.current;
    if (!panel) return;
    const body = panel.querySelector<HTMLElement>('.connections-body')!;
    let pending = 0;
    const measure = () => {
      pending = 0;
      const canvas = panel.parentElement!.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const clip = body.getBoundingClientRect();
      const anchors: Record<string, Point> = {};
      panel.querySelectorAll<HTMLElement>('[data-connection-key]').forEach(el => {
        const rect = el.getBoundingClientRect();
        // Scrolled-out rows attach to the dock boundary, never to invisible content.
        anchors[el.dataset.connectionKey!] = {
          x: Math.round((panelRect.right-canvas.left+2)*2)/2,
          y: Math.round((Math.max(clip.top+8,Math.min(clip.bottom-8,(rect.top+rect.bottom)/2))-canvas.top)*2)/2,
        };
      });
      onAnchors(anchors);
    };
    const schedule = () => { if (!pending) pending = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule); observer.observe(panel); observer.observe(body);
    const stopWheel = (event: WheelEvent) => event.stopPropagation();
    panel.addEventListener('wheel',stopWheel,{passive:true});
    body.addEventListener('scroll',schedule,{passive:true});
    window.addEventListener('resize',schedule);
    measure();
    return () => { observer.disconnect(); body.removeEventListener('scroll',schedule); panel.removeEventListener('wheel',stopWheel); window.removeEventListener('resize',schedule); cancelAnimationFrame(pending); };
  }, [signature, width, onAnchors]);
  useLayoutEffect(() => {
    const selected = root.current?.querySelector<HTMLElement>('[aria-pressed=true]');
    selected?.scrollIntoView({ block:'nearest', inline:'nearest' });
  }, [JSON.stringify(selection)]);
  const picked = (s: Selection) => JSON.stringify(s) === JSON.stringify(selection);
  const groups = [
    { kind:'resource', label:'Resources', icon:Database },
    { kind:'action', label:'Actions', icon:Zap },
  ] as const;
  return <section ref={root} className="graph-connections" aria-label="Connections" style={{width}}
    onPointerDown={e => e.stopPropagation()} onWheel={e => e.stopPropagation()}>
    <header className="connections-title"><Network size={15} aria-hidden="true"/><b>Connections</b><span>{models.length+nodes.length}</span></header>
    <div className="connections-body">
      <section className="connection-group" data-group="route" aria-label="Model routes">
        <header className="connection-group-title"><Cpu size={13} aria-hidden="true"/><h3>Model routes</h3><span>{models.length}</span></header>
        {!models.length && <p className="connections-empty">No model routes in use.</p>}
        {models.map(b => {
          const live=providerActivity[b.route.id];
          const sel: Selection = {kind:'route',key:b.key}, local=(live?.provider??b.route.provider)==='ollama', down=b.route.connected===false;
          return <button key={b.key} className="graph-route connection-row" data-local={local} data-down={down}
            data-execution={live?.stage}
            data-connection-key={b.key} data-identity={b.key} style={identityStyle(identityColor(b.key))} data-dim={!!lit&&!lit.keys.has(b.key)} aria-pressed={picked(sel)}
            {...hoverable(sel)} onClick={() => onSelect(sel)}
            title={`${b.route.name} (${b.route.model}, ${local?'on this PC':'cloud'}): ${b.users.length} agent${b.users.length===1?'':'s'} think with it${down?'. Not reachable now':''}`}>
            <span className="graph-route-icon">{local?<Cpu size={14} aria-hidden="true"/>:<Cloud size={14} aria-hidden="true"/>}</span>
            <span className="graph-route-text"><b>{b.route.name}</b><span>{live?.model??b.route.model}</span></span>
            <span className="connection-route-type">{live?.stage==='requesting'?<><LoaderCircle size={10} className="provider-request-spinner" aria-hidden="true"/>{live.count>1?`${live.count} requests`:'Requesting'}</>:live?.stage==='responded'?<><Check size={10} aria-hidden="true"/>Responded</>:live?.stage==='failed'?'Request failed':live?.stage==='cancelled'?'Cancelled':down?'Unavailable':local?'Local':'Cloud'}</span>
          </button>;
        })}
      </section>
      {groups.map(({kind,label,icon:Icon}) => {
        const rows=nodes.filter(n => n.kind===kind);
        return <section key={kind} className="connection-group" data-group={kind} aria-label={label}>
          <header className="connection-group-title"><Icon size={13} aria-hidden="true"/><h3>{label}</h3><span>{rows.length}</span></header>
          {!rows.length && <p className="connections-empty">No {label.toLowerCase()} in use.</p>}
          {rows.map(n => {
            const sel: Selection={kind:n.kind,key:n.key};
            return <button key={n.key} className="graph-node connection-row" data-kind={n.kind} data-connection-key={n.key}
              data-dim={!!lit&&!lit.keys.has(n.key)} data-identity={`${n.kind}:${n.key}`} style={identityStyle(identityColor(`${n.kind}:${n.key}`))} data-allowed={n.allowed} aria-pressed={picked(sel)}
              {...hoverable(sel)} onClick={() => onSelect(sel)} title={`${n.label}: ${n.users.length} agent${n.users.length===1?'':'s'}${n.allowed?'':' (not allowed yet)'}`}>
              <span className="map-icon"><WireIcon name={n.icon}/></span><span className="map-label">{n.label}</span>
              {!n.allowed&&<span className="connection-unavailable">Not allowed</span>}
            </button>;
          })}
        </section>;
      })}
    </div>
    <footer className="connections-footer">Select a connection to see its agents.</footer>
  </section>;
}
