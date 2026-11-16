import {expect,it} from 'vitest';
import catalog from '../../docs/examples/practical-crew-catalog.json';
import hints from '../../docs/examples/connection-requirements.json';
import {exampleConnectionKeys} from './exampleConnections';
import {wiring} from '../components/crewWiring';

it('only decorates explicitly labeled practical examples and never grants access',()=>{
  expect(exampleConnectionKeys({name:'Example · Placements Portal Watch'})).toEqual([]);
  expect(exampleConnectionKeys({name:'Placements Portal Watch',responsibilities:'PRACTICAL EXAMPLE —'})).toEqual([]);
  const example={name:'Example · Placements Portal Watch',responsibilities:'PRACTICAL EXAMPLE —',permissions:['model:invoke'],configuration_plan:undefined};
  const before=JSON.stringify(example);
  expect(wiring(example)).toEqual({
    reads:[{key:'web',label:'Websites',icon:'globe',allowed:false,kind:'resource'}],
    does:[{key:'notify',label:'Desktop alerts',icon:'bell',allowed:false,kind:'action'}],
  });
  expect(JSON.stringify(example)).toBe(before);
  expect(wiring({...example,permissions:['model:invoke','action:browser.read','notifications:show']}).reads[0].allowed).toBe(true);
});

it('keeps suggestion names in the catalog and maps every suggestion to a known pending connection',()=>{
  const names=new Set(catalog.agents.map(entry=>entry.name));
  for (const [key,entries] of [...Object.entries(hints.resources),...Object.entries(hints.actions)]) {
    for (const name of entries) {
      expect(names.has(name)).toBe(true);
      const wires=wiring({name:`Example · ${name}`,responsibilities:'PRACTICAL EXAMPLE —',permissions:[],configuration_plan:undefined});
      expect([...wires.reads,...wires.does].some(wire=>wire.key===key&&!wire.allowed)).toBe(true);
    }
  }
});
