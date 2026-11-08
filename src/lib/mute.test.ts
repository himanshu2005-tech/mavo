import { beforeEach, describe, expect, it, vi } from "vitest";
import { MUTE_CHOICES, countdown, isMuted, minutesLeft, muteFor, mutedUntil, secondsLeft, unmute } from "./mute";

function fakeStorage() {
  const items = new Map<string, string>();
  return { getItem: (k: string) => items.get(k) ?? null, setItem: (k: string, v: string) => void items.set(k, v), removeItem: (k: string) => void items.delete(k) };
}

describe("mute", () => {
  const heard: string[] = [];
  beforeEach(() => {
    heard.length = 0;
    vi.stubGlobal("localStorage", fakeStorage());
    vi.stubGlobal("window", { dispatchEvent: (e: Event) => { heard.push(e.type); return true; } });
    vi.stubGlobal("Event", class { constructor(public type: string) {} });
  });

  it("offers short, round choices, five minutes first", () => {
    expect(MUTE_CHOICES.map(c => c.minutes)).toEqual([5, 15, 60]);
  });

  it("is not muted until asked, and counts down from the moment it is", () => {
    const start = 1_000_000;
    expect(isMuted(start)).toBe(false);
    muteFor(5, start);
    expect(isMuted(start + 1)).toBe(true);
    expect(secondsLeft(start)).toBe(300);
    expect(minutesLeft(start)).toBe(5);
    expect(secondsLeft(start + 272_000)).toBe(28);
    expect(minutesLeft(start + 272_000)).toBe(1);
    expect(heard).toEqual(["mavo-mute-changed"]);
  });

  it("ends by itself when the time is up", () => {
    muteFor(5, 0);
    expect(isMuted(299_999)).toBe(true);
    expect(isMuted(300_000)).toBe(false);
    expect(mutedUntil(300_000)).toBeNull();
    expect(secondsLeft(400_000)).toBe(0);
  });

  it("can be lifted early, and tells every window", () => {
    muteFor(60, 0);
    unmute();
    expect(isMuted(1)).toBe(false);
    expect(heard).toEqual(["mavo-mute-changed", "mavo-mute-changed"]);
  });

  it("shows the time left as minutes and seconds", () => {
    expect(countdown(272)).toBe("4:32");
    expect(countdown(60)).toBe("1:00");
    expect(countdown(9)).toBe("0:09");
    expect(countdown(-5)).toBe("0:00");
  });

  it("is simply off when the browser will not store anything", () => {
    vi.stubGlobal("localStorage", { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } });
    expect(isMuted()).toBe(false);
    expect(() => muteFor(5)).not.toThrow();
    expect(() => unmute()).not.toThrow();
  });
});
