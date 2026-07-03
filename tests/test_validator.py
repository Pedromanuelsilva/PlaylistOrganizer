import httpx
import pytest

from app.models import Credential, CredentialStatus, Provider
from app.services.validator import validate_credential


def provider() -> Provider:
    return Provider(id=1, scheme="http", host="one.test", port=80, base_url="http://one.test:80")


def credential() -> Credential:
    return Credential(
        id=1,
        provider_id=1,
        username="alice",
        password="secret",
        source_url="http://one.test/get.php?username=alice&password=secret",
    )


@pytest.mark.asyncio
async def test_xtream_valid_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/player_api.php"
        return httpx.Response(
            200,
            json={
                "user_info": {
                    "auth": 1,
                    "status": "Active",
                    "exp_date": "1893456000",
                    "max_connections": "2",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await validate_credential(client, provider(), credential())

    assert outcome.status == CredentialStatus.VALID
    assert outcome.method == "xtream_api"
    assert outcome.expires_at is not None
    assert outcome.account_metadata["max_connections"] == "2"


@pytest.mark.asyncio
async def test_xtream_invalid_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_info": {"auth": 0, "status": "Disabled"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await validate_credential(client, provider(), credential())

    assert outcome.status == CredentialStatus.INVALID
    assert outcome.method == "xtream_api"


@pytest.mark.asyncio
async def test_playlist_fallback_success_after_non_json_api() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/player_api.php":
            return httpx.Response(200, text="not json")
        return httpx.Response(200, text="#EXTM3U\n#EXTINF:-1, Channel")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await validate_credential(client, provider(), credential())

    assert outcome.status == CredentialStatus.VALID
    assert outcome.method == "playlist_fetch"


@pytest.mark.asyncio
async def test_playlist_fallback_missing_playlist_is_invalid() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/player_api.php":
            return httpx.Response(503)
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await validate_credential(client, provider(), credential())

    assert outcome.status == CredentialStatus.INVALID
    assert outcome.method == "playlist_fetch"
