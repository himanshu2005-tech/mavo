import { ApiError } from './api';
import type { FirebaseStatus } from '../types';

export const SESSION_STARTUP_GRACE_MS = 8000;
export const SESSION_STARTUP_RETRY_MS = 500;
export const SESSION_POLL_MS = 5000;
export type SessionState = {
  phase: 'checking' | 'ready' | 'unavailable';
  status: FirebaseStatus | null;
  error: string | null;
};
export const initialSessionState = (): SessionState => ({phase: 'checking', status: null, error: null});

/** An unreachable backend is not an authoritative logout. Never clear account data here. */
export class SessionStartup {
  private started: number;
  private confirmed = false;
  constructor(private now: () => number = Date.now) { this.started = now(); }
  retry() { this.started = this.now(); this.confirmed = false; }
  success(status: FirebaseStatus): SessionState {
    if (typeof status?.signed_in !== 'boolean' || typeof status?.configured !== 'boolean') {
      throw new Error('Mavo returned an invalid account status. Retry the connection.');
    }
    this.confirmed = true;
    return {phase: 'ready', status, error: null};
  }
  failure(error: unknown): SessionState {
    const transient = error instanceof ApiError && [0, 502, 503, 504].includes(error.status);
    if (!this.confirmed && transient && this.now() - this.started < SESSION_STARTUP_GRACE_MS) return initialSessionState();
    return {phase: 'unavailable', status: null, error: transient
      ? 'Mavo’s local service is not responding yet. Retry, or restart Mavo if this continues. Your saved data has not been removed.'
      : error instanceof Error ? error.message : 'Could not verify your account. Retry the connection.'};
  }
}
