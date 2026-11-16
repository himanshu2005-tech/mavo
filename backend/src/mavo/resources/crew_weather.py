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
    # The person's own place: the location saved in Mavo's Resources, never a guess.
    here: bool = False

    @model_validator(mode="after")
    def destination(self):
        if sum([self.query is not None, self.location is not None, self.here]) != 1:
            raise ValueError("Supply one of: a city query, destination coordinates, or here")
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


def _without_position(value):
    if isinstance(value, dict):
        return {
            k: _without_position(v) for k, v in value.items() if k not in {"latitude", "longitude"}
        }
    if isinstance(value, list):
        return [_without_position(v) for v in value]
    return value


async def saved_place_weather(factory, adapter):
    """The forecast where the person is: the place saved in Resources, used only while location
    is allowed there. Coordinates never reach the model."""
    from mavo.database import WorkspaceSetting

    with factory() as session:
        access = read_access(session)
        saved = session.get(WorkspaceSetting, "weather-location")
        place = dict(saved.value) if saved else None
    if not place:
        return {
            "here": True,
            "status": "unavailable",
            "error": "No location is saved in Mavo's Resources. "
            "Say so in one line; do not guess a place.",
        }
    if place.get("source") == "device" and not access.location:
        return {
            "here": True,
            "status": "unavailable",
            "error": "Location is off in Mavo's Resources. "
            "Say so in one line; do not guess a place.",
        }
    snapshot = await adapter.read_location(WeatherLocation.model_validate(place))
    return {
        "here": True,
        "status": "ok",
        "forecast": _without_position(snapshot.model_dump(mode="json")),
        "source": "Open-Meteo",
        "limitations": "Five-day daily forecast at the saved location.",
    }


async def lookup_weather(factory, crew, adapter, request):
    require_weather(factory, crew)
    if adapter is None:
        raise ActionPolicyError("Weather is unavailable in this runtime.")
    try:
        if request.here:
            return await saved_place_weather(factory, adapter)
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
