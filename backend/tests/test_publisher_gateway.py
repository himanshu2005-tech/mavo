import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mavo.publisher_gateway import FirebaseVerifier, GatewaySettings, create_gateway


class Identity:
    async def verify(self, token):
        if token not in {"account-a", "account-b"}:
            raise HTTPException(401, "Invalid identity")
        return token


@pytest.fixture
def gateway(tmp_path):
    seen = []

    def provider(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer publisher-test-key"
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "low"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"reply": "Hello", "crew_proposal": None, "questions": []}
                            )
                        }
                    }
                ]
            },
        )

    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        api_key="publisher-test-key",
        user_daily_limit=2,
        global_daily_limit=3,
    )
    with TestClient(create_gateway(settings, httpx.MockTransport(provider), Identity())) as client:
        yield client, seen


def payload():
    return {
        "request_id": str(uuid4()),
        "messages": [{"role": "user", "content": "Hello"}],
        "context": {"local_time": "2026-11-01T22:00:00+05:30"},
    }


def test_upstream_timeout_is_explicit_and_does_not_repeat_paid_request(tmp_path):
    seen = []

    def provider(request):
        seen.append(request)
        raise httpx.ReadTimeout("Synthetic provider delay")

    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        api_key="publisher-test-key",
    )
    with TestClient(create_gateway(settings, httpx.MockTransport(provider), Identity())) as client:
        data = payload()
        response = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=data
        )
        assert response.status_code == 504 and "unchanged" in response.json()["detail"]
        repeated = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=data
        )
        assert repeated.status_code == 409 and len(seen) == 1


def test_all_authenticated_accounts_use_shared_minimax_key_and_cache_is_owner_bound(gateway):
    client, seen = gateway
    data = payload()
    first = client.post("/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=data)
    assert first.status_code == 200 and first.json()["model"] == "minimax/minimax-m3.1-flash"
    again = client.post("/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=data)
    assert again.json() == first.json() and len(seen) == 1
    other = client.post("/v1/core/turn", headers={"Authorization": "Bearer account-b"}, json=data)
    assert other.status_code == 409
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-b"}, json=payload()
        ).status_code
        == 200
    )
    sent = json.loads(seen[0].content)
    assert sent["model"] == "minimax/minimax-m3.1-flash" and sent["max_tokens"] == 4096
    assert "publisher-test-key" not in json.dumps(sent)
    assert "account-a" not in json.dumps(sent)
    assert "publisher-test-key" not in first.text


def test_missing_identity_custom_models_and_system_messages_are_rejected(gateway):
    client, seen = gateway
    assert client.post("/v1/core/turn", json=payload()).status_code == 401
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer invalid"}, json=payload()
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/core/turn",
            headers={"Authorization": "Bearer account-a"},
            json={**payload(), "model": "attacker-model"},
        ).status_code
        == 422
    )
    data = payload()
    data["messages"][0]["role"] = "system"
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=data
        ).status_code
        == 422
    )
    assert not seen


def test_per_account_and_global_quotas_bound_shared_credits(gateway):
    client, seen = gateway
    for _ in range(2):
        assert (
            client.post(
                "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
        ).status_code
        == 429
    )
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-b"}, json=payload()
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-b"}, json=payload()
        ).status_code
        == 429
    )
    assert len(seen) == 3


def test_with_no_limits_set_nobody_is_stopped(tmp_path):
    """Limits are off while Mavo is being built: 0 (the default) means no cap at all."""
    reply = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"reply": "Hi", "crew_proposal": None, "questions": []})
                }
            }
        ]
    }
    settings = GatewaySettings(
        _env_file=None, data_dir=tmp_path, firebase_project_id="test-project", api_key="k"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
    with TestClient(create_gateway(settings, transport, Identity())) as client:
        codes = {
            client.post(
                "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
            ).status_code
            for _ in range(12)  # more than the old 10 a minute
        }
    assert codes == {200}


@pytest.mark.asyncio
async def test_firebase_signatures_project_expiry_and_provider_are_verified():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "firebase-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(private, hashes.SHA256())
    )
    verifier = FirebaseVerifier(
        "test-project",
        httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"test": cert.public_bytes(serialization.Encoding.PEM).decode()}
            )
        ),
    )
    base = {
        "sub": "user-a",
        "aud": "test-project",
        "iss": "https://securetoken.google.com/test-project",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "auth_time": int(time.time()),
        "email_verified": True,
        "firebase": {"sign_in_provider": "google.com"},
    }

    def token(**changes):
        return jwt.encode({**base, **changes}, private, algorithm="RS256", headers={"kid": "test"})

    assert await verifier.verify(token()) == "user-a"
    assert await verifier.verify(token(sub="user-b")) == "user-b"
    for changes in (
        {"aud": "other-project"},
        {"exp": int(time.time()) - 120},
        {"firebase": {"sign_in_provider": "password"}},
        {"email_verified": False},
    ):
        with pytest.raises(HTTPException) as error:
            await verifier.verify(token(**changes))
        assert error.value.status_code == 401


def serving(tmp_path, replies):
    """A gateway whose model gives these replies in turn: the client and what it was sent."""
    sent = []

    def provider(request):
        body = json.loads(request.content)
        sent.append((request.headers["Idempotency-Key"], body["messages"]))
        reply = replies[min(len(sent) - 1, len(replies) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        api_key="publisher-test-key",
        user_daily_limit=5,
        global_daily_limit=9,
    )
    return TestClient(create_gateway(settings, httpx.MockTransport(provider), Identity())), sent


def test_an_invalid_reply_gets_one_correction_and_the_person_sees_only_the_good_one(tmp_path):
    bad = json.dumps({"reply": "On it", "task": {"title": "x"}})  # a task without steps
    good = json.dumps(
        {
            "reply": "On it.",
            "task": {
                "title": "Form",
                "deliver": "The link",
                "steps": [
                    {
                        "id": "make",
                        "kind": "agent",
                        "title": "Make it",
                        "instructions": "Create the form",
                        "actions": ["browser.open"],
                        "sites": ["forms.office.com"],
                    }
                ],
            },
        }
    )
    client, sent = serving(tmp_path, [bad, good])
    with client:
        answer = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
        )
    assert (
        answer.status_code == 200 and answer.json()["decision"]["task"]["steps"][0]["id"] == "make"
    )
    assert len(sent) == 2
    # The correction names what was wrong, without echoing the reply, and is a fresh paid request.
    assert sent[0][0] != sent[1][0]
    repair = sent[1][1][-1]["content"]
    assert (
        repair.startswith("That reply was not valid")
        and "task.steps" in repair
        and "ONLY the corrected JSON" in repair
    )
    assert sent[1][1][-2]["role"] == "assistant"


def test_a_reply_that_stays_invalid_is_refused_after_one_correction(tmp_path):
    client, sent = serving(tmp_path, ["not json at all", "still not json"])
    with client:
        answer = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
        )
    assert answer.status_code == 502 and "valid response" in answer.text and len(sent) == 2
