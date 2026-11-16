import { describe, it, expect } from 'vitest';
import { ApiError } from './api';
import { SessionStartup, SESSION_STARTUP_GRACE_MS } from './sessionStartup';
import type { FirebaseStatus } from '../types';

const status: FirebaseStatus = {configured:true,signed_in:true,email:null,name:null,phase:'idle',error:null};
describe('account startup readiness', () => {
  it('keeps a brief local connection failure in loading, not signed out', () => {
    let now = 0; const startup = new SessionStartup(() => now);
    for (now = 0; now < 3000; now += 500) {
      expect(startup.failure(new ApiError('Failed to fetch',0))).toEqual({phase:'checking',status:null,error:null});
    }
    expect(startup.success(status)).toEqual({phase:'ready',status,error:null});
  });
  it('shows a bounded actionable failure and supports a fresh retry', () => {
    let now = 0; const startup = new SessionStartup(() => now);
    now = SESSION_STARTUP_GRACE_MS;
    expect(startup.failure(new ApiError('Failed to fetch',0))).toMatchObject({phase:'unavailable',status:null});
    expect(startup.failure(new ApiError('Failed to fetch',0)).error).not.toContain('Failed to fetch');
    startup.retry();
    expect(startup.failure(new ApiError('Failed to fetch',0)).phase).toBe('checking');
    expect(startup.success({...status,signed_in:false}).status?.signed_in).toBe(false);
  });
  it('does not hide authorization or malformed response errors as warm-up', () => {
    const startup = new SessionStartup(() => 0);
    expect(startup.failure(new ApiError('Session rejected',401))).toMatchObject({phase:'unavailable',error:'Session rejected'});
    expect(() => startup.success({signed_in:'yes'} as unknown as FirebaseStatus)).toThrow('invalid account status');
  });
  it('does not treat a later connection failure as a confirmed logout', () => {
    const startup = new SessionStartup(() => 0); startup.success(status);
    expect(startup.failure(new ApiError('Offline',0))).toMatchObject({phase:'unavailable',status:null});
    expect(startup.success(status).status?.signed_in).toBe(true);
  });
});
