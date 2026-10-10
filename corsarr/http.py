"""Small helpers around httpx clients shared by the setup assistant, the checks and the *arr code."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx


@asynccontextmanager
async def borrowed_client(client: httpx.AsyncClient | None, timeout: float = 15,
                          **kwargs: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Use the client passed in (tests hand in one with a mock transport), or make one for the duration
    of the block and close it afterwards."""
    if client is not None:
        yield client
        return
    own = httpx.AsyncClient(timeout=timeout, **kwargs)
    try:
        yield own
    finally:
        await own.aclose()


def arr_headers(api_key: str) -> dict[str, str]:
    """Sonarr/Radarr authenticate every request with this header."""
    return {"X-Api-Key": api_key}


def arr_api(url: str) -> str:
    """Base of the v3 API of a Sonarr/Radarr address as entered by the user."""
    return f"{url.rstrip('/')}/api/v3"
