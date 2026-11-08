import type { WeatherLocation } from "../types";

export function detectDeviceLocation(): Promise<WeatherLocation> {
  if (!navigator.geolocation) return Promise.reject(new Error("Computer location is unavailable on this device."));
  return new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(
    position => resolve({
      name: "Current location", source: "device",
      latitude: Math.round(position.coords.latitude * 1000) / 1000,
      longitude: Math.round(position.coords.longitude * 1000) / 1000,
      accuracy_m: Math.round(position.coords.accuracy),
    }),
    reason => reject(new Error(reason.code === 1
      ? "Location was denied. Allow location access in Windows Settings, then return to Mavo to retry automatically."
      : "Windows could not determine your location. Check its location settings; Mavo retries automatically when you return.")),
    {timeout: 15000, maximumAge: 300000, enableHighAccuracy: false},
  ));
}
