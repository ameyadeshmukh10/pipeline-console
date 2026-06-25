"""Deal + owner fetching. The deal search requests all 16 hs_v2 stage props
inline so stage timelines need zero per-deal calls.
"""
from typing import Any, Callable, Dict, List, Optional

from ..constants import DEAL_ALL_PROPS
from .client import HubSpotClient

SEARCH_PAGE = 100  # search API page size


async def fetch_deals(
    client: HubSpotClient,
    pipeline_id: str,
    on_page: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """All deals in the given pipeline, with flat + stage-history properties."""
    results: List[Dict[str, Any]] = []
    after: Optional[str] = None
    total = 0
    while True:
        payload: Dict[str, Any] = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
            ]}],
            "properties": DEAL_ALL_PROPS,
            "limit": SEARCH_PAGE,
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
        }
        if after:
            payload["after"] = after
        data = await client.post("/crm/v3/objects/deals/search", payload)
        total = data.get("total", total)
        page = data.get("results", [])
        results.extend(page)
        if on_page:
            on_page(len(results), total or len(results))
        after = (data.get("paging", {}) or {}).get("next", {}).get("after")
        if not after:
            break
    return results


async def fetch_owners(client: HubSpotClient) -> List[Dict[str, Any]]:
    owners: List[Dict[str, Any]] = []
    after: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"limit": 100}
        if after:
            params["after"] = after
        data = await client.get("/crm/v3/owners", params=params)
        owners.extend(data.get("results", []))
        after = (data.get("paging", {}) or {}).get("next", {}).get("after")
        if not after:
            break
    return owners
