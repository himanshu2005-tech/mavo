import { useState } from "react";
import { RUN_TOOLS, scopeField } from "../lib/actionRuleInput";

export interface ActionRule { enabled: boolean; mode: "restricted" | "unrestricted"; folders: string[]; domains: string[]; apps: string[]; recipients: string[];repositories?:string[] }
export function emptyRule(): ActionRule { return {enabled:false,mode:"restricted",folders:[],domains:[],apps:[],recipients:[],repositories:[]}; }
export function ActionAccessEditor({operation,value,onChange}: {operation:string;value:ActionRule;onChange:(value:ActionRule)=>void}) {
  const field=scopeField(operation);
  const project=operation.startsWith("project.");
  const [text,setText]=useState(field?(value[field]??[]).join("\n"):"");
  const access=!value.enabled?"blocked":value.mode;
  function update(next:string,scope=text) { const entries=scope.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);onChange({...value,enabled:next!=="blocked",mode:next==="unrestricted"?"unrestricted":"restricted",...(field?{[field]:next==="unrestricted"?[]:entries}:{})}); }
  // Running a script with limits means choosing its tools: start from the two that exist.
  function choose(next:string) { if(next==="restricted"&&operation==="project.run"&&!text.trim()){const tools=RUN_TOOLS.join("\n");setText(tools);update(next,tools);}else update(next); }
  const labels={folders:project?"Where projects are kept (optional)":"Folders",domains:"Sites — exact hostnames",apps:operation==="project.run"?"Tools that may run scripts":"Apps — executable names",recipients:"Recipients",repositories:"Repositories — owner/name"};
  const examples={folders:project?"D:\\Mavo Projects":"D:\\Projects\\MyProject",domains:"example.com\nwww.example.com",apps:operation==="project.run"?RUN_TOOLS.join("\n"):"notepad.exe",recipients:"person@example.com",repositories:"owner/project"};
  const hint=operation==="project.create"?"Leave empty for Documents\\Mavo Projects. The other project actions follow this folder.":operation==="project.run"?"python.exe and blender.exe only. Each run still waits for your OK.":"One per line.";
  return <div className="space-y-3"><select aria-label={operation+" access"} className="form-control !mt-0 !w-auto" value={access} onChange={e=>choose(e.target.value)}><option value="blocked">Off</option><option value="restricted">Allow with limits</option><option value="unrestricted">Allow without limits</option></select>{access==="restricted"&&field&&<label className="block text-xs leading-6 text-zinc-500">{labels[field]}<textarea className="form-control" rows={2} value={text} onChange={e=>{setText(e.target.value);update(access,e.target.value);}} placeholder={examples[field]}/><span className="text-[11px]">{hint}</span></label>}{access==="unrestricted"&&field&&<p className="text-xs leading-6 text-zinc-500">No custom {{folders:"folder",domains:"site",apps:"app",recipients:"recipient",repositories:"repository"}[field]} limits. Platform safety checks still apply.{operation==="project.run"&&" Each run still waits for your OK."}</p>}</div>;
}
