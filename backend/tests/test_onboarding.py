from mavo.database import ModelRoute, WorkspaceSetting


def bind(client, uid="first", project="test-project"):
    with client.app.state.crew_runtime.factory() as session:
        owner = session.get(WorkspaceSetting, "workspace-owner")
        if owner:
            owner.value = {"uid": uid, "project_id": project}
        else:
            session.add(
                WorkspaceSetting(key="workspace-owner", value={"uid": uid, "project_id": project})
            )
        session.commit()


def advance(client, auth, action, revision, expected=200):
    response = client.put(
        "/api/v1/onboarding", headers=auth, json={"action": action, "revision": revision}
    )
    assert response.status_code == expected, response.text
    return response.json()


def test_requires_authentication_and_a_bound_account(client, auth):
    assert client.get("/api/v1/onboarding").status_code == 401
    assert client.get("/api/v1/onboarding", headers=auth).status_code == 401


def test_ordered_setup_explicit_skip_and_no_implicit_access(client, auth):
    bind(client)
    initial = client.get("/api/v1/onboarding", headers=auth).json()
    assert initial["step"] == "welcome"
    assert initial["revision"] == 0
    assert advance(client, auth, "next", 0)["step"] == "providers"
    advance(client, auth, "next", 1, expected=409)
    assert advance(client, auth, "skip", 1)["step"] == "resources"
    complete = advance(client, auth, "next", 2)
    assert complete["step"] == "complete"
    assert complete["completed_at"]
    assert complete["providers_skipped"]
    assert client.get("/api/v1/onboarding", headers=auth).json() == complete
    assert client.get("/api/v1/routes", headers=auth).json() == []
    assert client.get("/api/v1/crew", headers=auth).json() == []
    assert client.get("/api/v1/resources/access", headers=auth).json() == {
        "weather": False,
        "location": False,
    }
    advance(client, auth, "back", 3, expected=409)


def test_resume_back_revision_fencing_and_server_selected_identity(client, auth):
    bind(client)
    first = advance(client, auth, "next", 0)
    advance(client, auth, "next", 0, expected=409)
    assert advance(client, auth, "back", 1)["step"] == "welcome"
    response = client.put(
        "/api/v1/onboarding",
        headers=auth,
        json={"action": "next", "revision": 2, "uid": "someone-else"},
    )
    assert response.status_code == 422
    bind(client, uid="second")
    second = client.get("/api/v1/onboarding", headers=auth).json()
    assert second["step"] == "welcome"
    assert second["account_key"] != first["account_key"]
    bind(client)
    resumed = client.get("/api/v1/onboarding", headers=auth).json()
    assert resumed["revision"] == 2
    assert resumed["account_key"] == first["account_key"]
    bind(client, project="different-project")
    assert client.get("/api/v1/onboarding", headers=auth).json()["revision"] == 0


def test_saved_local_route_allows_continue_without_inference(client, auth):
    bind(client)
    advance(client, auth, "next", 0)
    response = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Local", "provider": "ollama", "model": "qwen-test"},
    )
    assert response.status_code == 201
    assert advance(client, auth, "next", 1)["step"] == "resources"
    assert not client.get("/api/v1/onboarding", headers=auth).json()["providers_skipped"]


def test_invalid_actions_do_not_move_progress(client, auth):
    bind(client)
    advance(client, auth, "skip", 0, expected=400)
    advance(client, auth, "back", 0, expected=400)
    for body in (
        {"action": "next", "revision": -1},
        {"action": "next", "revision": "0"},
        {"action": "complete", "revision": 0},
    ):
        assert client.put("/api/v1/onboarding", headers=auth, json=body).status_code == 422
    assert client.get("/api/v1/onboarding", headers=auth).json()["revision"] == 0


def test_a_cloud_route_without_its_credential_is_not_ready(client, auth):
    bind(client)
    advance(client, auth, "next", 0)
    with client.app.state.crew_runtime.factory() as session:
        session.add(
            ModelRoute(
                name="Missing key",
                provider="openai",
                model="test-model",
                credential_id="missing-credential",
            )
        )
        session.commit()
    advance(client, auth, "next", 1, expected=409)
