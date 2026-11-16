// Browser-test harness only: real setup component, isolated fixture state and intercepted API.
import React from 'react';
import {createRoot} from 'react-dom/client';
import {CrewSetupCard} from '../src/components/CrewSetupCard';
import {clearWorkspace,useAppStore} from '../src/store/app';
import type {CrewInstance,ModelRoute} from '../src/types';

export function mountSetup(crew:CrewInstance,routes:ModelRoute[]){
  clearWorkspace();
  useAppStore.getState().setRoutes(routes);
  useAppStore.getState().upsertCrew(crew);
  const host=document.createElement('div');
  host.id='setup-probe';host.style.cssText='position:fixed;inset:0;z-index:99999;overflow:auto;background:#111;padding:24px';
  document.body.append(host);
  function Harness(){
    const current=useAppStore(state=>[...state.setupDrafts,...state.crew].find(item=>item.id===crew.id));
    return current?<CrewSetupCard key={current.id+JSON.stringify(current.configuration_plan)} crew={current}/>:null;
  }
  createRoot(host).render(<Harness/>);
}
