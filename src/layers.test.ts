import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const sheets = ["styles.css", "studio.css", "art.css", "report.css"].map((name) =>
  readFileSync(new URL("./" + name, import.meta.url), "utf8"),
).join("\n");

/** The declaration of the first rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  const start = sheets.split("\n").find((line) => line.trimStart().startsWith(selector + " {"));
  if (!start) throw new Error("No CSS rule for " + selector);
  return start;
}

describe("the Crew drawer and dialogs always cover the window", () => {
  // A transform animation that keeps filling after it ends (`both` or `forwards`) turns its
  // element into the containing block of every position:fixed descendant. The drawer backdrop
  // then covers only the page area, ends short at the bottom and scrolls away with the page.
  const wrappers = [".page-enter", ".page-content > *", ".birth", ".birth > *", ".drawer"];
  for (const selector of wrappers) {
    it(selector + " does not keep its animation applied after it ends", () => {
      const declaration = rule(selector);
      expect(declaration).toMatch(/animation:/);
      expect(declaration).not.toMatch(/animation:[^;}]*\b(?:both|forwards)\b/);
    });
  }

  it("the drawer backdrop is fixed to the window", () => {
    expect(rule(".drawer-backdrop")).toMatch(/position:\s*fixed;\s*inset:\s*0/);
  });
});
