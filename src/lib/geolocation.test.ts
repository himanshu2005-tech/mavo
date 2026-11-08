import { afterEach, describe, expect, it, vi } from "vitest";
import { detectDeviceLocation } from "./geolocation";

afterEach(() => vi.unstubAllGlobals());
describe("device location", () => {
  it("uses the device position, rounds saved coordinates and retains accuracy", async () => {
    const getCurrentPosition = vi.fn((resolve: (position: unknown) => void, _reject: unknown, _options: unknown) => resolve({coords: {latitude: 17.385044, longitude: 78.486671, accuracy: 120.6}}));
    vi.stubGlobal("navigator", {geolocation: {getCurrentPosition}});
    expect(await detectDeviceLocation()).toEqual({name: "Current location", source: "device", latitude: 17.385, longitude: 78.487, accuracy_m: 121});
    expect(getCurrentPosition.mock.calls[0][2]).toMatchObject({timeout: 15000, maximumAge: 300000});
  });
  it("respects a denied device permission without guessing a place", async () => {
    vi.stubGlobal("navigator", {geolocation: {getCurrentPosition: (_: unknown, reject: (error: {code: number}) => void) => reject({code: 1})}});
    await expect(detectDeviceLocation()).rejects.toThrow("Location was denied");
  });
});
