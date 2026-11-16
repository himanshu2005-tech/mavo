import { parseTime } from './time';

export interface MentionRef { crewId: string; agentId?:string; label: string; start: number; end: number }

/** Adjust stable-ID ranges after one edit. Editing a token invalidates it rather than guessing. */
export function editMentions(before:string, after:string, refs:MentionRef[]):MentionRef[] {
  let start=0;
  while(start<before.length&&start<after.length&&before[start]===after[start])start++;
  let oldEnd=before.length,newEnd=after.length;
  while(oldEnd>start&&newEnd>start&&before[oldEnd-1]===after[newEnd-1]){oldEnd--;newEnd--;}
  const delta=newEnd-oldEnd;
  return refs.flatMap(ref=>ref.end<=start?[ref]:ref.start>=oldEnd?[{...ref,start:ref.start+delta,end:ref.end+delta}]:[])
    .filter(ref=>after.slice(ref.start,ref.end)===`@${ref.label}`);
}

export interface ConfigEdit {
  id:string; crew_id:string|null; scope:string; status:string; base_version:number; version:number|null;
  diff:{field:string;label:string;before:unknown;after:unknown}[];
}
export interface MissingCapability {
  capability:string; operation:string|null; scope:Record<string,string[]>; risk:'read'|'change'|'sensitive';
  duration:'crew'; setup:'grant'|'resources'|'provider'|'actions'; explanation:string;
}
export interface ConversationActivity {
  conversation_id?: string | null;
  id:string;seq:number;kind:string;sender:string;crew_id:string|null;task_id:string|null;step_id:string|null;
  status:string;revision:number;created_at:string;resolved_at:string|null;
  payload:{summary:string;name?:string;run_id?:string;choices?:string[];capability?:MissingCapability;edit?:ConfigEdit;action_request_id?:string|null;held?:boolean};
}
export interface AgentMessage {
  id:string;kind:string;sender:string;recipient:string;task_id:string|null;run_id:string|null;
  status:string;task_status?:string|null;created_at:string;consumed_at:string|null;payload:{summary:string};
}
/** Transport consumption is not user read acknowledgement or task success. */
export function handoffStatus(message:AgentMessage):string {
  if(message.status==='consumed')return 'Processed';
  if(message.recipient==='user'&&['result','completion'].includes(message.kind))return 'Result available';
  if(message.task_status==='failed')return 'Run failed';
  if(message.task_status==='stopped'||message.task_status==='cancelled')return 'Run stopped';
  if(message.task_status==='needs_you')return 'Waiting for input';
  if(message.task_status==='done')return 'Run completed';
  if(message.status==='claimed')return 'In progress';
  return message.status==='pending'?'Queued':message.status.replaceAll('_',' ');
}
/** No global or other-chat traffic may bleed into a task conversation. */
export function activityForScope(rows:ConversationActivity[],conversationId?:string,workspace=false):ConversationActivity[] {
  if(workspace)return rows.filter(row=>row.status==='pending'||row.kind==='configuration');
  if(!conversationId)return [];
  return rows.filter(row=>row.conversation_id===conversationId);
}
export function freshHandoff(messages:AgentMessage[],from:string,to:string,now:number):AgentMessage|undefined {
  return messages.find(m=>m.sender===`crew:${from}`&&m.recipient===`crew:${to}`&&
    now-parseTime(m.created_at).getTime()>=0&&now-parseTime(m.created_at).getTime()<8000);
}

/** Keep pending questions visible and collapse routine completions to one per sender. */
export function conciseActivity(rows:ConversationActivity[]):ConversationActivity[] {
  const latest=new Set<string>();
  return [...rows].reverse().filter(row=>{
    if(row.status==='pending')return true;
    if(row.kind==='configuration'){
      const key=`config:${row.payload.edit?.id??row.id}`;
      if(latest.has(key))return false;latest.add(key);return true;
    }
    const key=`${row.sender}:${row.kind}`;
    if(latest.has(key))return false;
    latest.add(key);return true;
  }).reverse();
}
