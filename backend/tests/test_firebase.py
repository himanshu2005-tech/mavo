import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mavo.firebase_auth import FirebaseIdentity, load_firebase_config
from mavo.google_errors import GoogleSignInError
from tests.test_google import google_env as google_fixture

google_env = google_fixture


def test_full_firebase_web_snippet_is_data_not_executable(tmp_path, monkeypatch):
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    file = tmp_path / "firebase.json"
    file.write_text(
        'const firebaseConfig = { apiKey: "ABCDEFGHIJKLMNOPQRSTUVWX", '
        'authDomain: "mavo-test.firebaseapp.com", projectId: "mavo-test", '
        'storageBucket: "mavo-test.appspot.com", messagingSenderId: "123", '
        'appId: "web-id", measurementId: "G-ID", };'
    )
    config = load_firebase_config()
    assert config["projectId"] == "mavo-test" and config["appId"] == "web-id"
    file.write_text(json.dumps({"type": "service_account", "private_key": "never-use-admin-key"}))
    with pytest.raises(ValueError, match="Admin SDK"):
        load_firebase_config()
    file.write_text("const firebaseConfig = executeCode();")
    with pytest.raises(ValueError):
        load_firebase_config()


@pytest.mark.asyncio
async def test_firebase_verified_login_keeps_connector_tokens_separate(google_env):
    auth, credentials, factory, _ = google_env
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-certificate")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    state = {"bad_audience": False, "requests": []}

    def handler(request):
        state["requests"].append(request)
        if "metadata/x509" in request.url.path:
            return httpx.Response(
                200, json={"firebase-key": cert.public_bytes(serialization.Encoding.PEM).decode()}
            )
        assert request.headers["X-Goog-Api-Key"] == "ABCDEFGHIJKLMNOPQRSTUVWX"
        assert "key=" not in str(request.url)
        token = jwt.encode(
            {
                "aud": "wrong-project" if state["bad_audience"] else "mavo-test",
                "iss": "https://securetoken.google.com/mavo-test",
                "sub": state.get("uid", "firebase-uid"),
                "email": "test@example.com",
                "email_verified": True,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "auth_time": int(time.time()),
                "firebase": {"sign_in_provider": "google.com"},
            },
            key,
            algorithm="RS256",
            headers={"kid": "firebase-key"},
        )
        return httpx.Response(
            200,
            json={
                "localId": state.get("uid", "firebase-uid"),
                "idToken": token,
                "refreshToken": "firebase-private-refresh",
                "expiresIn": "3600",
            },
        )

    identity = FirebaseIdentity(
        factory,
        credentials,
        auth.configuration(),
        httpx.MockTransport(handler),
        config={"apiKey": "ABCDEFGHIJKLMNOPQRSTUVWX", "projectId": "mavo-test"},
    )
    credentials.set("google-tokens", "keep-connector-credentials")
    identity.google.pending = {"phase": "exchanging"}
    await identity.exchange_google(
        "verified-google-token", {"sub": "google-user-1", "email": "test@example.com"}
    )
    assert identity.status()["signed_in"]
    assert credentials.get("google-tokens") == "keep-connector-credentials"

    before = credentials.get("firebase-tokens")
    state["uid"] = "another-account"
    with pytest.raises(GoogleSignInError) as rejected:
        await identity.exchange_google(
            "verified-google-token",
            {"sub": "google-user-1", "email": "test@example.com"},
            "identity-only-google-access",
        )
    assert rejected.value.code == "workspace_owner"
    assert credentials.get("firebase-tokens") == before
    state.pop("uid")
    assert "firebase-private-refresh" not in json.dumps(identity.status())
    before = credentials.get("firebase-tokens")
    state["bad_audience"] = True
    with pytest.raises(GoogleSignInError, match="Firebase login failed"):
        await identity.exchange_google(
            "verified-google-token", {"sub": "google-user-1", "email": "test@example.com"}
        )
    assert credentials.get("firebase-tokens") == before
    state["bad_audience"] = False
    identity.separate_project = True
    await identity.exchange_google(
        "verified-google-token",
        {"sub": "google-user-1", "email": "test@example.com"},
        "identity-only-google-access",
    )
    sent = json.loads(state["requests"][-1].content)
    assert "access_token=identity-only-google-access" in sent["postBody"]
    assert credentials.get("google-tokens") == "keep-connector-credentials"


def test_firebase_endpoints_require_local_auth(client):
    assert client.get("/api/v1/auth/firebase").status_code == 401
    assert client.post("/api/v1/auth/firebase/start").status_code == 401
