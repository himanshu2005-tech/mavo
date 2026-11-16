import type { Reminder } from '../types';
import { isFresh } from './time';

export const ALERT_POLL_MS = 1000;

/** An alert that waits for the person's answer: an agent's question, or a change for their OK. */
export const waitsOnYou = (r: Pick<Reminder, 'kind' | 'task_step_id'>) =>
  r.kind === 'question' || (r.kind === 'approval' && !!r.task_step_id?.startsWith('action:'));
export const ALERT_PEEK_MS = 9000;

interface DeliveryPort {
  signedIn: () => Promise<boolean>;
  pending: () => Promise<Pick<Reminder, 'id' | 'preview'> | null>;
  due: () => Promise<Reminder[]>;
  show: (reminder: Reminder) => Promise<void>;
  close: () => Promise<void>;
  muted: () => boolean;
  canReplace?: () => boolean;
  missed: (reminders: Reminder[]) => void;
  now?: () => number;
}

/** A single consumer in the always-on status window, independent of its presentation.
 * Showing an alert never dismisses it. Superseded unread cards remain in Missed.
 * A failed native show is retried; a long-running check cannot overlap another.
 */
export class ReminderDelivery {
  private shown = new Set<string>();
  private checking = false;
  private active = true;
  private visibleId: string | null = null;
  private visibleSince = 0;
  constructor(private port: DeliveryPort) {}
  stop() { this.active = false; }

  async check() {
    if (this.checking || !this.active) return;
    this.checking = true;
    try {
      const signedIn = await this.port.signedIn();
      if (!this.active) return;
      if (!signedIn) {
        this.shown.clear(); this.visibleId = null;
        this.port.missed([]);
        await this.port.close();
        return;
      }
      const [visible, reminders] = await Promise.all([this.port.pending(), this.port.due()]);
      if (!this.active) return;
      const now = (this.port.now ?? Date.now)();
      const ids = new Set(reminders.map(r => r.id));
      this.shown = new Set([...this.shown].filter(id => ids.has(id)));
      this.port.missed(reminders.filter(r => r.id !== visible?.id && (!isFresh(r.scheduled_for, now) || this.shown.has(r.id))));
      if (visible?.preview) return;
      if (visible && !ids.has(visible.id)) {
        await this.port.close();
        this.visibleId = null;
        return;
      }
      if (visible && this.visibleId !== visible.id) {
        this.visibleId = visible.id; this.visibleSince = now;
        this.shown.add(visible.id);
      }
      if (this.port.muted() || (visible && now - this.visibleSince < ALERT_PEEK_MS)) return;
      // Do not replace a question while the person is composing its reply.
      if (visible && this.port.canReplace?.() === false) return;
      // What needs the person (a question, a change waiting for their OK) comes before news.
      const fresh = reminders.filter(r => isFresh(r.scheduled_for, now) && !this.shown.has(r.id));
      const next = fresh.find(waitsOnYou) ?? fresh[0];
      if (!next || !this.active) return;
      await this.port.show(next);
      this.shown.add(next.id);
      this.visibleId = next.id; this.visibleSince = now;
      this.port.missed(reminders.filter(r => r.id !== next.id && (!isFresh(r.scheduled_for, now) || this.shown.has(r.id))));
    } finally {
      this.checking = false;
    }
  }
}
