from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from mavo.schemas import WeatherCurrent, WeatherDay, WeatherLocation, WeatherSnapshot


class WeatherResourceError(RuntimeError):
    pass


class OpenMeteoWeatherAdapter:
    id = "weather.open-meteo"

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_seconds: int = 900,
    ) -> None:
        self.transport = transport
        self.cache_seconds = cache_seconds
        self.cache: dict[str, tuple[float, WeatherSnapshot]] = {}

    async def search(self, query: str, country_code: str | None = None) -> list[WeatherLocation]:
        parts = [part.strip() for part in query.split(",") if part.strip()]
        if not parts:
            raise WeatherResourceError("Enter a city name")
        params = {"name": parts[0], "count": 10, "language": "en", "format": "json"}
        if country_code:
            params["countryCode"] = country_code.upper()
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self.transport) as client:
                response = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search", params=params
                )
                response.raise_for_status()
                result = []
                for place in response.json().get("results", []):
                    location = WeatherLocation(
                        id=place.get("id"),
                        name=place["name"],
                        region=place.get("admin1", ""),
                        country=place.get("country", ""),
                        country_code=place.get("country_code", ""),
                        latitude=place["latitude"],
                        longitude=place["longitude"],
                        timezone=place.get("timezone"),
                    )
                    description = f"{location.name} {location.region} {location.country}".casefold()
                    if (
                        country_code
                        and location.country_code
                        and location.country_code.upper() != country_code.upper()
                    ):
                        continue
                    if all(part.casefold() in description for part in parts[1:]):
                        result.append(location)
                # Prefer an exact city name over prefix matches such as its airport.
                # Same-named cities in different regions remain ambiguous.
                exact = [p for p in result if p.name.casefold() == parts[0].casefold()]
                return exact or result
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise WeatherResourceError(
                "Location search is unavailable. Try Use my location."
            ) from exc

    async def read(self, query: str) -> WeatherSnapshot:
        places = await self.search(query)
        if not places:
            raise WeatherResourceError("No matching location was found")
        if len(places) > 1:
            raise WeatherResourceError("Several places match. Select the city, state, and country.")
        return await self.read_location(places[0])

    async def read_location(self, place: WeatherLocation) -> WeatherSnapshot:
        cache_key = f"{place.latitude:.3f}:{place.longitude:.3f}"
        label = ", ".join(dict.fromkeys(x for x in (place.name, place.region, place.country) if x))
        cached = self.cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1].model_copy(update={"cached": True, "location": label})
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self.transport) as client:
                forecast_response = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": place.latitude,
                        "longitude": place.longitude,
                        "current": (
                            "temperature_2m,apparent_temperature,precipitation,"
                            "weather_code,wind_speed_10m,is_day"
                        ),
                        "daily": (
                            "weather_code,temperature_2m_max,temperature_2m_min,"
                            "precipitation_probability_max,sunrise,sunset"
                        ),
                        "timezone": "auto",
                        "forecast_days": 5,
                    },
                )
                forecast_response.raise_for_status()
                data = forecast_response.json()
        except WeatherResourceError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise WeatherResourceError("The weather service is currently unavailable") from exc

        try:
            current = data["current"]
            daily = data["daily"]
            forecast = [
                WeatherDay(
                    date=date,
                    weather_code=daily["weather_code"][index],
                    temperature_max_c=daily["temperature_2m_max"][index],
                    temperature_min_c=daily["temperature_2m_min"][index],
                    precipitation_probability_max=daily["precipitation_probability_max"][index],
                    sunrise=daily["sunrise"][index],
                    sunset=daily["sunset"][index],
                )
                for index, date in enumerate(daily["time"])
            ]
            snapshot = WeatherSnapshot(
                location=label,
                latitude=place.latitude,
                longitude=place.longitude,
                timezone=data["timezone"],
                fetched_at=datetime.now(UTC),
                cached=False,
                current=WeatherCurrent(
                    temperature_c=current["temperature_2m"],
                    apparent_temperature_c=current["apparent_temperature"],
                    precipitation_mm=current["precipitation"],
                    wind_speed_kmh=current["wind_speed_10m"],
                    weather_code=current["weather_code"],
                    is_day=bool(current["is_day"]),
                ),
                forecast=forecast,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherResourceError("The weather service returned invalid data") from exc
        if len(self.cache) >= 128:
            self.cache.pop(next(iter(self.cache)))
        self.cache[cache_key] = (time.monotonic() + self.cache_seconds, snapshot)
        return snapshot
