from typing import get_args
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from mavo.coordinator_contract import PlannerContext
from mavo.crew_planner import CrewAccess
from mavo.google_permissions import (
    BASE,
    CAPABILITIES,
    GOOGLE_CAPABILITY_LIMIT,
    GoogleCapability,
)
from mavo.schemas import (
    AcceptCrewProposal,
    CrewCreateRequest,
    CrewDraft,
    CrewUpdateRequest,
    GoogleSignInRequest,
)


def test_all_catalog_capabilities_fit_every_google_request_contract():
    capabilities = list(CAPABILITIES)
    assert GOOGLE_CAPABILITY_LIMIT == len(get_args(GoogleCapability)) == len(capabilities)
    assert set(get_args(GoogleCapability)) == set(capabilities)
    assert GoogleSignInRequest(capabilities=capabilities).capabilities == capabilities
    assert CrewAccess(capabilities=capabilities).capabilities == capabilities
    assert (
        CrewCreateRequest(
            template_id="research",
            name="Google review",
            responsibilities="Review authorized sources",
            google_capabilities=capabilities,
        ).google_capabilities
        == capabilities
    )
    assert CrewUpdateRequest(google_capabilities=capabilities).google_capabilities == capabilities
    assert set(
        CrewDraft(
            template_id="research",
            name="Google review",
            responsibilities="Review authorized sources",
            requested_google_capabilities=capabilities,
        ).requested_google_capabilities
    ) == set(capabilities)
    assert AcceptCrewProposal(approved_google_capabilities=capabilities)
    context = PlannerContext(
        local_time="now", connected_capabilities=capabilities, denied_resources=capabilities
    )
    assert context.connected_capabilities == context.denied_resources == capabilities


def test_unknown_or_oversized_capability_lists_are_still_rejected():
    with pytest.raises(ValidationError):
        GoogleSignInRequest(capabilities=["drive:write"])
    with pytest.raises(ValidationError):
        GoogleSignInRequest(capabilities=["gmail:read"] * (GOOGLE_CAPABILITY_LIMIT + 1))


def test_connect_all_eleven_capabilities_builds_consent_without_granting_access(client, auth):
    configured = client.put(
        "/api/v1/auth/google/client",
        headers=auth,
        json={"client_id": "123-desktop.apps.googleusercontent.com"},
    )
    assert configured.status_code == 200
    response = client.post(
        "/api/v1/auth/google/start", headers=auth, json={"capabilities": list(CAPABILITIES)}
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert {BASE + entry[2] for entry in CAPABILITIES.values()} <= set(query["scope"][0].split())
    status = client.get("/api/v1/auth/google", headers=auth).json()
    assert not status["signed_in"]
    assert status["capabilities"] == []
    assert client.post("/api/v1/auth/google/cancel", headers=auth).status_code == 200
    assert client.get("/api/v1/crew", headers=auth).json() == []
