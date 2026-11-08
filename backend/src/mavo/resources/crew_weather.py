"""Bounded read-only forecasts for arbitrary destinations, never device location."""

import asyncio

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mavo.database import CrewInstance
from mavo.google_actions import ActionPolicyError
from mavo.resource_access import read_access
from mavo.resources.weather import WeatherResourceError
from mavo.schemas import WeatherLocation


class WeatherLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, min_length=2, max_length=160)
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    location: WeatherLocation | None = None

    @model_validator(mode="after")
    def destination(self):
        if (self.query is None) == (self.location is None):
            raise ValueError("Supply a city query or explicit destination coordinates, not both")
        if self.location and self.location.source != "manual":
            raise ValueError("Weather lookup cannot request device location")
        return self


def require_weather(factory, crew):
    with factory() as session:
        current = session.get(CrewInstance, crew.id)
        if (
            not current
            or not current.is_running
            or current.runtime_generation != crew.runtime_generation
        ):
            raise asyncio.CancelledError()
        if "weather:read" not in current.permissions or not read_access(session).weather:
            raise ActionPolicyError(
                "Allow Weather in Resources and in this Crew's access settings."
            )


async def lookup_weather(factory, crew, adapter, request):
    require_weather(factory, crew)
    if adapter is None:
        raise ActionPolicyError("Weather is unavailable in this runtime.")
    try:
        location = request.location
        if location is None:
            places = await adapter.search(request.query, request.country_code)
            require_weather(factory, crew)
            if len(places) != 1:
                return {
                    "query": request.query,
                    "status": "ambiguous" if places else "not_found",
                    "matches": [p.model_dump(mode="json") for p in places[:5]],
                    "message": "Ask for the exact city/state/country; never guess a forecast.",
                }
            location = places[0]
        snapshot = await adapter.read_location(location)
        require_weather(factory, crew)
        return {
            "status": "ok",
            "forecast": snapshot.model_dump(mode="json"),
            "source": "Open-Meteo",
            "limitations": "Five-day daily forecast; rain does not prove flight disruption.",
        }
    except WeatherResourceError as exc:
        require_weather(factory, crew)
        return {"status": "unavailable", "error": str(exc)}
