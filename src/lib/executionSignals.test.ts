import {expect,it} from 'vitest';
import {executionSignals,mergeExecution,validRuntimeSnapshot,type RuntimeEvent,type RuntimeSnapshot} from './executionSignals';
const event=(kind:RuntimeEvent['kind'],seq:number,other:Partial<RuntimeEvent>={}):RuntimeEvent=>({kind,seq,crew_id:'crew',run_id:'run',route_id:'route',at:'2026-11-08T06:00:00Z',...other});
const snapshot=(extra:Partial<RuntimeSnapshot>={}):RuntimeSnapshot=>({stream_id:'one',cursor:0,reset:false,events:[],active_runs:[],active_calls:[],...extra});
it('does not show provider work merely because an agent run has started',()=>{
  const feed=mergeExecution(null,snapshot({active_runs:[event('run_started',1)]}),1000);
  const signals=executionSignals(feed,1100);
  expect(signals.crews.crew.stage).toBe('running');
  expect(signals.providers).toEqual({});
});
it('captures a short model call and completed run between screen refreshes without replaying it forever',()=>{
  const initial=mergeExecution(null,snapshot(),1000);
  const events=[event('run_started',1),event('model_started',2),event('model_finished',3,{status:'responded'}),event('run_finished',4,{status:'completed'})];
  const next=mergeExecution(initial,snapshot({cursor:4,events}),2000);
  expect(executionSignals(next,2100).crews.crew.stage).toBe('completed');
  expect(executionSignals(next,2100).providers.route.stage).toBe('responded');
  const repeat=mergeExecution(next,snapshot({cursor:4}),4000);
  expect(repeat.notices).toHaveLength(4);
  expect(executionSignals(repeat,15000).crews).toEqual({});
});
it('reports concurrent requests, stops stale progress and fences old backend/session streams',()=>{
  const feed=mergeExecution(null,snapshot({cursor:2,active_calls:[event('model_started',1),event('model_started',2,{crew_id:'other',run_id:'other'})]}),1000);
  expect(executionSignals(feed,1200).providers.route.count).toBe(2);
  expect(executionSignals(feed,10000).calls).toHaveLength(0);
  expect(executionSignals(feed,10000).providers).toEqual({});
  const reset=mergeExecution(feed,snapshot({stream_id:'new'}),11000);
  expect(executionSignals(reset,11001).crews).toEqual({});
  expect(validRuntimeSnapshot([])).toBe(false);
  expect(validRuntimeSnapshot(snapshot())).toBe(true);
});
it('reports a failed request/run without claiming a response or completed work',()=>{
  const feed=mergeExecution(null,snapshot({cursor:2,events:[event('model_finished',1,{status:'failed'}),event('run_finished',2,{status:'failed'})]}),1000);
  const signals=executionSignals(feed,1100);
  expect(signals.providers.route.stage).toBe('failed');
  expect(signals.crews.crew.stage).toBe('failed');
  expect(signals.calls).toHaveLength(0);
});
