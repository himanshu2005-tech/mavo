import {expect,it} from 'vitest';
import {validOnboarding} from './onboarding';

it('accepts persisted setup progress and never interprets missing progress as complete',()=>{
  const value={version:1,step:'welcome',revision:0,account_key:'account',completed_at:null,providers_skipped:false};
  expect(validOnboarding(value)).toBe(true);
  expect(validOnboarding({...value,step:'complete',completed_at:'2026-11-08T00:00:00Z'})).toBe(true);
  for(const wrong of [null,[],{}, {...value,step:'unknown'},{...value,revision:-1},
    {...value,account_key:''},{...value,version:2},{...value,providers_skipped:undefined},
    {...value,step:'complete',completed_at:null},{...value,step:'complete',completed_at:'invalid'}]) {
    expect(validOnboarding(wrong)).toBe(false);
  }
});
