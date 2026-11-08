from mavo.database import CrewInstance


def test_edit_and_run_require_exact_read_grant_for_resource_trigger(client, auth):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Scoped local",
            "provider": "ollama",
            "model": "test",
        },
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Mail watcher",
            "template_id": "communication",
            "route_id": route["id"],
            "responsibilities": "Review new mail",
            "trigger_type": "gmail_message",
            "google_capabilities": ["gmail:read"],
        },
    ).json()
    response = client.patch(
        f"/api/v1/crew/{crew['id']}",
        headers=auth,
        json={
            "google_capabilities": ["gmail:send"],
        },
    )
    assert response.status_code == 400
    assert "read permission" in response.json()["detail"]
    factory = client.app.state.crew_runtime.factory
    with factory() as session:
        row = session.get(CrewInstance, crew["id"])
        row.permissions = ["model:invoke", "google:gmail:send"]
        session.commit()
    response = client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    assert response.status_code == 400
    assert "read permission" in response.json()["detail"]
