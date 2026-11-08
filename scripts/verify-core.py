"""Live coordinator/agent verification plus unambiguous Hyderabad weather lookup."""
import asyncio
import runpy
from pathlib import Path

from mavo.resources.weather import OpenMeteoWeatherAdapter


async def weather():
    adapter = OpenMeteoWeatherAdapter()
    places = await adapter.search("Hyderabad")
    selected = next(p for p in places if p.country_code == "IN" and p.region == "Telangana")
    forecast = await adapter.read_location(selected)
    assert forecast.location == "Hyderabad, Telangana, India"
    print("Hyderabad, Telangana, India live weather: PASS")


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("verify-coordinator.py")), run_name="__main__")
    asyncio.run(weather())
