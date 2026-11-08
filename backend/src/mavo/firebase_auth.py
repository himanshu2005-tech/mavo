"""Workspace identity using native Google PKCE, separate from connector grants."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt
from cryptography import x509
from fastapi import APIRouter, HTTPException

from mavo.database import GoogleAccount, WorkspaceSetting
from mavo.google_auth import GoogleAuth
from mavo.google_errors import GoogleSignInError

PUBLIC_FIELDS = {
    "apiKey",
    "authDomain",
    "projectId",
    "storageBucket",
    "messagingSenderId",
    "appId",
    "measurementId",
    "googleDesktopClientId",
}


def load_firebase_config():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / "firebase.json"
    if not path.exists():
        bundle = root / "publisher.json"
        return (
            json.loads(bundle.read_text(encoding="utf-8-sig")).get("firebase", {})
            if bundle.exists()
            else {}
        )
    raw = path.read_text(encoding="utf-8-sig")
    if len(raw) > 20000 or "private_key" in raw or "service_account" in raw:
        raise ValueError("Use Firebase's public Web app configuration, not an Admin SDK key")
    match = re.search(r"(?:const|let|var)\s+firebaseConfig\s*=\s*(\{.*?\})\s*;?", raw, re.S)
    raw = match.group(1) if match else raw.strip()
    raw = re.sub(r"([,{]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', raw)
    raw = re.sub(r",\s*}", "}", raw)
    data = json.loads(raw)
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError("Firebase configuration must contain string values")
    result = {key: value for key, value in data.items() if key in PUBLIC_FIELDS}
    if result.get("projectId") and not re.fullmatch(
        r"[a-z][a-z0-9-]{4,61}[a-z0-9]", result["projectId"]
    ):
        raise ValueError("Invalid Firebase projectId")
    if result.get("apiKey") and not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", result["apiKey"]):
        raise ValueError("Invalid Firebase apiKey")
    return result


class FirebaseIdentity:
    def __init__(self, factory, credentials, google_config, transport=None, config=None):
        self.factory, self.credentials, self.transport = factory, credentials, transport
        self.config_error = None
        try:
            self.config = load_firebase_config() if config is None else config
        except (ValueError, OSError):
            self.config = {}
            self.config_error = (
                "Check backend/firebase.json: paste the public Firebase Web configuration."
            )
        self.keys, self.keys_expiry = {}, 0
        self.session_error = None
        self.next_refresh_attempt = 0
        self.lock = asyncio.Lock()
        if self.config.get("googleDesktopClientId"):
            google_config = {**google_config, "client_id": self.config["googleDesktopClientId"]}
        self.separate_project = bool(
            self.config.get("messagingSenderId")
            and google_config.get("client_id", "").split("-")[0] != self.config["messagingSenderId"]
        )
        self.google = GoogleAuth(
            factory,
            credentials,
            transport,
            publisher_config=google_config,
            identity_callback=self.exchange_google,
        )

    def status(self):
        tokens = json.loads(self.credentials.get("firebase-tokens") or "{}")
        valid = tokens.get("project_id") == self.config.get("projectId") and bool(
            tokens.get("refresh_token")
        )
        return {
            "configured": bool(
                self.config.get("apiKey") and self.config.get("projectId") and not self.config_error
            ),
            "signed_in": valid and not self.session_error,
            "email": tokens.get("email") if valid else None,
            "name": tokens.get("name") if valid else None,
            "phase": self.google.pending["phase"] if self.google.pending else "idle",
            "error": self.config_error
            or self.session_error
            or (self.google.pending.get("error") if self.google.pending else None),
        }

    async def verify(self, token, uid):
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("Invalid Firebase identity")
        if self.keys_expiry < time.time() or header.get("kid") not in self.keys:
            async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                response = await client.get(
                    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
                )
                response.raise_for_status()
                self.keys = response.json()
                self.keys_expiry = time.time() + 3600
        key = x509.load_pem_x509_certificate(self.keys[header["kid"]].encode()).public_key()
        project = self.config["projectId"]
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=project,
            issuer="https://securetoken.google.com/" + project,
            leeway=60,
            options={"require": ["sub", "exp", "iat", "auth_time"]},
        )
        if (
            claims["sub"] != uid
            or not claims.get("email_verified")
            or claims["auth_time"] > time.time() + 60
            or claims.get("firebase", {}).get("sign_in_provider") != "google.com"
        ):
            raise ValueError("Firebase identity mismatch")
        return claims

    async def exchange_google(self, google_id_token, google_claims, google_access_token=None):
        if not self.status()["configured"]:
            raise GoogleSignInError("firebase_setup")
        with self.factory() as session:
            account = session.get(GoogleAccount, "google")
            owner = session.get(WorkspaceSetting, "workspace-owner")
            if not owner and account and account.subject != google_claims["sub"]:
                raise GoogleSignInError("account_mismatch")
        async with self.lock:
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                    response = await client.post(
                        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp",
                        headers={"X-Goog-Api-Key": self.config["apiKey"]},
                        json={
                            "requestUri": "http://localhost",
                            "returnSecureToken": True,
                            "postBody": urlencode(
                                {
                                    (
                                        "access_token"
                                        if self.separate_project and google_access_token
                                        else "id_token"
                                    ): google_access_token
                                    if self.separate_project and google_access_token
                                    else google_id_token,
                                    "providerId": "google.com",
                                }
                            ),
                        },
                    )
                    if response.is_error:
                        try:
                            code = (
                                response.json()
                                .get("error", {})
                                .get("message", "")
                                .split(" : ")[0]
                                .split(":")[0]
                            )
                        except (ValueError, AttributeError):
                            code = ""
                        if code in {"OPERATION_NOT_ALLOWED", "CONFIGURATION_NOT_FOUND"}:
                            raise GoogleSignInError("firebase_provider")
                        if code in {"INVALID_IDP_RESPONSE", "INVALID_OAUTH_CLIENT_ID"}:
                            raise GoogleSignInError("firebase_google_client")
                    response.raise_for_status()
                    result = response.json()
                claims = await self.verify(result["idToken"], result["localId"])
                if claims.get("email", "").casefold() != google_claims.get("email", "").casefold():
                    raise ValueError("Identity mismatch")
                with self.factory() as session:
                    owner = session.get(WorkspaceSetting, "workspace-owner")
                    binding = {"uid": claims["sub"], "project_id": self.config["projectId"]}
                    if owner and owner.value != binding:
                        raise GoogleSignInError("workspace_owner")
                    if not owner:
                        session.add(WorkspaceSetting(key="workspace-owner", value=binding))
                        session.commit()
                if not self.google.pending or self.google.pending["phase"] != "exchanging":
                    raise GoogleSignInError("cancelled")
                self.credentials.set(
                    "firebase-tokens",
                    json.dumps(
                        {
                            "id_token": result["idToken"],
                            "refresh_token": result["refreshToken"],
                            "expires_at": time.time() + int(result.get("expiresIn", 3600)),
                            "project_id": self.config["projectId"],
                            "uid": claims["sub"],
                            "email": claims.get("email"),
                            "name": claims.get("name"),
                        }
                    ),
                )
                self.session_error = None
            except (httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError):
                raise GoogleSignInError("firebase_login") from None

    async def refresh(self):
        async with self.lock:
            if time.monotonic() < self.next_refresh_attempt:
                return
            old = self.credentials.get("firebase-tokens")
            tokens = json.loads(old or "{}")
            if not tokens or tokens.get("expires_at", 0) > time.time() + 60:
                return
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                    response = await client.post(
                        "https://securetoken.googleapis.com/v1/token",
                        headers={"X-Goog-Api-Key": self.config["apiKey"]},
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": tokens["refresh_token"],
                        },
                    )
                    response.raise_for_status()
                    result = response.json()
                await self.verify(result["id_token"], tokens["uid"])
                if self.credentials.get("firebase-tokens") != old:
                    return
                tokens.update(
                    id_token=result["id_token"],
                    refresh_token=result["refresh_token"],
                    expires_at=time.time() + int(result.get("expires_in", 3600)),
                )
                self.credentials.set("firebase-tokens", json.dumps(tokens))
                self.session_error = None
            except (httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError):
                self.session_error = "Firebase session expired. Sign in again."
                self.next_refresh_attempt = time.monotonic() + 60


def build_firebase_api(identity):
    router = APIRouter()

    @router.get("/auth/firebase")
    async def status():
        await identity.refresh()
        return identity.status()

    @router.post("/auth/firebase/start")
    async def start():
        if not identity.status()["configured"]:
            raise HTTPException(
                409, "Add your Firebase Web configuration to backend/firebase.json first."
            )
        return await identity.google.start([])

    @router.post("/auth/firebase/cancel")
    async def cancel():
        await identity.google.cancel()
        return identity.status()

    @router.delete("/auth/firebase", status_code=204)
    async def sign_out():
        await identity.google.cancel()
        if getattr(identity, "on_sign_out", None):
            identity.on_sign_out()
        identity.credentials.delete("firebase-tokens")
        identity.session_error = None

    return router
