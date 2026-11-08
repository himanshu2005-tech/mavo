import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CrewGraph } from "../components/CrewGraph";
import { describeAccess } from "../components/ResourceAccess";
import type { PoolResource } from "../types";
import { dayHeading, groupByDay } from "./TimelinePage";

const now = new Date(2026, 10, 2, 8, 0);
const at = (day: number, hour: number) => new Date(2026, 10, day, hour, 0).toISOString();
const event = (iso: string, title: string) => ({ at: iso, kind: "reminder" as const, crew_name: "Meals", title });

describe("Timeline", () => {
  it("names today and tomorrow and groups events by day in order", () => {
    expect(dayHeading(at(2, 13), now)).toBe("Today");
    expect(dayHeading(at(3, 9), now)).toBe("Tomorrow");
    const groups = groupByDay([event(at(2, 9), "Breakfast"), event(at(2, 13), "Lunch"), event(at(3, 9), "Breakfast")], now);
    expect(groups.map(g => [g.heading, g.events.length])).toEqual([["Today", 2], ["Tomorrow", 1]]);
  });
});

describe("Crew graph", () => {
  it("draws nothing for unconnected Crew and names every connected Crew", () => {
    expect(renderToStaticMarkup(<CrewGraph nodes={[{ id: "a", name: "Alone" }]} edges={[]} />)).toBe("");
    const html = renderToStaticMarkup(<CrewGraph nodes={[{ id: "a", name: "Trip watch", running: true }, { id: "b", name: "Leave for airport", status: "draft" }]} edges={[{ from: "a", to: "b" }]} />);
    expect(html).toContain("Trip watch");
    expect(html).toContain("Leave for airport");
    expect(html).toContain("Draft");
    expect(html).toContain("marker-end");
  });
});

const resource = (over: Partial<PoolResource>): PoolResource => ({ id: "gmail", name: "Gmail", group: "Google", kind: "google", description: "", connected: true, account: null, level: "write", can_act: true, reads: ["gmail:read"], readable: [], actions: [], ...over });

describe("Access wording", () => {
  it("says plainly what each level allows", () => {
    expect(describeAccess(resource({ connected: false }))).toBe("Not connected");
    expect(describeAccess(resource({ level: "off" }))).toBe("Mavo will not use this");
    expect(describeAccess(resource({ level: "read" }))).toBe("Crew can read. Actions are blocked.");
    expect(describeAccess(resource({ actions: [{ operation: "gmail.send", label: "Send", consequential: true, available: true, enabled_by_you: false }] }))).toBe("Crew can read and use 1 action.");
    expect(describeAccess(resource({ can_act: false, level: "read" }))).toBe("Crew can read.");
    expect(describeAccess(resource({ reads: [], level: "write", actions: [{ operation: "browser.open", label: "Open", consequential: false, available: true, enabled_by_you: false }] }))).toBe("Crew can use 1 action.");
  });
});
