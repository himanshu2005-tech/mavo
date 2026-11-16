import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from './api';
import {useAppStore} from '../store/app';
import type {CoreStatus} from '../types';

/** Health only: reconnect without ever replaying a chat/model request. */
export function useCoreConnection() {
  const [core,setCore]=useState<CoreStatus|null>(null);
  const [checking,setChecking]=useState(false);
  const alive=useRef(false), fetching=useRef(false);
  const generation=useRef(0);
  const timer=useRef<number|undefined>(undefined);
  const epoch=useAppStore(s=>s.workspaceEpoch);
  const check=useCallback(async(refresh=false)=>{
    if(fetching.current||!alive.current)return;
    fetching.current=true;setChecking(true);
    window.clearTimeout(timer.current);
    const workspace=useAppStore.getState().workspaceEpoch;
    const request=generation.current;
    const current=()=>alive.current&&request===generation.current&&workspace===useAppStore.getState().workspaceEpoch;
    let connected=false;
    try {
      const status=await api.coreStatus(refresh);
      if(typeof status?.connected!=='boolean'||typeof status?.configured!=='boolean')throw Error('Invalid Core health response');
      connected=status.connected;
      if(current())setCore(status);
    }catch {
      if(current())setCore({configured:true,connected:false,provider:'Mavo Core',model:null,error:'Could not check Mavo Core. Retrying the connection…'});
    }finally {
      if(current()){
        fetching.current=false;
        setChecking(false);
        timer.current=window.setTimeout(()=>void check(),connected?10_000:2_000);
      }
    }
  },[]);
  useEffect(()=>{
    generation.current++;alive.current=true;fetching.current=false;void check();
    return()=>{alive.current=false;generation.current++;fetching.current=false;window.clearTimeout(timer.current);};
  },[check,epoch]);
  return {core,checking,checkConnection:()=>check(true)};
}
