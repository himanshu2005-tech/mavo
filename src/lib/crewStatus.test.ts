import { expect, it } from "vitest";
import { workerLabel } from "./crewStatus";
import type { CrewInstance } from "../types";

it("does not call an enabled but blocked Crew Running",()=>{
  const crew={status:"active",is_running:true,runtime_state:"needs_input"} as CrewInstance;
  expect(workerLabel(crew)).toBe("Needs input");
  expect(workerLabel({...crew,runtime_state:"waiting_browser"})).toBe("Waiting for browser");
});
