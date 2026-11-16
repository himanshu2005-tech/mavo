import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {CrewMembers,memberEdges} from './CrewMembers';
import {mentionMatches} from './CrewMentions';
import {editMentions} from '../lib/orchestration';
import {configurationValue} from './ConversationActivity';
import {visibleTasks} from '../lib/taskTeams';
import type {CrewAgent,CrewInstance,TaskRun} from '../types';

const member=(id:string,key:string,depends_on:string[]=[]):CrewAgent=>({id,crew_id:'internships',key,name:key,role:key,instructions:`Do ${key}`,route_id:null,status:'active',runtime_state:'waiting',checkpoint:null,execution:{},definition:{depends_on}});
const agents=[member('discovery-id','Discovery'),member('matching-id','Matching',['Discovery']),member('draft-id','Drafting',['Matching'])];
const crew={id:'internships',name:'Internships',status:'active',is_running:true,agents,permissions:['model:invoke'],configuration_plan:{},template_id:'research',runtime_state:'waiting',trigger_type:'interval'} as unknown as CrewInstance;

it('projects internal dependencies using agent IDs, not display names',()=>{
  expect(memberEdges(agents)).toEqual([{from:'discovery-id',to:'matching-id'},{from:'matching-id',to:'draft-id'}]);
});
it('keeps internal graphs collapsed until expanded and shows roles when expanded',()=>{
  const collapsed=renderToStaticMarkup(<CrewMembers crew={crew}/>);
  expect(collapsed).toContain('3 agents');expect(collapsed).toContain('aria-expanded="false"');expect(collapsed).not.toContain('Matching');
  const expanded=renderToStaticMarkup(<CrewMembers crew={crew} expanded onSelect={()=>{}}/>);
  expect(expanded).toContain('Discovery');expect(expanded).toContain('Matching');expect(expanded).toContain('Drafting');expect(expanded).toContain('role="button"');
});
it('addresses an individual member by canonical ID even with duplicate names',()=>{
  const other={...crew,id:'other',name:'Other',agents:[{...agents[0],id:'other-discovery',crew_id:'other'}]};
  const matches=mentionMatches([crew,other],'Internships/Dis');
  expect(matches).toHaveLength(1);expect(matches[0].agentId).toBe('discovery-id');expect(matches[0].parentCrewId).toBe('internships');
});
it('retains agent mention IDs through edits outside the token and invalidates edits inside',()=>{
  const text='@Internships/Discovery check';
  const refs=[{crewId:'internships',agentId:'discovery-id',label:'Internships/Discovery',start:0,end:22}];
  expect(editMentions(text,text+' now',refs)[0].agentId).toBe('discovery-id');
  expect(editMentions(text,text.replace('Discovery','Matching'),refs)).toEqual([]);
});
it('renders a readable role diff rather than object coercion',()=>{
  expect(configurationValue({agents:[{name:'Find',role:'Discovery'},{name:'Check',role:'Eligibility'}]})).toBe('Find (Discovery) → Check (Eligibility)');
});
it('does not show persistent Crew cycles again as unrelated temporary task teams',()=>{
  const run={id:'run',status:'running',crew_id:'internships'} as TaskRun;
  expect(visibleTasks([run])).toEqual([]);
});
