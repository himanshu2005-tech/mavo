from mavo.database import WorkspaceSetting


def test_defaults_deny_weather_and_saved_location_until_explicitly_allowed(client, auth):
    assert client.get("/api/v1/resources/access", headers=auth).json() == {
        "weather": False,
        "location": False,
    }
    for path in (
        "weather?location=Hyderabad",
        "weather/locations?query=Hyderabad",
        "weather/location",
    ):
        assert client.get("/api/v1/resources/" + path, headers=auth).status_code == 403
    assert (
        client.post(
            "/api/v1/resources/weather/forecast",
            headers=auth,
            json={"name": "Hyderabad", "latitude": 17.385, "longitude": 78.486},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/resources/access").status_code == 401


def test_manual_weather_works_without_device_location_and_revocation_blocks_cache(client, auth):
    assert (
        client.put(
            "/api/v1/resources/access", headers=auth, json={"weather": True, "location": False}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/resources/weather?location=New%20Delhi", headers=auth).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/resources/weather/forecast",
            headers=auth,
            json={
                "name": "Current location",
                "source": "device",
                "latitude": 17.385,
                "longitude": 78.486,
            },
        ).status_code
        == 403
    )
    client.put("/api/v1/resources/access", headers=auth, json={"weather": False})
    assert (
        client.get("/api/v1/resources/weather?location=New%20Delhi", headers=auth).status_code
        == 403
    )


def test_location_revocation_removes_device_coordinates_and_access_survives_sessions(client, auth):
    client.put("/api/v1/resources/access", headers=auth, json={"weather": True, "location": True})
    response = client.post(
        "/api/v1/resources/weather/forecast",
        headers=auth,
        json={
            "name": "Current location",
            "source": "device",
            "latitude": 17.385,
            "longitude": 78.486,
        },
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/resources/weather/location", headers=auth).json()["source"] == "device"
    )
    client.put("/api/v1/resources/access", headers=auth, json={"weather": True, "location": False})
    with client.app.state.crew_runtime.factory() as session:
        assert session.get(WorkspaceSetting, "weather-location") is None
        assert session.get(WorkspaceSetting, "default-resource-access").value == {
            "weather": True,
            "location": False,
        }
    assert client.get("/api/v1/resources/weather/location", headers=auth).json() is None


def test_access_rejects_unknown_permissions_and_implicit_boolean_coercion(client, auth):
    for payload in ({"terminal": True}, {"weather": "yes"}):
        assert client.put("/api/v1/resources/access", headers=auth, json=payload).status_code == 422


def test_detected_location_has_its_own_permission_and_is_removed_when_revoked(client, auth):
    coordinates = {
        "name": "Device",
        "latitude": 17.385044,
        "longitude": 78.486671,
        "accuracy_m": 100,
    }
    assert client.get("/api/v1/resources/location", headers=auth).status_code == 403
    assert (
        client.put("/api/v1/resources/location", headers=auth, json=coordinates).status_code == 403
    )
    client.put("/api/v1/resources/access", headers=auth, json={"location": True})
    saved = client.put("/api/v1/resources/location", headers=auth, json=coordinates)
    assert saved.status_code == 200
    assert saved.json()["source"] == "device" and saved.json()["latitude"] == 17.385
    assert saved.json()["longitude"] == 78.487 and saved.json()["accuracy_m"] == 100
    assert client.get("/api/v1/resources/location", headers=auth).json() == saved.json()
    # Location permission alone does not enable weather or contact its provider.
    assert (
        client.post(
            "/api/v1/resources/weather/forecast", headers=auth, json=saved.json()
        ).status_code
        == 403
    )
    client.put("/api/v1/resources/access", headers=auth, json={"location": False})
    assert client.get("/api/v1/resources/location", headers=auth).status_code == 403
    with client.app.state.crew_runtime.factory() as session:
        assert session.get(WorkspaceSetting, "device-location") is None
