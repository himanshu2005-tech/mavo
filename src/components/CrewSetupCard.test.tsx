import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { CrewSetupCard } from "./CrewSetupCard";
import { CrewReplyBox } from "./CrewReplyBox";

describe("existing manual Crew without a coordinator plan", () => {
  const crew = {
    id: "manual-crew", permissions: ["model:invoke"], configuration_plan: {},
  } as CrewInstance;

  it("renders without treating the database's empty JSON object as a setup plan", () => {
    expect(renderToString(<CrewSetupCard crew={crew} />)).toBe("");
  });

  it("keeps the Crew message box available", () => {
    expect(renderToString(<CrewReplyBox crew={crew} />)).toContain("Message this Crew");
  });
});
