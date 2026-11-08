from __future__ import annotations

import os

import pytest

from mavo.credentials import DpapiCredentialStore


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is a Windows facility")
def test_dpapi_store_encrypts_round_trip_and_deletes(tmp_path) -> None:
    store = DpapiCredentialStore(tmp_path)
    secret = "sk-this-must-not-appear-on-disk"

    store.set("openai", secret)
    encrypted = (tmp_path / "credentials" / "openai.bin").read_bytes()

    assert secret.encode() not in encrypted
    assert store.get("openai") == secret
    store.delete("openai")
    assert store.get("openai") is None
