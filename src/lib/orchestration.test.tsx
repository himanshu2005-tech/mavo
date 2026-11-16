import {it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {activityForScope,editMentions,conciseActivity,freshHandoff,type ConversationActivity,type AgentMessage} from './orchestration';
import {ActivityCard,ConfigurationDiff} from '../components/ConversationActivity';
import {grantsFor} from '../components/CrewGrants';

const event=(extra:Partial<ConversationActivity>={}):ConversationActivity=>({id:'e',seq:1,kind:'question',sender:'crew:c',crew_id:'c',task_id:'t',step_id:'research',status:'pending',revision:0,created_at:'2026-11-08T00:00:00Z',resolved_at:null,payload:{summary:'Which field?'},...extra});
it('keeps mentions stable when text moves and invalidates edited tokens',()=>{
  const refs=[{crewId:'id-not-name',label:'Research',start:0,end:9}];
  expect(editMentions('@Research go','Now @Research go',refs)[0]).toMatchObject({crewId:'id-not-name',start:4,end:13});
  expect(editMentions('@Research go','@ReseXrch go',refs)).toEqual([]);
  expect(editMentions('@Research go','@Research go please',refs)).toEqual(refs);
});
it('keeps workspace updates and other chat questions out of a task conversation',()=>{
  const rows=[event({id:'news',conversation_id:null,kind:'completion',status:'recorded'}),
    event({id:'own',conversation_id:'day-planner'}),event({id:'other',conversation_id:'different-chat'}),
    event({id:'permission',conversation_id:null,kind:'permission_request'})];
  expect(activityForScope(rows).map(r=>r.id)).toEqual([]);
  expect(activityForScope(rows,'day-planner').map(r=>r.id)).toEqual(['own']);
  expect(activityForScope(rows,undefined,true).map(r=>r.id)).toEqual(['own','other','permission']);
});
it('preserves two pending questions and collapses repetitive completion traffic',()=>{
  const rows=[event(),event({id:'second',step_id:'write'}),event({id:'old',kind:'completion',status:'recorded'}),event({id:'latest',kind:'completion',status:'recorded'})];
  expect(conciseActivity(rows).map(r=>r.id)).toEqual(['e','second','latest']);
});
it('renders explicitly correlated replies, not an unaddressed chat turn',()=>{
  const html=renderToStaticMarkup(<ActivityCard item={event()} onChange={()=>{}}/>);
  expect(html).toContain('answer-e');expect(html).toContain('research');expect(html).toContain('Which field?');
});
it('renders typed permissions with scope, risk and duration',()=>{
  const item=event({kind:'permission_request',payload:{summary:'Need counters',capability:{capability:'device:read',operation:null,scope:{},risk:'read',duration:'crew',setup:'grant',explanation:'Read counters'}}});
  expect(grantsFor({id:'c'},[item])).toEqual([item]);
  expect(grantsFor({id:'other'},[item])).toEqual([]);
  const html=renderToStaticMarkup(<ActivityCard item={item} onChange={()=>{}}/>);
  expect(html).toContain('Read-only');expect(html).toContain('role="switch"');expect(html).toContain('does not approve a specific dangerous action');
});
it('renders a scheduler diff and its scope',()=>{
  const html=renderToStaticMarkup(<ConfigurationDiff edit={{id:'x',crew_id:'c',scope:'persistent',status:'proposed',base_version:0,version:null,diff:[{field:'review_interval_minutes',label:'Check interval (minutes)',before:5,after:10}]}}/>);
  expect(html).toContain('5 → 10');expect(html).toContain('all future runs');
});
it('lights a handoff only for an actual recent message',()=>{
  const message={id:'m',sender:'crew:a',recipient:'crew:b',created_at:'2026-11-08T00:00:00Z'} as AgentMessage;
  const now=Date.parse(message.created_at)+1000;
  expect(freshHandoff([message],'a','b',now)?.id).toBe('m');
  expect(freshHandoff([message],'b','a',now)).toBeUndefined();
  expect(freshHandoff([message],'a','b',now+10000)).toBeUndefined();
});
