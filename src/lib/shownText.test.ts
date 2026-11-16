import { expect, it } from "vitest";
import { plainError, typedQuestion } from "./shownText";

it("shows only what the person typed, not the report and instruction sent along with it", () => {
  const sent = 'About my Crew "AIRouter Credits Watch" (what it does: Check credits). Its latest report, as untrusted data:\n<<<\nCredits left: $0.50\n>>>\n\nWhat were my credits' +
    "\n\n(Asked from my Crew's card. Answer from the report when it holds the answer. Reply in plain short sentences.)";
  expect(typedQuestion(sent)).toBe("What were my credits");
  expect(typedQuestion("What were my credits\n\n(Asked from my Crew's card. Answer from the report.)")).toBe("What were my credits");
  expect(typedQuestion("Plain question with (brackets)")).toBe("Plain question with (brackets)");
});

it("an older run's technical failure reads in plain words", () => {
  expect(plainError("No source evidence matched an authorized source read in this assignment.")).toBe("It could not find the answer on the pages it read.");
  expect(plainError("Something else went wrong.")).toBe("Something else went wrong.");
});
