"""True engagements (calls/emails/meetings/notes/tasks) for a set of deals.

Fast path, no N+1: for each type, ONE batch association read per 100 deals
(deal -> engagement ids), then batch object reads (100 ids/call) for the
timestamps/metadata. Counts come free from the association lists.
"""
import asyncio
from typing import Any, Callable, Dict, Iterable, List, Optional

from .client import HubSpotClient

# plural object type -> (row label, properties to read)
ENGAGEMENT_TYPES: Dict[str, Dict[str, Any]] = {
    "meetings": {"label": "meeting", "props": ["hs_timestamp", "hs_meeting_title", "hs_meeting_outcome", "hs_meeting_start_time", "hubspot_owner_id", "hs_lastmodifieddate"]},
    "calls":    {"label": "call",    "props": ["hs_timestamp", "hs_call_title", "hs_call_direction", "hs_call_disposition", "hubspot_owner_id", "hs_lastmodifieddate"]},
    "emails":   {"label": "email",   "props": ["hs_timestamp", "hs_email_subject", "hs_email_direction", "hs_email_status", "hubspot_owner_id", "hs_lastmodifieddate"]},
    "notes":    {"label": "note",    "props": ["hs_timestamp", "hubspot_owner_id", "hs_lastmodifieddate"]},
    "tasks":    {"label": "task",    "props": ["hs_timestamp", "hs_task_subject", "hs_task_status", "hs_task_type", "hubspot_owner_id", "hs_lastmodifieddate"]},
}


def _chunks(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _direction(label: str, p: Dict[str, Any]) -> Optional[str]:
    if label == "email":
        d = (p.get("hs_email_direction") or "").upper()
        if "INCOMING" in d:
            return "in"
        return "out" if d else None
    if label == "call":
        d = (p.get("hs_call_direction") or "").upper()
        if d == "INBOUND":
            return "in"
        if d == "OUTBOUND":
            return "out"
    return None


def _status(label: str, p: Dict[str, Any]) -> Optional[str]:
    return {
        "meeting": p.get("hs_meeting_outcome"),
        "call": p.get("hs_call_disposition"),
        "email": p.get("hs_email_status"),
        "task": p.get("hs_task_status"),
    }.get(label)


def _subject(label: str, p: Dict[str, Any]) -> Optional[str]:
    return {
        "meeting": p.get("hs_meeting_title"),
        "call": p.get("hs_call_title"),
        "email": p.get("hs_email_subject"),
        "task": p.get("hs_task_subject"),
    }.get(label)


async def fetch_engagements_for_deals(
    client: HubSpotClient,
    deal_ids: Iterable[str],
    on_type_done: Optional[Callable[[str, int], None]] = None,
) -> List[Dict[str, Any]]:
    """Return per-(engagement, deal) rows ready to upsert into ``engagements``."""
    ids = [str(d) for d in deal_ids]
    rows: List[Dict[str, Any]] = []
    if not ids:
        return rows

    for plural, cfg in ENGAGEMENT_TYPES.items():
        label = cfg["label"]
        props = cfg["props"]

        # 1) associations: deal -> engagement ids (counts come free here)
        assoc_calls = [
            client.post(f"/crm/v4/associations/deals/{plural}/batch/read",
                        {"inputs": [{"id": d} for d in chunk]})
            for chunk in _chunks(ids, 100)
        ]
        eng_to_deals: Dict[str, List[str]] = {}
        for data in await asyncio.gather(*assoc_calls):
            for res in data.get("results", []):
                deal_from = str((res.get("from") or {}).get("id"))
                for to in res.get("to", []):
                    eid = str(to.get("toObjectId"))
                    eng_to_deals.setdefault(eid, []).append(deal_from)

        if not eng_to_deals:
            if on_type_done:
                on_type_done(label, 0)
            continue

        # 2) objects: timestamps + metadata
        all_ids = list(eng_to_deals.keys())
        obj_calls = [
            client.post(f"/crm/v3/objects/{plural}/batch/read",
                        {"properties": props, "inputs": [{"id": i} for i in chunk]})
            for chunk in _chunks(all_ids, 100)
        ]
        for data in await asyncio.gather(*obj_calls):
            for obj in data.get("results", []):
                eid = str(obj.get("id"))
                p = obj.get("properties", {}) or {}
                ts = p.get("hs_timestamp") or p.get("hs_meeting_start_time") or p.get("hs_lastmodifieddate")
                owner = p.get("hubspot_owner_id") or None
                is_automated = 1 if (label == "email" and not owner) else 0
                base = {
                    "raw_id": eid,
                    "type": label,
                    "ts": ts,
                    "last_modified": p.get("hs_lastmodifieddate"),
                    "owner_id": owner,
                    "direction": _direction(label, p),
                    "status": _status(label, p),
                    "subject": _subject(label, p),
                    "is_automated": is_automated,
                }
                for did in eng_to_deals.get(eid, []):
                    row = dict(base)
                    row["deal_id"] = did
                    rows.append(row)

        if on_type_done:
            on_type_done(label, len(eng_to_deals))

    return rows
