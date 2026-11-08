def test_manual_monitor_can_start_edit_and_revoke_notifications(client, auth):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Local", "provider": "ollama", "model": "unused"},
    ).json()
    payload = {
        "name": "Read-only drive",
        "template_id": "computer",
        "route_id": route["id"],
        "responsibilities": "Read drive counters",
        "allow_device_metrics": True,
        "allow_notifications": True,
        "device_monitor": {"kind": "storage", "drive": "C:", "notifications": "each_check"},
    }
    response = client.post("/api/v1/crew", headers=auth, json=payload)
    assert response.status_code == 201
    crew = response.json()
    assert "notifications:show" in crew["permissions"]
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 200
    change = client.patch(
        f"/api/v1/crew/{crew['id']}",
        headers=auth,
        json={
            "allow_notifications": False,
            "device_monitor": {
                "kind": "storage",
                "drive": "D:",
                "notifications": "alerts",
                "min_free_gb": 10,
            },
        },
    )
    assert change.status_code == 200
    assert "notifications:show" not in change.json()["permissions"]
    assert change.json()["configuration_plan"]["device_monitor"]["drive"] == "D:"
    payload.update(name="Wrong template", template_id="schedule", allow_device_metrics=False)
    assert client.post("/api/v1/crew", headers=auth, json=payload).status_code == 400
