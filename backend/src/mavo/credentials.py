from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class CredentialError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, value: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class DpapiCredentialStore:
    """Stores provider keys encrypted for the current Windows user using DPAPI."""

    def __init__(self, data_dir: Path):
        if os.name != "nt":
            raise CredentialError("DPAPI credential storage is only available on Windows")
        self.directory = data_dir / "credentials"

    def _path(self, provider: str) -> Path:
        if not provider.isascii() or not provider.replace("-", "").isalnum():
            raise CredentialError("Invalid credential identifier")
        return self.directory / f"{provider}.bin"

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob, buffer

    @staticmethod
    def _protect(data: bytes) -> bytes:
        source, _buffer = DpapiCredentialStore._blob(data)
        result = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            "Mavo provider credential",
            None,
            None,
            None,
            0,
            ctypes.byref(result),
        ):
            raise CredentialError("Windows could not protect the credential")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        source, _buffer = DpapiCredentialStore._blob(data)
        result = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
        ):
            raise CredentialError("Windows could not unlock the credential")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)

    def get(self, provider: str) -> str | None:
        path = self._path(provider)
        if not path.exists():
            return None
        try:
            return self._unprotect(path.read_bytes()).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CredentialError("The saved credential could not be read") from exc

    def set(self, provider: str, value: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(provider)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(self._protect(value.encode("utf-8")))
        temporary.replace(path)

    def delete(self, provider: str) -> None:
        self._path(provider).unlink(missing_ok=True)


class MemoryCredentialStore:
    """Test-only credential store."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self.values.get(provider)

    def set(self, provider: str, value: str) -> None:
        self.values[provider] = value

    def delete(self, provider: str) -> None:
        self.values.pop(provider, None)
