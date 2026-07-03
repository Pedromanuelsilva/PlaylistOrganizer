from typing import Any
from urllib.parse import urlencode

import httpx

from app.models import Credential, Provider


CATEGORY_ACTIONS = {
    "live": "get_live_categories",
    "vod": "get_vod_categories",
    "series": "get_series_categories",
}


async def fetch_categories(
    client: httpx.AsyncClient,
    provider: Provider,
    credential: Credential,
) -> dict[str, Any]:
    """Fetch Live, VOD and Series category lists from the Xtream API.

    Returns a dict with keys ``live``, ``vod``, ``series`` (each a list of
    category dicts) and optionally ``error`` (a human-readable string).
    """
    base_params = {"username": credential.username, "password": credential.password}
    result: dict[str, Any] = {"live": [], "vod": [], "series": []}

    for key, action in CATEGORY_ACTIONS.items():
        params = {**base_params, "action": action}
        url = f"{provider.base_url}/player_api.php?{urlencode(params)}"
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            result["error"] = f"Request failed ({key}): {exc}"
            return result

        if response.status_code != 200:
            result["error"] = f"HTTP {response.status_code} when fetching {key} categories"
            return result

        try:
            payload = response.json()
        except ValueError:
            result["error"] = f"Non-JSON response for {key} categories"
            return result

        if isinstance(payload, list):
            result[key] = payload
        else:
            result[key] = []

    return result
