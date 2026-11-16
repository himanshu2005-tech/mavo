"""Failure diagnosis across the publisher -> desktop boundary; no live model calls."""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from mavo.coordinator_client import CoordinatorClient, CoordinatorUnavailable
from mavo.coordinator_contract import GatewayTurn, PlannerContext
from mavo.core_errors import problem
from mavo.credentials import MemoryCredentialStore
from mavo.publisher_gateway import GatewaySettings, create_gateway
from tests.test_publisher_gateway import Identity, payload


def model_gateway(tmp_path, handler):
    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        api_key="private-test-key",
    )
    return TestClient(create_gateway(settings, httpx.MockTransport(handler), Identity()))


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "core_provider_request"),
        (401, "core_provider_key"),
        (403, "core_provider_key"),
        (402, "core_provider_credits"),
        (404, "core_model_unavailable"),
        (429, "core_provider_rate_limit"),
        (500, "core_provider_unavailable"),
    ],
)
def test_provider_failures_are_explicit_redacted_and_durable(tmp_path, status, code):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private-test-key and private user information"}}
        )

    request = payload()
    with model_gateway(tmp_path, provider) as client:
        response = client.post(
            "/v1/core/turn", json=request, headers={"Authorization": "Bearer account-a"}
        )
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == request["request_id"]
        assert "private-test-key" not in response.text
        assert "private user information" not in response.text
        repeat = client.post(
            "/v1/core/turn", json=request, headers={"Authorization": "Bearer account-a"}
        )
        assert repeat.status_code == 409 and repeat.json()["error"]["code"] == code
    assert len(calls) == 1
    with sqlite3.connect(tmp_path / "gateway.db") as db:
        state, raw = db.execute("SELECT state,response FROM turns").fetchone()
        assert state == "failed" and json.loads(raw)["error"]["code"] == code


@pytest.mark.parametrize(
    "content,reason,code",
    [
        (None, "stop", "core_empty_response"),
        ("", "stop", "core_empty_response"),
        ("{", "length", "core_truncated_response"),
    ],
)
def test_empty_and_truncated_answers_do_not_trigger_a_second_bill(tmp_path, content, reason, code):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": reason}]}
        )

    with model_gateway(tmp_path, provider) as client:
        result = client.post(
            "/v1/core/turn", json=payload(), headers={"Authorization": "Bearer account-a"}
        )
    assert result.json()["error"]["code"] == code
    assert len(calls) == 1


def desktop_client(handler):
    credentials = MemoryCredentialStore()
    credentials.set("firebase-tokens", json.dumps({"id_token": "private-account-token"}))
    firebase = SimpleNamespace(
        refresh=AsyncMock(), status=lambda: {"signed_in": True}, credentials=credentials
    )
    return CoordinatorClient(
        SimpleNamespace(core_gateway_url="https://gateway.example"),
        firebase,
        httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "core_sign_in_required"),
        (422, "core_protocol_mismatch"),
        (504, "core_timeout"),
        (502, "core_gateway_failed"),
    ],
)
async def test_old_gateways_get_honest_safe_status_diagnoses(status, code):
    client = desktop_client(
        lambda request: httpx.Response(
            status, json={"detail": "private-account-token arbitrary error"}
        )
    )
    turn = GatewayTurn(
        request_id=str(uuid4()),
        messages=[{"role": "user", "content": "Hello"}],
        context=PlannerContext(local_time="now"),
    )
    with pytest.raises(CoordinatorUnavailable) as failure:
        await client.complete(turn)
    assert failure.value.problem["code"] == code
    assert failure.value.problem["request_id"] == turn.request_id
    assert "private-account-token" not in str(failure.value)


@pytest.mark.asyncio
async def test_known_gateway_code_survives_desktop_but_untrusted_message_does_not():
    client = desktop_client(
        lambda request: httpx.Response(
            503,
            json={
                "error": {
                    **problem("core_provider_credits"),
                    "message": "private-account-token",
                    "action": "leak me",
                }
            },
        )
    )
    turn = GatewayTurn(
        request_id=str(uuid4()),
        messages=[{"role": "user", "content": "Hello"}],
        context=PlannerContext(local_time="now"),
    )
    with pytest.raises(CoordinatorUnavailable) as failure:
        await client.complete(turn)
    assert failure.value.problem["code"] == "core_provider_credits"
    assert "private-account-token" not in str(failure.value) and "leak me" not in str(failure.value)


def test_typed_problem_reaches_authenticated_local_api(client, auth):
    runtime = client.app.state.crew_runtime
    runtime.planner.coordinator = SimpleNamespace(
        complete=AsyncMock(side_effect=CoordinatorUnavailable(code="core_provider_credits"))
    )
    result = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "Hello", "request_id": str(uuid4())}
    )
    assert result.status_code == 503 and result.json()["error"]["code"] == "core_provider_credits"
    assert client.get("/api/v1/crew", headers=auth).json() == []


def test_selected_planning_route_failure_is_not_called_a_setup_conflict(client, auth):
    from mavo.model_router import ModelProviderError

    runtime = client.app.state.crew_runtime
    runtime.planner.coordinator = SimpleNamespace(
        complete=AsyncMock(side_effect=ModelProviderError("private provider content"))
    )
    result = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "Hello", "request_id": str(uuid4())}
    )
    assert result.status_code == 503 and result.json()["error"]["code"] == "core_planning_route"
    assert "private provider content" not in result.text
