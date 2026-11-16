import {ArrowLeft,ArrowRight,Check,KeyRound,Layers,LoaderCircle,Plus,ShieldCheck} from 'lucide-react';
import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {api} from '../lib/api';
import {validOnboarding,type OnboardingState} from '../lib/onboarding';
import {RouteForm} from '../pages/SettingsPage';
import {ResourcesPage} from '../pages/ResourcesPage';
import {clearWorkspace,useAppStore} from '../store/app';
import type {ModelRoute} from '../types';

const steps=['welcome','providers','resources'] as const;
const titles={welcome:'Welcome to Mavo',providers:'Model providers',resources:'Resources'};

/** First-use setup is server-persisted and account-bound, never a global localStorage flag. */
export function OnboardingGate({children}:{children:ReactNode}) {
  const [state,setState]=useState<OnboardingState|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [routes,setRoutes]=useState<ModelRoute[]>([]);
  const [routesLoaded,setRoutesLoaded]=useState(false);
  const [routeError,setRouteError]=useState<string|null>(null);
  const [showForm,setShowForm]=useState(false);
  const active=useRef(true);
  const load=useCallback(async()=>{
    setError(null);
    try {
      const value=await api.onboarding();
      if (!validOnboarding(value)) throw Error('Setup returned an invalid response. Please retry.');
      if (active.current) setState(value);
    } catch (reason) {if(active.current)setError(reason instanceof Error?reason.message:'Could not load setup.');}
  },[]);
  useEffect(()=>{active.current=true;void load();return()=>{active.current=false;};},[load]);
  const refreshRoutes=useCallback(async()=>{
    setRoutesLoaded(false);setRouteError(null);
    try {
      const [saved,status]=await Promise.all([api.routes(),api.status()]);
      if(active.current) {
        setRoutes(saved);setRoutesLoaded(true);
        useAppStore.getState().setRoutes(saved);useAppStore.getState().setStatus(status);
      }
    } catch(reason) {if(active.current)setRouteError(reason instanceof Error?reason.message:'Could not load providers.');}
  },[]);
  useEffect(()=>{if(state?.step==='providers')void refreshRoutes();},[state?.step,refreshRoutes]);
  async function advance(action:'next'|'back'|'skip') {
    if(!state||busy)return;
    setBusy(true);setError(null);
    try {
      const value=await api.advanceOnboarding(action,state.revision);
      if(!validOnboarding(value)||value.account_key!==state.account_key)throw Error('Your setup session changed. Reload setup to continue.');
      if(active.current){setState(value);setShowForm(false);}
    } catch(reason){if(active.current)setError(reason instanceof Error?reason.message:'Could not save setup.');}
    finally{if(active.current)setBusy(false);}
  }
  async function signOut() {
    if(busy||showForm)return;
    setBusy(true);setError(null);
    try {await api.firebaseSignOut();clearWorkspace();window.dispatchEvent(new Event('mavo-session-changed'));}
    catch(reason){if(active.current){setError(reason instanceof Error?reason.message:'Could not sign out.');setBusy(false);}}
  }
  if(state?.step==='complete')return children;
  const ready=routesLoaded&&routes.some(route=>route.has_credential);
  return <main className="onboarding" aria-label="Set up Mavo">
    <div className="onboarding-shell">
      <header className="onboarding-brand"><span aria-hidden="true">M</span><b>Mavo setup</b><button className="btn-quiet" disabled={busy||showForm} onClick={()=>void signOut()}>Sign out</button></header>
      {!state?<section className="onboarding-loading">
        {error?<><h1>Let’s get setup working.</h1><p role="alert">{error}</p><button className="btn-secondary" onClick={()=>void load()}>Retry</button></>
          :<p role="status"><LoaderCircle size={16} className="animate-spin"/>Preparing your workspace…</p>}
      </section>:<>
        <ol className="onboarding-progress" aria-label="Setup progress">{steps.map((step,index)=><li key={step} aria-current={state.step===step?'step':undefined} data-done={steps.indexOf(state.step as typeof steps[number])>index}>
          <span>{steps.indexOf(state.step as typeof steps[number])>index?<Check size={13}/>:index+1}</span>{step==='welcome'?'Welcome':step==='providers'?'Providers':'Resources'}
        </li>)}</ol>
        <section className="onboarding-content" key={state.step}>
          <div className="onboarding-intro"><h1>{titles[state.step]}</h1>
            <p>{state.step==='welcome'?'Set up your models and connections. You can change these later in Settings.':state.step==='providers'?'Add a provider, model and API key. Local Ollama does not need a key.':'Choose what to connect. Each Crew still needs its own access.'}</p>
          </div>
          {state.step==='welcome'&&<div className="onboarding-principles">
            <article><KeyRound size={17}/><h2>Models</h2><p>Save the connections your Crew will use.</p></article>
            <article><Layers size={17}/><h2>Resources</h2><p>Connect accounts or enable local information.</p></article>
            <article><ShieldCheck size={17}/><h2>Permissions</h2><p>Nothing gets access automatically.</p></article>
          </div>}
          {state.step==='providers'&&<>
            {routeError&&<div className="notice error-notice" role="alert">{routeError}<button className="btn-quiet" onClick={()=>void refreshRoutes()}>Retry</button></div>}
            {!routesLoaded&&!routeError&&<p role="status" className="onboarding-inline-status"><LoaderCircle size={14} className="animate-spin"/>Loading providers…</p>}
            <div className="onboarding-routes">{routes.map(route=><article key={route.id}><KeyRound size={17}/><div><h2>{route.name}</h2><p>{route.model}</p></div><span>{route.has_credential?'Saved':'Key needed'}</span></article>)}</div>
            {showForm?<RouteForm existing={null} onClose={()=>{setShowForm(false);void refreshRoutes();}}/>
              :<button className="onboarding-add" onClick={()=>setShowForm(true)}><Plus size={17}/>{routes.length?'Add another provider':'Add a provider'}</button>}
            <p className="onboarding-note">Cloud usage is billed by your provider. Keys are encrypted by Windows. Saving a model does not send a paid test request.</p>
          </>}
          {state.step==='resources'&&<ResourcesPage setup/>}
        </section>
        {error&&<div className="notice error-notice" role="alert">{error}<button className="btn-quiet" onClick={()=>void load()}>Reload setup</button></div>}
        <footer className="onboarding-footer">
          {state.step!=='welcome'?<button className="btn-quiet" disabled={busy} onClick={()=>void advance('back')}><ArrowLeft size={14}/>Back</button>:<span className="onboarding-note">Step 1 of 3</span>}
          <div>{state.step==='providers'&&!ready&&<button className="btn-quiet" disabled={busy||showForm} onClick={()=>void advance('skip')}>Set up later</button>}
            <button className="btn-primary" disabled={busy||(state.step==='providers'&&(!ready||showForm))} onClick={()=>void advance('next')}>
              {busy?<LoaderCircle size={14} className="animate-spin"/>:null}{state.step==='resources'?'Finish':'Continue'}{!busy&&<ArrowRight size={14}/>}</button>
          </div>
        </footer>
      </>}
    </div>
  </main>;
}
