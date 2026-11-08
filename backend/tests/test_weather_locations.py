import httpx
import pytest

from mavo.resources.weather import OpenMeteoWeatherAdapter, WeatherResourceError


@pytest.mark.asyncio
async def test_hyderabad_is_not_silently_resolved_to_an_arbitrary_country():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "name": "Hyderabad",
                        "admin1": "Sindh",
                        "country": "Pakistan",
                        "country_code": "PK",
                        "latitude": 25.39,
                        "longitude": 68.37,
                    },
                    {
                        "id": 2,
                        "name": "Hyderabad",
                        "admin1": "Telangana",
                        "country": "India",
                        "country_code": "IN",
                        "latitude": 17.38,
                        "longitude": 78.46,
                    },
                ]
            },
        )

    adapter = OpenMeteoWeatherAdapter(httpx.MockTransport(handler))
    places = await adapter.search("Hyderabad")
    assert len(places) == 2 and places[1].region == "Telangana"
    with pytest.raises(WeatherResourceError, match="Several places"):
        await adapter.read("Hyderabad")
    assert all(r.url.host == "geocoding-api.open-meteo.com" for r in requests)
    selected = await adapter.search("Hyderabad, Telangana, India")
    assert len(selected) == 1 and selected[0].country_code == "IN"
    selected = await adapter.search("Hyderabad", "IN")
    assert len(selected) == 1 and selected[0].country_code == "IN"


@pytest.mark.asyncio
async def test_city_name_is_not_confused_with_its_airport():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "name": "Chennai International Airport",
                        "country": "India",
                        "admin1": "Tamil Nadu",
                        "country_code": "IN",
                        "latitude": 13,
                        "longitude": 80,
                    },
                    {
                        "id": 2,
                        "name": "Chennai",
                        "country": "India",
                        "country_code": "IN",
                        "admin1": "Tamil Nadu",
                        "latitude": 13,
                        "longitude": 80,
                    },
                ]
            },
        )

    adapter = OpenMeteoWeatherAdapter(httpx.MockTransport(handler))
    found = await adapter.search("Chennai, Tamil Nadu, India", "IN")
    assert len(found) == 1 and found[0].id == 2


def test_weather_coordinates_are_validated_and_saved_after_success(client, auth):
    client.put("/api/v1/resources/access", headers=auth, json={"weather": True})
    invalid = client.post(
        "/api/v1/resources/weather/forecast",
        headers=auth,
        json={"name": "Current location", "latitude": 100, "longitude": 78},
    )
    assert invalid.status_code == 422
    response = client.post(
        "/api/v1/resources/weather/forecast",
        headers=auth,
        json={
            "name": "Hyderabad",
            "region": "Telangana",
            "country": "India",
            "latitude": 17.38405,
            "longitude": 78.45636,
        },
    )
    assert response.status_code == 200
    assert response.json()["location"] == "Hyderabad, Telangana, India"
    saved = client.get("/api/v1/resources/weather/location", headers=auth).json()
    assert saved["name"] == "Hyderabad" and saved["latitude"] == 17.384
