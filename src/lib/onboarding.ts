export type SetupStep='welcome'|'providers'|'resources'|'complete';
export interface OnboardingState {
  version:1;step:SetupStep;revision:number;account_key:string;
  completed_at:string|null;providers_skipped:boolean;
}

/** Fail closed on absent/malformed progress; never silently bypass first-use setup. */
export function validOnboarding(value:unknown):value is OnboardingState {
  if (!value||typeof value!=='object') return false;
  const state=value as OnboardingState;
  return state.version===1&&['welcome','providers','resources','complete'].includes(state.step)
    &&Number.isInteger(state.revision)&&state.revision>=0&&typeof state.account_key==='string'
    &&state.account_key.length>0&&typeof state.providers_skipped==='boolean'
    &&(state.completed_at===null||typeof state.completed_at==='string')
    &&(state.step!=='complete'||(typeof state.completed_at==='string'&&Number.isFinite(Date.parse(state.completed_at))));
}
