import type { CoreProblem } from '../lib/api';

export function ChatFailure({error,problem,requestId,onRetry,onSettings}:{error:string;problem?:CoreProblem;requestId:string;onRetry:()=>void;onSettings:()=>void}) {
  return <article role="alert" aria-label="Mavo Core could not answer" className="surface p-4 text-sm">
    <h3 className="font-semibold text-zinc-100">Mavo Core couldn’t answer</h3>
    <p className="mt-2 leading-6 text-zinc-300">{problem?.message??error}</p>
    {problem?.action&&<p className="mt-2 leading-6 text-zinc-400">{problem.action}</p>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {problem?.retryable!==false&&<button type="button" className="btn-secondary" onClick={onRetry}>Send again</button>}
      {problem?.code==='core_sign_in_required'&&<button type="button" className="btn-secondary" onClick={onSettings}>Open Settings</button>}
    </div>
    <p className="mt-3 break-all text-xs text-zinc-500">Reference: {problem?.request_id??requestId}</p>
  </article>;
}
