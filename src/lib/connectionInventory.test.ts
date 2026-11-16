import {it,expect} from 'vitest';
import {connectionInventory,mergeConnections} from './connectionInventory';
import type {PoolResource} from '../types';

const resource=(over:Partial<PoolResource>={}):PoolResource=>({id:'gmail',name:'Gmail',group:'Google',kind:'google',description:'Mail',connected:true,account:null,level:'read',can_act:true,reads:['gmail:read'],readable:['gmail:read'],actions:[],...over});
it('shows connected services without requiring any Crew or granting access',()=>{
  const nodes=connectionInventory([resource()]);
  expect(nodes).toHaveLength(1);
  expect(nodes[0]).toMatchObject({key:'google.gmail',connectionState:'Connected',users:[],allowed:false});
});
it('does not pretend Google sign-in authorized all Google services',()=>{
  expect(connectionInventory([resource({id:'calendar',readable:[],actions:[]})])).toEqual([]);
  expect(connectionInventory([resource({connected:false})])).toEqual([]);
});
it('keeps connected-but-disabled accounts visible and does not enable actions',()=>{
  const rows=connectionInventory([resource({level:'off',actions:[{operation:'gmail.send',label:'Send',available:true,enabled_by_you:true,consequential:true}]})]);
  expect(rows.map(n=>n.kind)).toEqual(['resource']);
  expect(rows[0].connectionState).toBe('Access off');
});
it('shows only enabled, available action inventory under the resource ceiling',()=>{
  const rows=connectionInventory([resource({level:'write',actions:[
    {operation:'gmail.send',label:'Send',available:true,enabled_by_you:true,consequential:true},
    {operation:'gmail.archive',label:'Archive',available:true,enabled_by_you:false,consequential:false},
  ]})]);
  expect(rows.map(n=>n.key)).toEqual(['google.gmail','gmail.send']);
  expect(rows.every(n=>!n.allowed&&n.users.length===0)).toBe(true);
});
it('merges inventory metadata with real grants without creating phantom agent links',()=>{
  const idle=connectionInventory([resource()]);
  const combined=mergeConnections([{...idle[0],users:['c'],allowed:true,pendingUsers:[]}],idle);
  expect(combined).toHaveLength(1);
  expect(combined[0]).toMatchObject({users:['c'],allowed:true,connectionState:'Connected'});
});
