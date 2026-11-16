import { describe, expect, it } from "vitest";
import { daysLine, deskLine } from "./DeskSettingsLine";

describe("the agent's hours in words", () => {
  it("says a run of days as a range, and other days one by one", () => {
    expect(daysLine(["mon", "tue", "wed", "thu", "fri"])).toBe("Mon–Fri");
    expect(daysLine(["sat", "mon", "wed"])).toBe("Mon, Wed, Sat");
    expect(daysLine(["mon", "tue"])).toBe("Mon, Tue");
  });

  it("uses defaults for what is not set", () => {
    expect(deskLine({})).toBe("Mon–Fri, 9:00 AM–6:00 PM, your time zone · 30 min meetings");
    expect(deskLine({ start: "12:30", end: "00:30" })).toContain("12:30 PM–12:30 AM");
  });
});
