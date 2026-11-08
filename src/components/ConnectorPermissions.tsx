import { useEffect, useState } from "react";
import { api } from "../lib/api";

export const connectorLabels:Record<string,string>={
  "github:repo:read":"Read selected repositories",
  "github:issues:read":"Read repository issues",
  "github:pulls:read":"Read pull requests",
  "github:workflows:read":"Read workflow and CI status",
  "github:issues:write":"Create issues and comments",
};

let feedNames:Record<string,string>|null=null;
function useFeedNames(){
  const [names,setNames]=useState(feedNames??{});
  useEffect(()=>{
    if(feedNames)return;let active=true;
    void api.connectors().then(list=>{
      const sources=list.find(p=>p.provider==="feeds")?.available_sources??[];
      feedNames=Object.fromEntries(sources.map(s=>[`feeds:${s.id}:read`,`Read ${s.name} feed`]));
      if(active)setNames(feedNames);
    }).catch(()=>undefined);
    return()=>{active=false;};
  },[]);
  return names;
}

export function ConnectorPermissions({value,onChange,allowed}:{value:string[];onChange:(value:string[])=>void;allowed:string[]}){
  const names=useFeedNames();
  return <div className="space-y-3">{allowed.map(id=><label key={id} className="flex items-start gap-3 text-xs leading-5 text-zinc-400"><input type="checkbox" className="mt-1" checked={value.includes(id)} onChange={e=>onChange(e.target.checked?[...value,id]:value.filter(c=>c!==id))}/><span>{connectorLabels[id]??names[id]??id}</span></label>)}</div>;
}
