import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ConnectorStatus, GoogleCapability } from "../types";
import { PermissionPicker } from "./PermissionPicker";
import { ConnectorPermissions } from "./ConnectorAccounts";

export function CrewAccountPermissions({googleValue,connectorValue,onChange}:{googleValue:GoogleCapability[];connectorValue:string[];onChange:(google:GoogleCapability[],connectors:string[])=>void}){
  const [providers,setProviders]=useState<ConnectorStatus[]|null>(null);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{let active=true;void api.connectors().then(v=>{if(active)setProviders(v);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[]);
  const google=providers?.find(p=>p.provider==="google"&&p.connected);
  const capabilities=providers?.filter(p=>p.provider!=="google"&&p.connected).flatMap(p=>p.capabilities)??[];
  return <details><summary className="cursor-pointer text-xs font-medium text-zinc-300">Connected account permissions</summary><div className="mt-3 space-y-4">{!providers&&!error&&<p className="text-xs text-zinc-500">Loading connected accounts...</p>}{google&&<PermissionPicker value={googleValue} allowed={google.capabilities.map(c=>c.slice(7)) as GoogleCapability[]} onChange={v=>onChange(v,connectorValue)}/>}<ConnectorPermissions value={connectorValue} onChange={v=>onChange(googleValue,v)} allowed={capabilities}/>{providers&&!google&&!capabilities.length&&<p className="text-xs text-zinc-500">Connect an account in Resources to use its tools. Local tools work without connected accounts.</p>}{error&&<p role="alert" className="notice error-notice">{error}</p>}<p className="text-[11px] leading-5 text-zinc-500">Only permissions already connected are listed. Each Crew gets only the access you select.</p></div></details>;
}
