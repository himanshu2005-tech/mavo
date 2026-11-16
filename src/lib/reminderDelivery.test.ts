import { describe, it, expect, vi } from 'vitest';
import { ALERT_PEEK_MS, ALERT_POLL_MS, ReminderDelivery, waitsOnYou } from './reminderDelivery';
import type { Reminder } from '../types';

const NOW = Date.parse('2026-11-09T14:00:00Z');
const alert = (id: string, age = 0): Reminder => ({ id, crew_id: 'news', crew_name: 'News', kind: 'crew_update', message: 'Verified new headlines', scheduled_for: new Date(NOW - age).toISOString(), local_date: '2026-11-09' });
function fixture() {
  let now = NOW;
  let visible: Pick<Reminder, 'id' | 'preview'> | null = null;
  const port = {
    signedIn: vi.fn(async () => true),
    pending: vi.fn(async () => visible),
    due: vi.fn(async () => [alert('one')]),
    show: vi.fn(async (r: Reminder) => { visible = r; }),
    close: vi.fn(async () => { visible = null; }),
    muted: vi.fn(() => false),
    missed: vi.fn(), now: () => now,
  };
  return { port, delivery: new ReminderDelivery(port), advance: (ms: number) => { now += ms; }, visible: (r: Reminder | null) => { visible = r; } };
}

describe('durable desktop alert consumer', () => {
  it('checks every second and displays a new alert once without dismissing it', async () => {
    expect(ALERT_POLL_MS).toBe(1000);
    const { delivery, port } = fixture();
    await delivery.check(); await delivery.check();
    expect(port.show).toHaveBeenCalledTimes(1);
    expect(port.show).toHaveBeenCalledWith(alert('one'));
    expect(port.close).not.toHaveBeenCalled();
  });
  it('does not let an unread card starve later alerts; keeps the old one in Missed', async () => {
    const { delivery, port, advance } = fixture();
    await delivery.check();
    port.due.mockResolvedValue([alert('one'), alert('two'), alert('three')]);
    await delivery.check(); expect(port.show).toHaveBeenCalledTimes(1);
    advance(ALERT_PEEK_MS); await delivery.check();
    expect(port.show).toHaveBeenLastCalledWith(alert('two'));
    expect(port.missed).toHaveBeenLastCalledWith([alert('one')]);
    advance(ALERT_PEEK_MS); await delivery.check();
    expect(port.show).toHaveBeenLastCalledWith(alert('three'));
    expect(port.missed).toHaveBeenLastCalledWith([alert('one'), alert('two')]);
  });
  it('retries failed native delivery instead of marking it shown', async () => {
    const { delivery, port } = fixture();
    port.show.mockRejectedValueOnce(Error('Window not ready'));
    await expect(delivery.check()).rejects.toThrow('Window not ready');
    await delivery.check(); expect(port.show).toHaveBeenCalledTimes(2);
  });
  it('does not redirect an in-progress question reply to a newer alert', async () => {
    const { delivery, port, advance } = fixture();
    const canReplace = vi.fn(() => false);
    Object.assign(port, { canReplace });
    await delivery.check(); advance(ALERT_PEEK_MS);
    port.due.mockResolvedValue([alert('one'), alert('two')]);
    await delivery.check(); expect(port.show).toHaveBeenCalledTimes(1);
    canReplace.mockReturnValue(true);
    await delivery.check(); expect(port.show).toHaveBeenLastCalledWith(alert('two'));
  });
  it('shows what needs you before news: a question or a change for your OK comes first', async () => {
    const { delivery, port } = fixture();
    const question: Reminder = { ...alert('ask'), kind: 'question', task_step_id: 'desk:t1' };
    const approval: Reminder = { ...alert('ok'), kind: 'approval', task_step_id: 'action:a1' };
    port.due.mockResolvedValue([alert('news'), question, approval]);
    await delivery.check();
    expect(port.show).toHaveBeenCalledWith(question);
    expect(waitsOnYou(approval)).toBe(true);
    expect(waitsOnYou(alert('news'))).toBe(false);
    expect(waitsOnYou({ kind: 'approval', task_step_id: null })).toBe(false);  // nothing to answer
  });
  it('leaves muted alerts pending and delivers promptly when unmuted', async () => {
    const { delivery, port } = fixture();
    port.muted.mockReturnValue(true); await delivery.check();
    expect(port.show).not.toHaveBeenCalled();
    port.muted.mockReturnValue(false); await delivery.check();
    expect(port.show).toHaveBeenCalledTimes(1);
  });
  it('retains old unread updates in Missed without replaying a day of popups', async () => {
    const { delivery, port } = fixture(); const old = alert('yesterday', 86_400_000);
    port.due.mockResolvedValue([old]); await delivery.check();
    expect(port.show).not.toHaveBeenCalled(); expect(port.missed).toHaveBeenLastCalledWith([old]);
  });
  it('restores an existing native popup after consumer restart without replaying it', async () => {
    const { delivery, port, visible } = fixture(); visible(alert('one'));
    await delivery.check(); expect(port.show).not.toHaveBeenCalled();
  });
  it('clears a revoked/deleted alert and then delivers the next valid one', async () => {
    const { delivery, port, visible } = fixture(); visible(alert('deleted'));
    await delivery.check(); expect(port.close).toHaveBeenCalledTimes(1);
    await delivery.check(); expect(port.show).toHaveBeenCalledWith(alert('one'));
  });
  it('does not interrupt a manual preview', async () => {
    const { delivery, port, visible } = fixture(); visible({ ...alert('preview'), preview: true });
    await delivery.check(); expect(port.show).not.toHaveBeenCalled(); expect(port.close).not.toHaveBeenCalled();
  });
  it('clears private delivery state on logout', async () => {
    const { delivery, port } = fixture(); port.signedIn.mockResolvedValue(false);
    await delivery.check(); expect(port.due).not.toHaveBeenCalled();
    expect(port.close).toHaveBeenCalled(); expect(port.missed).toHaveBeenLastCalledWith([]);
  });
  it('serializes overlapping checks and stops before delivery on unmount', async () => {
    const { delivery, port } = fixture();
    let resolve!: (value: boolean) => void;
    port.signedIn.mockImplementation(() => new Promise<boolean>(r => { resolve = r; }));
    const first = delivery.check(); await delivery.check();
    expect(port.signedIn).toHaveBeenCalledTimes(1);
    delivery.stop(); resolve(true); await first;
    expect(port.show).not.toHaveBeenCalled();
  });
});
