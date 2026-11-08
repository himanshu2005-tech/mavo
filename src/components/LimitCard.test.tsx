import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { LimitCard, limitOf } from "./LimitCard";

const crew = (over: Partial<CrewInstance>) => ({ id: "q", name: "Motivational Quotes", max_model_calls: 1, daily_call_limit: 48, question: null, last_runtime_error: null, ...over }) as unknown as CrewInstance;

it("reads the limit that stopped an agent, from the agent or from the old wording", () => {
  expect(limitOf(crew({ limit: { kind: "run", current: 2, suggested: 4 } }))).toEqual({ kind: "run", current: 2, suggested: 4 });
  expect(limitOf(crew({ question: "This follow-up needs a remaining session model-call budget. Adjust this Crew's access settings or tell it another way to proceed." }))).toEqual({ kind: "run", current: 1, suggested: 4 });
  expect(limitOf(crew({ question: "No daily model-call budget remains for a follow-up." }))).toEqual({ kind: "day", current: 48, suggested: 96 });
  expect(limitOf(crew({ question: "Which inbox label counts as urgent?" }))).toBeNull();
});

it("says it plainly and offers the one change that lets it go on", () => {
  const run = renderToStaticMarkup(<LimitCard crew={crew({ limit: { kind: "run", current: 2, suggested: 4 } })} />);
  for (const text of ["Stopped before it could finish", "Motivational Quotes may think through only 2 steps", "Allow 4 per run"]) expect(run).toContain(text);
  expect(run).not.toContain("budget");
  const day = renderToStaticMarkup(<LimitCard crew={crew({ limit: { kind: "day", current: 48, suggested: 96 } })} />);
  for (const text of ["Used today&#x27;s AI calls", "carries on by itself tomorrow", "Raise to 96 a day"]) expect(day).toContain(text);
});
