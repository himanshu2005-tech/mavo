import type {PoolResource} from '../types';
import type {GraphNode} from '../components/AgentGraph';
import {wiring} from '../components/crewWiring';

const LABELS:Record<string,[string,string]>={
  gmail:['Gmail','mail'],calendar:['Calendar','calendar'],tasks:['Tasks','tasks'],drive:['Drive','drive'],
  contacts:['Contacts','contacts'],youtube:['YouTube','youtube'],github:['GitHub','code'],feeds:['News feeds','rss'],
  weather:['Weather','weather'],location:['Location','globe'],open_data:['Open data','globe'],device:['This computer','monitor'],
};

/** Available connections are inventory, not per-Crew grants. Never invent users or edges. */
export function connectionInventory(resources:PoolResource[]):GraphNode[]{
  const nodes=new Map<string,GraphNode>();
  for(const resource of resources){
    const external=['google','github','feeds'].includes(resource.kind);
    if(!resource.connected)continue;
    const authorized=!external||resource.readable.length>0||resource.actions.some(a=>a.available);
    if(!authorized)continue; // One Google login does not authorize every Google service.
    const state=resource.level==='off'?'Access off':external?'Connected':'Available';
    if(LABELS[resource.id]&&(external||resource.level!=='off')){
      const [label,icon]=LABELS[resource.id];
      const key=resource.kind==='google'?`google.${resource.id}`:resource.id;
      nodes.set(`resource:${key}`,{key,label,icon,kind:'resource',allowed:false,users:[],pendingUsers:[],connectionState:state});
    }
    if(resource.level!=='write')continue;
    for(const action of resource.actions.filter(a=>a.available&&a.enabled_by_you)){
      // Reuse the graph's established grouping and canonical action/resource keys.
      const wires=wiring({permissions:[`action:${action.operation}`],configuration_plan:undefined});
      for(const wire of [...wires.reads,...wires.does])nodes.set(`${wire.kind}:${wire.key}`,{
        ...wire,allowed:false,users:[],pendingUsers:[],connectionState:wire.kind==='action'?'Enabled':state,
      });
    }
  }
  return [...nodes.values()];
}

export function mergeConnections(used:GraphNode[],available:GraphNode[]):GraphNode[]{
  const nodes=new Map(available.map(n=>[`${n.kind}:${n.key}`,n]));
  for(const node of used){
    const key=`${node.kind}:${node.key}`;
    nodes.set(key,{...nodes.get(key),...node});
  }
  return [...nodes.values()];
}
