import type { CrewInstance } from "../types";
import type { ConversationActivity } from '../lib/orchestration';
import { ActivityCard, useConversationActivity } from './ConversationActivity';

/** Only typed backend requests can offer grants. Model text never becomes a switch. */
export function grantsFor(crew: Pick<CrewInstance,'id'>, requests:ConversationActivity[]=[]){
  return requests.filter(r=>r.crew_id===crew.id&&r.kind==='permission_request'&&r.status==='pending'&&r.payload.capability);
}

export function CrewGrants({ crew }: { crew: CrewInstance }) {
  const {rows,reload}=useConversationActivity();
  return <>{grantsFor(crew,rows).map(item=><ActivityCard key={item.id} item={item} onChange={reload}/>)}</>;
}
