import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { AgentTraffic } from './AgentTraffic';
import { CrewOutput } from './CrewDetail';
import { freshHandoff, handoffStatus, type AgentMessage } from '../lib/orchestration';
import { localTimestamp } from '../lib/time';
import type { CrewInstance } from '../types';

const message = (extra:Partial<AgentMessage>={}):AgentMessage => ({
  id:'assignment',kind:'task_assignment',sender:'core',recipient:'crew:news',
  task_id:'run',run_id:'run',status:'pending',task_status:'failed',
  created_at:'2026-11-10T02:46:51',consumed_at:null,payload:{summary:'Read supplied feeds'},...extra,
});

it('distinguishes terminal task failure from queued delivery and unread results',()=>{
  expect(handoffStatus(message())).toBe('Run failed');
  expect(handoffStatus(message({task_status:'running'}))).toBe('Queued');
  expect(handoffStatus(message({task_status:'needs_you'}))).toBe('Waiting for input');
  expect(handoffStatus(message({kind:'result',recipient:'user',task_status:'done'}))).toBe('Result available');
  expect(handoffStatus(message({status:'consumed'}))).toBe('Processed');
});

it('renders backend UTC times and retains exact transport state for inspection',()=>{
  const html=renderToStaticMarkup(<AgentTraffic messages={[message(),message({id:'result',kind:'result',recipient:'user',task_status:'done'})]}/>);
  expect(html).toContain(localTimestamp('2026-11-10T02:46:51'));
  expect(html).toContain('Run failed');
  expect(html).toContain('Result available');
  expect(html).toContain('Transport: pending');
  expect(html).toContain('You');
});

it('treats zone-less handoff timestamps as UTC for actual graph events',()=>{
  const m=message({sender:'crew:a',recipient:'crew:b'});
  expect(freshHandoff([m],'a','b',Date.parse('2026-11-10T02:46:53Z'))).toBe(m);
  expect(freshHandoff([m],'a','b',Date.parse('2026-11-10T02:47:53Z'))).toBeUndefined();
});

it('never says waiting for first result after a failed run',()=>{
  const crew={id:'news',name:'News',last_runtime_error:'Missing evidence',checkpoint:null,trigger_type:'daily',is_running:true,permissions:[]} as unknown as CrewInstance;
  const failed=renderToStaticMarkup(<CrewOutput crew={crew}/>);
  expect(failed).toContain('did not produce a verified report');
  expect(failed).not.toContain('Waiting for the first result');
  const previous=renderToStaticMarkup(<CrewOutput crew={{...crew,checkpoint:'Earlier verified digest'}}/>);
  expect(previous).toContain('Previous successful report');
  expect(previous).toContain('Earlier verified digest');
});
