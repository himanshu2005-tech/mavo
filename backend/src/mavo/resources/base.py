from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T")


class ResourceAdapter(Protocol[T]):
    id: str

    async def read(self, query: str) -> T: ...
