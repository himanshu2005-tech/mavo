import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect,vi} from 'vitest';

const state=vi.hoisted(()=>({status:{service:'mavo'} as object|null,statusError:null as string|null}));
vi.mock('../store/app',()=>({useAppStore:(selector:(value:typeof state)=>unknown)=>selector(state)}));
import {StatusPill} from './StatusPill';

it('says nothing when the workspace is fine: no dot, no "ready" label',()=>{
  expect(renderToStaticMarkup(<StatusPill/>)).toBe('');
});
it('clearly identifies a workspace outage rather than a model error',()=>{
  state.statusError='offline';
  const view=renderToStaticMarkup(<StatusPill/>);
  expect(view).toContain('Workspace unavailable');
  expect(view).toContain('Mavo Core has a separate service connection');
  expect(view).not.toContain('rounded-full');
  state.statusError=null;
});
it('shows plain words while it first connects',()=>{
  state.status=null;
  expect(renderToStaticMarkup(<StatusPill/>)).toContain('Connecting…');
  state.status={service:'mavo'};
});
