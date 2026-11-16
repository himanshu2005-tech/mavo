import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { LimitCard, limitOf } from "./LimitCard";

const crew = (over: Partial<CrewInstance>) => ({ id: "q", name: "Motivational Quotes", max_model_calls: 1, daily_call_limit: 500, question: null, last_runtime_error: null, ...over }) as unknown as CrewInstance;

it("only the daily safety limit is shown: per-run budgets are gone", () => {
  expect(limitOf(crew({ limit: { kind: "run", current: 2, suggested: 4 } }))).toBeNull();
  expect(limitOf(crew({ question: "This follow-up needs a remaining session model-call budget." }))).toBeNull();
  expect(limitOf(crew({ question: "No daily model-call budget remains for a follow-up." }))).toEqual({ kind: "day", current: 500, suggested: 500 });
  expect(limitOf(crew({ question: "Which inbox label counts as urgent?" }))).toBeNull();
});

it("says it plainly, with nothing to set", () => {
  const day = renderToStaticMarkup(<LimitCard crew={crew({ limit: { kind: "day", current: 500, suggested: 500 } })} />);
  for (const text of ["Used today&#x27;s AI calls", "daily safety limit of 500", "carries on by itself tomorrow"]) expect(day).toContain(text);
  expect(day).not.toContain("<button");
  expect(day).not.toContain("budget");
});
